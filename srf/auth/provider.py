from __future__ import annotations

import logging
import os

from azure.core.credentials import TokenCredential
from azure.identity import ClientSecretCredential, DefaultAzureCredential

from srf.config.models import MainConfig
from srf.keyvault.client import KeyVaultClient

_ENV_VAR = "SRF_MASTER_CLIENT_SECRET"
logger = logging.getLogger(__name__)


class AuthProvider:
    """Resolve the credential used for all Graph API and Key Vault calls.

    Three modes are supported (evaluated in priority order):

    **Mode 1 — GitHub Secret / env var** (GitHub Actions recommended)
        Set ``SRF_MASTER_CLIENT_SECRET`` to the master SP client secret.
        No Key Vault or Azure login required.
        Needs ``tenant_id`` + ``master_client_id`` in the YAML config.

    **Mode 2 — OIDC / DefaultAzureCredential** (zero-secrets, GitHub Actions gold standard)
        Do *not* set ``SRF_MASTER_CLIENT_SECRET`` and omit ``master_keyvault_id``.
        Azure credentials are resolved automatically by ``DefaultAzureCredential``
        (Workload Identity Federation, ``az login``, Managed Identity, etc.).
        The ``azure/login`` GitHub Action sets the required env vars for OIDC.
        ``master_client_id`` in YAML is optional — ``AZURE_CLIENT_ID`` env var is used instead.

    **Mode 3 — Key Vault bootstrap** (for environments with a pre-existing managed identity)
        Provide ``master_keyvault_id`` + ``master_secret_name`` in the YAML.
        ``DefaultAzureCredential`` reads the master SP secret from that Key Vault, then
        creates a ``ClientSecretCredential``.  Only use when the *host* already has an
        identity (Managed Identity, ``az login``) that can reach the Key Vault — otherwise
        you recreate the same bootstrap loop.
    """

    def __init__(self, main_config: MainConfig) -> None:
        self._config = main_config

    def get_master_credential(self) -> TokenCredential:
        # ------------------------------------------------------------------ #
        # Mode 1: explicit client secret from environment variable            #
        # ------------------------------------------------------------------ #
        secret = os.environ.get(_ENV_VAR)
        if secret:
            if not self._config.master_client_id:
                raise RuntimeError(
                    f"{_ENV_VAR!r} is set but 'master_client_id' is missing from the YAML config."
                )
            logger.info(
                "Auth mode 1: ClientSecretCredential via %s env var (client_id=%s)",
                _ENV_VAR,
                self._config.master_client_id,
            )
            return ClientSecretCredential(
                tenant_id=self._config.tenant_id,
                client_id=self._config.master_client_id,
                client_secret=secret,
            )

        # ------------------------------------------------------------------ #
        # Mode 3: Key Vault bootstrap (requires host identity for KV access)  #
        # ------------------------------------------------------------------ #
        if self._config.master_keyvault_id and self._config.master_secret_name:
            if not self._config.master_client_id:
                raise RuntimeError(
                    "Key Vault bootstrap requires 'master_client_id' in the YAML config."
                )
            logger.info(
                "Auth mode 3: Key Vault bootstrap (keyvault=%s, client_id=%s)",
                self._config.master_keyvault_id,
                self._config.master_client_id,
            )
            bootstrap_credential = DefaultAzureCredential()
            kv = KeyVaultClient(
                credential=bootstrap_credential,
                keyvault_id=self._config.master_keyvault_id,
            )
            kv_secret = kv.get_secret(self._config.master_secret_name)
            return ClientSecretCredential(
                tenant_id=self._config.tenant_id,
                client_id=self._config.master_client_id,
                client_secret=kv_secret,
            )

        # ------------------------------------------------------------------ #
        # Mode 2: DefaultAzureCredential directly (OIDC / Managed Identity)   #
        # ------------------------------------------------------------------ #
        # DefaultAzureCredential resolves credentials from the environment:
        #   - GitHub Actions: set by azure/login@v2 (OIDC workload identity)
        #   - Local: az login session
        #   - Azure compute: Managed Identity
        # No client secret is required; Azure handles token exchange.
        logger.info(
            "Auth mode 2: DefaultAzureCredential (OIDC / az login / Managed Identity)"
        )
        return DefaultAzureCredential()
