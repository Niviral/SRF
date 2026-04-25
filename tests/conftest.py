"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from srf.config.models import AppConfig, MainConfig, MailConfig, SecretConfig


TENANT_ID = "tenant-0000"
MASTER_CLIENT_ID = "master-0001"
MASTER_KV_ID = (
    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/master-kv"
)
MASTER_KV_SECRET = "master-sp-client-secret"

SP_KV_ID = (
    "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/sp-kv"
)


@pytest.fixture
def run_id_svc():
    return "run-id-svc-0001"


@pytest.fixture
def main_config():
    return MainConfig(
        tenant_id=TENANT_ID,
        master_client_id=MASTER_CLIENT_ID,
        master_keyvault_id=MASTER_KV_ID,
        master_secret_name=MASTER_KV_SECRET,
    )


@pytest.fixture
def mail_config():
    return MailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_password_keyvault_id=MASTER_KV_ID,
        smtp_password_secret_name="smtp-password",
        from_address="from@example.com",
        to_addresses=["ops@example.com", "dev@example.com"],
    )


@pytest.fixture
def secret_config_with_description():
    return SecretConfig(
        name="slave1",
        app_id="app-0001",
        keyvault_id=SP_KV_ID,
        secret_name="slave1-secret",
        keyvault_secret_description="My slave1 description",
    )


@pytest.fixture
def secret_config_no_description():
    return SecretConfig(
        name="slave2",
        app_id="app-0002",
        keyvault_id=SP_KV_ID,
        secret_name="slave2-secret",
    )


@pytest.fixture
def app_config(
    main_config,
    mail_config,
    secret_config_with_description,
    secret_config_no_description,
):
    return AppConfig(
        main=main_config,
        mail=mail_config,
        secrets=[secret_config_with_description, secret_config_no_description],
    )
