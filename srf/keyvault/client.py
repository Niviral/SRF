from __future__ import annotations

import re

from azure.keyvault.secrets import SecretClient


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
        self._client = SecretClient(vault_url=vault_uri, credential=credential)

    def get_secret(self, name: str) -> str:
        return self._client.get_secret(name).value  # type: ignore[return-value]

    def set_secret(self, name: str, value: str, description: str | None = None) -> None:
        self._client.set_secret(name, value, content_type=description)
