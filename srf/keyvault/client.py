from __future__ import annotations

import logging
import re

from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)


def parse_keyvault_uri(resource_id: str) -> str:
    """Extract the Key Vault URI from an ARM resource ID.

    Expected format:
    /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.KeyVault/vaults/{name}
    """
    match = re.search(r"/providers/Microsoft\.KeyVault/vaults/([^/]+)$", resource_id, re.IGNORECASE)
    if not match:
        raise ValueError(f"Cannot parse Key Vault name from resource ID: {resource_id!r}")
    vault_name = match.group(1)
    return f"https://{vault_name}.vault.azure.net"


class KeyVaultClient:
    def __init__(self, credential, keyvault_id: str) -> None:
        vault_uri = parse_keyvault_uri(keyvault_id)
        self._vault_name = vault_uri.split("//")[1].split(".")[0]
        self._client = SecretClient(vault_url=vault_uri, credential=credential)
        logger.debug("KeyVaultClient initialised for vault=%s", self._vault_name)

    def get_secret(self, name: str) -> str:
        logger.debug("get_secret vault=%s name=%s", self._vault_name, name)
        value = self._client.get_secret(name).value  # type: ignore[return-value]
        logger.info("get_secret OK vault=%s name=%s", self._vault_name, name)
        return value

    def set_secret(self, name: str, value: str, description: str | None = None) -> None:
        logger.debug("set_secret vault=%s name=%s", self._vault_name, name)
        self._client.set_secret(name, value, content_type=description)
        logger.info("set_secret OK vault=%s name=%s", self._vault_name, name)
