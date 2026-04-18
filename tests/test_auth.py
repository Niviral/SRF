"""Unit tests for AuthProvider.

Covers all three resolution paths:
  1. SRF_MASTER_CLIENT_SECRET env var (no KV)
  2. Key Vault bootstrap (DefaultAzureCredential → KV → ClientSecretCredential)
  3. Neither configured → RuntimeError
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from srf.auth.provider import AuthProvider
from srf.config.models import MainConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> MainConfig:
    defaults = dict(
        tenant_id="tenant-123",
        master_client_id="client-456",
        master_keyvault_id=None,
        master_keyvault_secret_name=None,
    )
    defaults.update(overrides)
    return MainConfig(**defaults)


# ---------------------------------------------------------------------------
# Env var path
# ---------------------------------------------------------------------------

def test_env_var_path_returns_credential(monkeypatch):
    monkeypatch.setenv("SRF_MASTER_CLIENT_SECRET", "my-env-secret")
    config = _make_config()  # no KV fields

    with patch("srf.auth.provider.ClientSecretCredential") as mock_csc:
        mock_csc.return_value = MagicMock()
        provider = AuthProvider(config)
        cred = provider.get_master_credential()

    mock_csc.assert_called_once_with(
        tenant_id="tenant-123",
        client_id="client-456",
        client_secret="my-env-secret",
    )
    assert cred is mock_csc.return_value


def test_env_var_skips_keyvault(monkeypatch):
    """When env var is set, KeyVaultClient must never be instantiated."""
    monkeypatch.setenv("SRF_MASTER_CLIENT_SECRET", "secret-value")
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
        master_keyvault_secret_name="my-secret",
    )

    with patch("srf.auth.provider.ClientSecretCredential") as mock_csc, \
         patch("srf.auth.provider.KeyVaultClient") as mock_kv:
        mock_csc.return_value = MagicMock()
        AuthProvider(config).get_master_credential()

    mock_kv.assert_not_called()


def test_env_var_takes_precedence_over_kv(monkeypatch):
    """Env var wins even when KV fields are also present."""
    monkeypatch.setenv("SRF_MASTER_CLIENT_SECRET", "env-wins")
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
        master_keyvault_secret_name="kv-secret",
    )

    with patch("srf.auth.provider.ClientSecretCredential") as mock_csc, \
         patch("srf.auth.provider.KeyVaultClient"):
        mock_csc.return_value = MagicMock()
        AuthProvider(config).get_master_credential()

    # Secret passed to credential must be the env var value, not the KV value
    _, kwargs = mock_csc.call_args
    assert kwargs["client_secret"] == "env-wins"


# ---------------------------------------------------------------------------
# Key Vault path
# ---------------------------------------------------------------------------

def test_kv_path_reads_secret_and_returns_credential(monkeypatch):
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/my-kv",
        master_keyvault_secret_name="master-secret",
    )

    with patch("srf.auth.provider.DefaultAzureCredential") as mock_dac, \
         patch("srf.auth.provider.KeyVaultClient") as mock_kv_cls, \
         patch("srf.auth.provider.ClientSecretCredential") as mock_csc:
        mock_dac.return_value = MagicMock()
        mock_kv_instance = MagicMock()
        mock_kv_instance.get_secret.return_value = "kv-secret-value"
        mock_kv_cls.return_value = mock_kv_instance
        mock_csc.return_value = MagicMock()

        cred = AuthProvider(config).get_master_credential()

    mock_dac.assert_called_once()
    mock_kv_cls.assert_called_once()
    mock_kv_instance.get_secret.assert_called_once_with("master-secret")
    mock_csc.assert_called_once_with(
        tenant_id="tenant-123",
        client_id="client-456",
        client_secret="kv-secret-value",
    )
    assert cred is mock_csc.return_value


def test_kv_path_uses_correct_kv_id(monkeypatch):
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    kv_id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/my-kv"
    config = _make_config(
        master_keyvault_id=kv_id,
        master_keyvault_secret_name="s",
    )

    with patch("srf.auth.provider.DefaultAzureCredential"), \
         patch("srf.auth.provider.KeyVaultClient") as mock_kv_cls, \
         patch("srf.auth.provider.ClientSecretCredential"):
        mock_kv_cls.return_value = MagicMock()
        mock_kv_cls.return_value.get_secret.return_value = "x"
        AuthProvider(config).get_master_credential()

    _, kwargs = mock_kv_cls.call_args
    assert kwargs["keyvault_id"] == kv_id


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

def test_no_secret_source_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    config = _make_config()  # no KV fields, no env var

    with pytest.raises(RuntimeError, match="SRF_MASTER_CLIENT_SECRET"):
        AuthProvider(config).get_master_credential()


def test_partial_kv_config_raises_runtime_error(monkeypatch):
    """Only master_keyvault_id without secret_name is still missing config."""
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
        # master_keyvault_secret_name intentionally omitted
    )

    with pytest.raises(RuntimeError, match="SRF_MASTER_CLIENT_SECRET"):
        AuthProvider(config).get_master_credential()
