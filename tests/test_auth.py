"""Unit tests for AuthProvider.

Covers all three resolution modes:
  1. SRF_MASTER_CLIENT_SECRET env var → ClientSecretCredential (GitHub Secret path)
  2. Key Vault bootstrap → ClientSecretCredential (managed identity path)
  3. DefaultAzureCredential directly (OIDC / workload identity / az login path)
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from azure.identity import DefaultAzureCredential

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
        secret_name=None,
    )
    defaults.update(overrides)
    return MainConfig(**defaults)


# ---------------------------------------------------------------------------
# Mode 1: env var path
# ---------------------------------------------------------------------------


def test_env_var_path_returns_credential(monkeypatch):
    monkeypatch.setenv("SRF_MASTER_CLIENT_SECRET", "my-env-secret")
    config = _make_config()

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
        master_secret_name="my-secret",
    )

    with (
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
        patch("srf.auth.provider.KeyVaultClient") as mock_kv,
    ):
        mock_csc.return_value = MagicMock()
        AuthProvider(config).get_master_credential()

    mock_kv.assert_not_called()


def test_env_var_takes_precedence_over_kv(monkeypatch):
    """Env var wins even when KV fields are also present."""
    monkeypatch.setenv("SRF_MASTER_CLIENT_SECRET", "env-wins")
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
        master_secret_name="kv-secret",
    )

    with (
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
        patch("srf.auth.provider.KeyVaultClient"),
    ):
        mock_csc.return_value = MagicMock()
        AuthProvider(config).get_master_credential()

    _, kwargs = mock_csc.call_args
    assert kwargs["client_secret"] == "env-wins"


def test_env_var_without_master_client_id_raises(monkeypatch):
    """Env var set but master_client_id missing → clear error."""
    monkeypatch.setenv("SRF_MASTER_CLIENT_SECRET", "secret")
    config = _make_config(master_client_id=None)

    with pytest.raises(RuntimeError, match="master_client_id"):
        AuthProvider(config).get_master_credential()


# ---------------------------------------------------------------------------
# Mode 3: Key Vault bootstrap path
# ---------------------------------------------------------------------------


def test_kv_path_reads_secret_and_returns_credential(monkeypatch):
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/my-kv",
        master_secret_name="master-secret",
    )

    with (
        patch("srf.auth.provider.DefaultAzureCredential") as mock_dac,
        patch("srf.auth.provider.KeyVaultClient") as mock_kv_cls,
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
    ):
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
    kv_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/my-kv"
    )
    config = _make_config(
        master_keyvault_id=kv_id,
        master_secret_name="s",
    )

    with (
        patch("srf.auth.provider.DefaultAzureCredential"),
        patch("srf.auth.provider.KeyVaultClient") as mock_kv_cls,
        patch("srf.auth.provider.ClientSecretCredential"),
    ):
        mock_kv_cls.return_value = MagicMock()
        mock_kv_cls.return_value.get_secret.return_value = "x"
        AuthProvider(config).get_master_credential()

    _, kwargs = mock_kv_cls.call_args
    assert kwargs["keyvault_id"] == kv_id


def test_kv_path_without_master_client_id_raises(monkeypatch):
    """KV fields set but master_client_id missing → clear error."""
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    config = _make_config(
        master_client_id=None,
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
        master_secret_name="s",
    )

    with pytest.raises(RuntimeError, match="master_client_id"):
        AuthProvider(config).get_master_credential()


# ---------------------------------------------------------------------------
# Mode 2: DefaultAzureCredential direct path (OIDC / Workload Identity)
# ---------------------------------------------------------------------------


def test_oidc_path_returns_default_azure_credential(monkeypatch):
    """When neither env var nor KV fields are set, DefaultAzureCredential is returned."""
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    # Minimal config — no client_id needed for OIDC, AZURE_CLIENT_ID handles it
    config = MainConfig(tenant_id="tenant-123")

    with patch("srf.auth.provider.DefaultAzureCredential") as mock_dac:
        mock_dac.return_value = MagicMock(spec=DefaultAzureCredential)
        cred = AuthProvider(config).get_master_credential()

    mock_dac.assert_called_once_with()
    assert cred is mock_dac.return_value


def test_oidc_path_does_not_call_kv_or_csc(monkeypatch):
    """OIDC path must never touch KeyVaultClient or ClientSecretCredential."""
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    config = MainConfig(tenant_id="tenant-123")

    with (
        patch("srf.auth.provider.DefaultAzureCredential") as mock_dac,
        patch("srf.auth.provider.KeyVaultClient") as mock_kv,
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
    ):
        mock_dac.return_value = MagicMock()
        AuthProvider(config).get_master_credential()

    mock_kv.assert_not_called()
    mock_csc.assert_not_called()


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
        master_secret_name="my-secret",
    )

    with (
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
        patch("srf.auth.provider.KeyVaultClient") as mock_kv,
    ):
        mock_csc.return_value = MagicMock()
        AuthProvider(config).get_master_credential()

    mock_kv.assert_not_called()


def test_env_var_takes_precedence_over_kv(monkeypatch):
    """Env var wins even when KV fields are also present."""
    monkeypatch.setenv("SRF_MASTER_CLIENT_SECRET", "env-wins")
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
        master_secret_name="kv-secret",
    )

    with (
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
        patch("srf.auth.provider.KeyVaultClient"),
    ):
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
        master_secret_name="master-secret",
    )

    with (
        patch("srf.auth.provider.DefaultAzureCredential") as mock_dac,
        patch("srf.auth.provider.KeyVaultClient") as mock_kv_cls,
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
    ):
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
    kv_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/my-kv"
    )
    config = _make_config(
        master_keyvault_id=kv_id,
        master_secret_name="s",
    )

    with (
        patch("srf.auth.provider.DefaultAzureCredential"),
        patch("srf.auth.provider.KeyVaultClient") as mock_kv_cls,
        patch("srf.auth.provider.ClientSecretCredential"),
    ):
        mock_kv_cls.return_value = MagicMock()
        mock_kv_cls.return_value.get_secret.return_value = "x"
        AuthProvider(config).get_master_credential()

    _, kwargs = mock_kv_cls.call_args
    assert kwargs["keyvault_id"] == kv_id


# ---------------------------------------------------------------------------
# Partial config falls through to OIDC mode
# ---------------------------------------------------------------------------


def test_partial_kv_config_falls_through_to_oidc(monkeypatch):
    """Only master_keyvault_id without secret_name → KV fields incomplete →
    falls through to DefaultAzureCredential (OIDC mode)."""
    monkeypatch.delenv("SRF_MASTER_CLIENT_SECRET", raising=False)
    config = _make_config(
        master_keyvault_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv",
        # master_secret_name intentionally omitted
    )

    with (
        patch("srf.auth.provider.DefaultAzureCredential") as mock_dac,
        patch("srf.auth.provider.KeyVaultClient") as mock_kv,
        patch("srf.auth.provider.ClientSecretCredential") as mock_csc,
    ):
        mock_dac.return_value = MagicMock()
        AuthProvider(config).get_master_credential()

    mock_kv.assert_not_called()
    mock_csc.assert_not_called()
    mock_dac.assert_called_once()
