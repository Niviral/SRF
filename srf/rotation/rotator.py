from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

from msgraph.generated.models.password_credential import PasswordCredential

from srf.config.models import SecretConfig
from srf.graph.client import GraphClient
from srf.keyvault.client import KeyVaultClient, parse_keyvault_uri

logger = logging.getLogger(__name__)


@dataclass
class RotationResult:
    name: str
    app_id: str
    rotated: bool
    error: Optional[str] = field(default=None)
    new_expiry: Optional[datetime] = field(default=None)
    current_expiry: Optional[datetime] = field(default=None)
    keyvault_name: Optional[str] = field(default=None)
    was_created: bool = field(default=False)
    dry_run: bool = field(default=False)
    rotation_needed: bool = field(default=False)
    cleanup_warnings: list[str] = field(default_factory=list)
    kv_secret_missing: bool = field(default=False)


def _vault_name_from_id(keyvault_id: str) -> str:
    """Extract the vault hostname-less name for display purposes."""
    uri = parse_keyvault_uri(keyvault_id)
    return uri.replace("https://", "").split(".")[0]


class SecretRotator:
    def __init__(
        self,
        graph_client: GraphClient,
        keyvault_client_factory,
        threshold_days: int = 7,
        validity_days: int = 365,
        dry_run: bool = False,
    ) -> None:
        """
        Args:
            graph_client: GraphClient instance authenticated as the master SP.
            keyvault_client_factory: Callable[[str], KeyVaultClient] — given a keyvault_id,
                returns a ready KeyVaultClient. Allows per-SP vaults.
            threshold_days: Rotate if expiry is within this many days (or already expired).
            validity_days: Validity period for newly created credentials.
            dry_run: If True, no writes are made; results reflect what would happen.
        """
        self._graph = graph_client
        self._kv_factory = keyvault_client_factory
        self._threshold = timedelta(days=threshold_days)
        self._validity_days = validity_days
        self._dry_run = dry_run

    # ------------------------------------------------------------------

    def needs_rotation(self, credentials: list[PasswordCredential], threshold: Optional[timedelta] = None) -> tuple[bool, Optional[datetime]]:
        """Return (needs_rotation, soonest_expiry) for the credential set."""
        effective_threshold = threshold if threshold is not None else self._threshold
        if not credentials:
            return True, None

        now = datetime.now(tz=timezone.utc)
        soonest: Optional[datetime] = None

        for cred in credentials:
            expiry = cred.end_date_time
            if expiry is None:
                # A credential with no expiry is treated as requiring rotation:
                # it cannot be tracked and may have been created without TTL controls.
                return True, None
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if soonest is None or expiry < soonest:
                soonest = expiry
            if expiry <= now + effective_threshold:
                return True, soonest

        return False, soonest

    # ------------------------------------------------------------------

    def rotate(self, secret_config: SecretConfig) -> RotationResult:
        vault_name = _vault_name_from_id(secret_config.keyvault_id)
        eff_threshold = (
            timedelta(days=secret_config.threshold_days)
            if secret_config.threshold_days is not None
            else self._threshold
        )
        eff_validity = (
            secret_config.validity_days
            if secret_config.validity_days is not None
            else self._validity_days
        )
        logger.info(
            "processing [%s] app_id=%s vault=%s threshold_days=%s validity_days=%s dry_run=%s",
            secret_config.name, secret_config.app_id, vault_name,
            int(eff_threshold.days), eff_validity, self._dry_run,
        )
        try:
            credentials = self._graph.list_password_credentials(secret_config.app_id)
            was_created = len(credentials) == 0
            should_rotate, current_expiry = self.needs_rotation(credentials, threshold=eff_threshold)
            kv_secret_missing = False
            logger.debug(
                "[%s] credentials=%d should_rotate=%s current_expiry=%s",
                secret_config.name, len(credentials), should_rotate, current_expiry,
            )

            if not should_rotate:
                logger.debug("[%s] checking KV secret existence before skipping", secret_config.name)
                kv = self._kv_factory(secret_config.keyvault_id)
                if not kv.secret_exists(secret_config.keyvault_secret_name):
                    logger.info("[%s] KV secret missing — forcing rotation despite valid SP credential", secret_config.name)
                    should_rotate = True
                    kv_secret_missing = True
                else:
                    kv_secret_missing = False

            if not should_rotate:
                logger.info("[%s] skipping — not expiring within threshold", secret_config.name)
                if self._dry_run:
                    return RotationResult(
                        name=secret_config.name,
                        app_id=secret_config.app_id,
                        rotated=False,
                        dry_run=True,
                        rotation_needed=False,
                        current_expiry=current_expiry,
                        keyvault_name=vault_name,
                    )
                return RotationResult(
                    name=secret_config.name,
                    app_id=secret_config.app_id,
                    rotated=False,
                    current_expiry=current_expiry,
                    keyvault_name=vault_name,
                )

            if self._dry_run:
                logger.info("[%s] dry-run: would rotate (was_created=%s kv_secret_missing=%s)", secret_config.name, was_created, kv_secret_missing)
                return RotationResult(
                    name=secret_config.name,
                    app_id=secret_config.app_id,
                    rotated=False,
                    dry_run=True,
                    rotation_needed=True,
                    was_created=was_created,
                    current_expiry=current_expiry,
                    keyvault_name=vault_name,
                    kv_secret_missing=kv_secret_missing,
                )

            logger.info("[%s] rotating secret (was_created=%s kv_secret_missing=%s)", secret_config.name, was_created, kv_secret_missing)
            new_cred = self._graph.add_password_credential(
                app_id=secret_config.app_id,
                display_name=f"rotated-by-srf",
                validity_days=eff_validity,
            )

            logger.debug("[%s] writing new secret to Key Vault", secret_config.name)
            kv = self._kv_factory(secret_config.keyvault_id)
            kv.set_secret(
                name=secret_config.keyvault_secret_name,
                value=new_cred.secret_text,  # type: ignore[arg-type]
                description=secret_config.keyvault_secret_description,
            )

            cleanup_warnings: list[str] = []
            for old_cred in credentials:
                if old_cred.key_id and old_cred.key_id != new_cred.key_id:
                    logger.debug("[%s] removing old credential key_id=%s", secret_config.name, old_cred.key_id)
                    try:
                        self._graph.remove_password_credential(
                            app_id=secret_config.app_id,
                            key_id=str(old_cred.key_id),
                        )
                    except Exception as exc:
                        cleanup_warnings.append(
                            f"Failed to remove old credential {old_cred.key_id}: "
                            f"{type(exc).__name__}"
                        )
                        logger.warning("[%s] cleanup failed for key_id=%s: %s", secret_config.name, old_cred.key_id, type(exc).__name__)

            logger.info("[%s] rotation complete new_expiry=%s", secret_config.name, new_cred.end_date_time)
            return RotationResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                rotated=True,
                new_expiry=new_cred.end_date_time,
                current_expiry=current_expiry,
                keyvault_name=vault_name,
                was_created=was_created,
                cleanup_warnings=cleanup_warnings,
                kv_secret_missing=kv_secret_missing,
            )

        except Exception as exc:
            # Use only the exception type — never str(exc) for operations that
            # handle secrets. Azure SDK exceptions can embed request bodies,
            # tokens, or the new secret value in their message text.
            logger.error("[%s] rotation failed: %s — check Azure logs for details", secret_config.name, type(exc).__name__)
            return RotationResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                rotated=False,
                error=f"{type(exc).__name__}: rotation failed — check Azure logs for details",
                keyvault_name=vault_name,
            )
