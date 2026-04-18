from __future__ import annotations

import os

from azure.identity import ClientSecretCredential, DefaultAzureCredential

from srf.config.models import MainConfig
from srf.keyvault.client import KeyVaultClient

_ENV_VAR = "SRF_MASTER_CLIENT_SECRET"


class AuthProvider:
    """Bootstrap authentication for the master service principal.

    Resolution order for the master SP client secret:
      1. ``SRF_MASTER_CLIENT_SECRET`` environment variable — skips Key Vault
         entirely; use this for CI/CD pipelines and local development.
      2. ``master_keyvault_id`` + ``master_keyvault_secret_name`` in YAML —
         reads the secret from Key Vault using ``DefaultAzureCredential``
         (Managed Identity, ``az login``, env vars, etc.).

    A ``RuntimeError`` is raised on startup if neither source is configured.
    """

    def __init__(self, main_config: MainConfig) -> None:
        self._config = main_config

    def get_master_credential(self) -> ClientSecretCredential:
        secret = os.environ.get(_ENV_VAR)

        if secret:
            # Env-var path — no Key Vault needed (CI/dev use case).
            return ClientSecretCredential(
                tenant_id=self._config.tenant_id,
                client_id=self._config.master_client_id,
                client_secret=secret,
            )

        if self._config.master_keyvault_id and self._config.master_keyvault_secret_name:
            # Production path — host identity → Key Vault → ClientSecretCredential.
            bootstrap_credential = DefaultAzureCredential()
            kv = KeyVaultClient(
                credential=bootstrap_credential,
                keyvault_id=self._config.master_keyvault_id,
            )
            secret = kv.get_secret(self._config.master_keyvault_secret_name)
            return ClientSecretCredential(
                tenant_id=self._config.tenant_id,
                client_id=self._config.master_client_id,
                client_secret=secret,
            )

        raise RuntimeError(
            f"Master SP secret not configured. "
            f"Set the {_ENV_VAR!r} environment variable, or provide "
            f"'master_keyvault_id' and 'master_keyvault_secret_name' in the YAML config."
        )
