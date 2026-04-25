"""Tests for KeyVaultClient and parse_keyvault_uri."""

from __future__ import annotations

import pytest

from srf.keyvault.client import KeyVaultClient, parse_keyvault_uri


# ---------------------------------------------------------------------------
# parse_keyvault_uri
# ---------------------------------------------------------------------------


def test_parse_keyvault_uri_standard():
    rid = "/subscriptions/sub-id/resourceGroups/my-rg/providers/Microsoft.KeyVault/vaults/my-vault"
    assert parse_keyvault_uri(rid) == "https://my-vault.vault.azure.net"


def test_parse_keyvault_uri_case_insensitive():
    rid = "/subscriptions/sub/resourceGroups/rg/providers/microsoft.keyvault/vaults/MyVault"
    assert parse_keyvault_uri(rid) == "https://MyVault.vault.azure.net"


def test_parse_keyvault_uri_invalid_raises():
    with pytest.raises(ValueError, match="Cannot parse Key Vault name"):
        parse_keyvault_uri("/invalid/resource/id")


# ---------------------------------------------------------------------------
# KeyVaultClient
# ---------------------------------------------------------------------------

KV_ID = (
    "/subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/test-vault"
)


def test_get_secret(monkeypatch):
    mock_value = "super-secret"

    class FakeSecret:
        value = mock_value

    class FakeSecretClient:
        def __init__(self, vault_url, credential):
            pass

        def get_secret(self, name):
            return FakeSecret()

    monkeypatch.setattr("srf.keyvault.client.SecretClient", FakeSecretClient)
    client = KeyVaultClient(credential=object(), keyvault_id=KV_ID)
    assert client.get_secret("my-secret") == mock_value


def test_set_secret_without_description(monkeypatch):
    calls = {}

    class FakeSecretClient:
        def __init__(self, vault_url, credential):
            pass

        def set_secret(self, name, value, content_type=None, expires_on=None):
            calls["name"] = name
            calls["value"] = value
            calls["content_type"] = content_type
            calls["expires_on"] = expires_on

    monkeypatch.setattr("srf.keyvault.client.SecretClient", FakeSecretClient)
    client = KeyVaultClient(credential=object(), keyvault_id=KV_ID)
    client.set_secret("my-key", "my-value")

    assert calls["name"] == "my-key"
    assert calls["value"] == "my-value"
    assert calls["content_type"] is None
    assert calls["expires_on"] is None


def test_set_secret_with_description(monkeypatch):
    calls = {}

    class FakeSecretClient:
        def __init__(self, vault_url, credential):
            pass

        def set_secret(self, name, value, content_type=None, expires_on=None):
            calls["content_type"] = content_type
            calls["expires_on"] = expires_on

    monkeypatch.setattr("srf.keyvault.client.SecretClient", FakeSecretClient)
    client = KeyVaultClient(credential=object(), keyvault_id=KV_ID)
    client.set_secret("k", "v", description="My description")

    assert calls["content_type"] == "My description"


def test_secret_exists_returns_true(monkeypatch):
    class FakeSecret:
        value = "v"

    class FakeSecretClient:
        def __init__(self, vault_url, credential):
            pass

        def get_secret(self, name):
            return FakeSecret()

    monkeypatch.setattr("srf.keyvault.client.SecretClient", FakeSecretClient)
    client = KeyVaultClient(credential=object(), keyvault_id=KV_ID)
    assert client.secret_exists("my-secret") is True


def test_secret_exists_returns_false_when_not_found(monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError

    class FakeSecretClient:
        def __init__(self, vault_url, credential):
            pass

        def get_secret(self, name):
            raise ResourceNotFoundError("not found")

    monkeypatch.setattr("srf.keyvault.client.SecretClient", FakeSecretClient)
    client = KeyVaultClient(credential=object(), keyvault_id=KV_ID)
    assert client.secret_exists("missing-secret") is False
