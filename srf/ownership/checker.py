from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Optional

from srf.config.models import SecretConfig
from srf.graph.client import GraphClient

logger = logging.getLogger(__name__)


@dataclass
class OwnershipResult:
    name: str
    app_id: str
    checked: bool  # False if required_owners was empty (skipped)
    owners_added: list[str] = field(default_factory=list)
    owners_already_present: list[str] = field(default_factory=list)
    error: Optional[str] = field(default=None)
    owners_would_add: list[str] = field(default_factory=list)
    dry_run: bool = field(default=False)
    warning: Optional[str] = field(default=None)


class OwnershipChecker:
    def __init__(
        self,
        graph_client: GraphClient,
        master_owners: list[str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self._graph = graph_client
        self._master_owners = master_owners or []
        self._dry_run = dry_run

    def _is_email(self, owner: str):
        if "@" in owner:
            return True
        else:
            return False

    def check_and_update(self, secret_config: SecretConfig) -> OwnershipResult:
        seen = set()
        effective_owners = []
        expected_owners = self._master_owners + secret_config.required_owners
        for uid in expected_owners:
            if self._is_email(uid):
                email_uid = self._graph.get_user_by_email(uid)
                if email_uid not in seen:
                    seen.add(email_uid)
                    effective_owners.append(email_uid)
            else:
                if uid not in seen:
                    seen.add(uid)
                    effective_owners.append(uid)

        if not effective_owners:
            logger.debug(
                "[%s] no required_owners configured — skipping ownership check",
                secret_config.name,
            )
            return OwnershipResult(
                name=secret_config.name, app_id=secret_config.app_id, checked=False
            )

        logger.info(
            "[%s] checking %d required owner(s)",
            secret_config.name,
            len(effective_owners),
        )
        try:
            current_owners = self._graph.list_owners(secret_config.app_id)
            current_set = set(current_owners)
            already_present = [uid for uid in effective_owners if uid in current_set]
            missing = [uid for uid in effective_owners if uid not in current_set]
            logger.debug(
                "[%s] owners already_present=%d missing=%d",
                secret_config.name,
                len(already_present),
                len(missing),
            )

            if self._dry_run:
                logger.info(
                    "[%s] dry-run: would add %d owner(s): %s",
                    secret_config.name,
                    len(missing),
                    missing,
                )
                return OwnershipResult(
                    name=secret_config.name,
                    app_id=secret_config.app_id,
                    checked=True,
                    dry_run=True,
                    owners_already_present=already_present,
                    owners_would_add=missing,
                )

            added = []
            for user_id in missing:
                logger.debug(
                    "[%s] adding owner user_id=%s", secret_config.name, user_id
                )
                self._graph.add_owner(secret_config.app_id, user_id)
                added.append(user_id)

            if added:
                logger.info(
                    "[%s] added %d owner(s): %s", secret_config.name, len(added), added
                )
            else:
                logger.info("[%s] all owners already present", secret_config.name)

            return OwnershipResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                checked=True,
                owners_added=added,
                owners_already_present=already_present,
            )
        except Exception as exc:
            logger.error(
                "[%s] ownership check failed: %s — check Azure logs for details",
                secret_config.name,
                type(exc).__name__,
            )
            return OwnershipResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                checked=True,
                error=f"{type(exc).__name__}: ownership check failed — check Azure logs for details",
            )
