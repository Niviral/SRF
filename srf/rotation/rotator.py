from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from msgraph.generated.models.password_credential import PasswordCredential

from srf.config.models import SecretConfig
from srf.graph.client import GraphClient
from srf.keyvault.client import KeyVaultClient, parse_keyvault_uri


@dataclass
class RotationResult:
    name: str
    app_id: str
    rotated: bool
    error: Optional[str] = field(default=None)
    new_expiry: Optional[datetime] = field(default=None)
    current_expiry: Optional[datetime] = field(default=None)
    keyvault_name: Optional[str] = field(default=None)


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
    ) -> None:
        """
        Args:
            graph_client: GraphClient instance authenticated as the master SP.
            keyvault_client_factory: Callable[[str], KeyVaultClient] — given a keyvault_id,
                returns a ready KeyVaultClient. Allows per-SP vaults.
            threshold_days: Rotate if expiry is within this many days (or already expired).
            validity_days: Validity period for newly created credentials.
        """
        self._graph = graph_client
        self._kv_factory = keyvault_client_factory
        self._threshold = timedelta(days=threshold_days)
        self._validity_days = validity_days

    # ------------------------------------------------------------------

    def needs_rotation(self, credentials: list[PasswordCredential]) -> tuple[bool, Optional[datetime]]:
        """Return (needs_rotation, soonest_expiry) for the credential set."""
        if not credentials:
            return True, None

        now = datetime.now(tz=timezone.utc)
        soonest: Optional[datetime] = None

        for cred in credentials:
            expiry = cred.end_date_time
            if expiry is None:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if soonest is None or expiry < soonest:
                soonest = expiry
            if expiry <= now + self._threshold:
                return True, soonest

        return False, soonest

    # ------------------------------------------------------------------

    def rotate(self, secret_config: SecretConfig) -> RotationResult:
        vault_name = _vault_name_from_id(secret_config.keyvault_id)
        try:
            credentials = self._graph.list_password_credentials(secret_config.app_id)
            should_rotate, current_expiry = self.needs_rotation(credentials)

            if not should_rotate:
                return RotationResult(
                    name=secret_config.name,
                    app_id=secret_config.app_id,
                    rotated=False,
                    current_expiry=current_expiry,
                    keyvault_name=vault_name,
                )

            new_cred = self._graph.add_password_credential(
                app_id=secret_config.app_id,
                display_name=f"rotated-by-srf",
                validity_days=self._validity_days,
            )

            kv = self._kv_factory(secret_config.keyvault_id)
            kv.set_secret(
                name=secret_config.keyvault_secret_name,
                value=new_cred.secret_text,  # type: ignore[arg-type]
                description=secret_config.keyvault_secret_description,
            )

            for old_cred in credentials:
                if old_cred.key_id and old_cred.key_id != new_cred.key_id:
                    try:
                        self._graph.remove_password_credential(
                            app_id=secret_config.app_id,
                            key_id=str(old_cred.key_id),
                        )
                    except Exception:
                        pass  # best-effort cleanup of old credentials

            return RotationResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                rotated=True,
                new_expiry=new_cred.end_date_time,
                current_expiry=current_expiry,
                keyvault_name=vault_name,
            )

        except Exception as exc:
            return RotationResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                rotated=False,
                error=str(exc),
                keyvault_name=vault_name,
            )
