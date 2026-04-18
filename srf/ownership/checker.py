from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from srf.config.models import SecretConfig
from srf.graph.client import GraphClient


@dataclass
class OwnershipResult:
    name: str
    app_id: str
    checked: bool          # False if required_owners was empty (skipped)
    owners_added: list[str] = field(default_factory=list)
    owners_already_present: list[str] = field(default_factory=list)
    error: Optional[str] = field(default=None)
    owners_would_add: list[str] = field(default_factory=list)
    dry_run: bool = field(default=False)


class OwnershipChecker:
    def __init__(self, graph_client: GraphClient, master_owners: list[str] | None = None, dry_run: bool = False) -> None:
        self._graph = graph_client
        self._master_owners = master_owners or []
        self._dry_run = dry_run

    def check_and_update(self, secret_config: SecretConfig) -> OwnershipResult:
        seen = set()
        effective_owners = []
        for uid in self._master_owners + secret_config.required_owners:
            if uid not in seen:
                seen.add(uid)
                effective_owners.append(uid)

        if not effective_owners:
            return OwnershipResult(name=secret_config.name, app_id=secret_config.app_id, checked=False)

        try:
            current_owners = self._graph.list_owners(secret_config.app_id)
            current_set = set(current_owners)
            already_present = [uid for uid in effective_owners if uid in current_set]
            missing = [uid for uid in effective_owners if uid not in current_set]

            if self._dry_run:
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
                self._graph.add_owner(secret_config.app_id, user_id)
                added.append(user_id)
            return OwnershipResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                checked=True,
                owners_added=added,
                owners_already_present=already_present,
            )
        except Exception as exc:
            return OwnershipResult(
                name=secret_config.name,
                app_id=secret_config.app_id,
                checked=True,
                error=f"{type(exc).__name__}: ownership check failed — check Azure logs for details",
            )
