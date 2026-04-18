from __future__ import annotations

from azure.identity import ClientSecretCredential, DefaultAzureCredential

from srf.config.models import MainConfig
from srf.keyvault.client import KeyVaultClient


class AuthProvider:
    """Bootstrap authentication for the master service principal.

    Uses DefaultAzureCredential to read the master SP's client secret
    from the configured Key Vault, then returns a ClientSecretCredential
    scoped to that SP for Graph API calls.
    """

    def __init__(self, main_config: MainConfig) -> None:
        self._config = main_config

    def get_master_credential(self) -> ClientSecretCredential:
        bootstrap_credential = DefaultAzureCredential()
        kv = KeyVaultClient(credential=bootstrap_credential, keyvault_id=self._config.master_keyvault_id)
        master_secret = kv.get_secret(self._config.master_keyvault_secret_name)
        return ClientSecretCredential(
            tenant_id=self._config.tenant_id,
            client_id=self._config.master_client_id,
            client_secret=master_secret,
        )
