"""End-to-end tests for CLI flags --validate, --dry-run, and --no-mail.

All Azure SDK calls are monkeypatched; no subprocess, no real network traffic.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

import main  # root-level main.py

# ---------------------------------------------------------------------------
# YAML content helpers
# ---------------------------------------------------------------------------

_BASE_YAML = """\
main:
  tenant_id: "tenant-abc"
  master_client_id: "master-app-id"
  master_keyvault_id: "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/myvault"
  master_keyvault_secret_name: "master-secret"
  threshold_days: 7
  validity_days: 365
secrets:
  - name: "sp-test"
    app_id: "app-id-123"
    keyvault_id: "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/myvault"
    keyvault_secret_name: "sp-test-secret"
"""

# mail block using exact Pydantic field names from MailConfig
_MAIL_BLOCK = """\
mail:
  smtp_host: "smtp.example.com"
  smtp_port: 587
  smtp_user: "user@example.com"
  smtp_password_keyvault_id: "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/myvault"
  smtp_password_secret_name: "mail-pw"
  from_address: "a@b.com"
  to_addresses:
    - "c@d.com"
"""

_INVALID_YAML = """\
main:
  tenant_x: "bad"
  master_client_id: "master-app-id"
  master_keyvault_id: "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/myvault"
  master_keyvault_secret_name: "master-secret"
secrets:
  - name: "sp-test"
    app_id: "app-id-123"
    keyvault_id: "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/myvault"
    keyvault_secret_name: "sp-test-secret"
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_yaml(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_BASE_YAML)
    return p


@pytest.fixture
def yaml_with_mail(tmp_path):
    p = tmp_path / "config_mail.yaml"
    p.write_text(_BASE_YAML + _MAIL_BLOCK)
    return p


@pytest.fixture
def mock_azure(monkeypatch):
    """Patch every Azure SDK touch-point so tests never make real network calls.

    Returns a dict of tracked MagicMock objects for assertions:
      - "add_password_credential"
      - "set_secret"
      - "send_report"
    """
    # Credential expiring in 2 days — within the 7-day threshold, so rotation is needed.
    mock_cred = MagicMock()
    mock_cred.end_date_time = datetime.now(tz=timezone.utc) + timedelta(days=2)
    mock_cred.key_id = "old-key-id"

    mock_new_cred = MagicMock()
    mock_new_cred.secret_text = "new-secret"
    mock_new_cred.end_date_time = datetime.now(tz=timezone.utc) + timedelta(days=365)
    mock_new_cred.key_id = "new-key-id"

    add_password = MagicMock(return_value=mock_new_cred)
    set_secret = MagicMock(return_value=None)
    send_report = MagicMock(return_value=None)

    monkeypatch.setattr(
        "srf.auth.provider.AuthProvider.get_master_credential",
        lambda self: MagicMock(),
    )
    monkeypatch.setattr(
        "srf.graph.client.GraphClient.__init__",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        "srf.graph.client.GraphClient.list_password_credentials",
        lambda self, app_id: [mock_cred],
    )
    monkeypatch.setattr("srf.graph.client.GraphClient.add_password_credential", add_password)
    monkeypatch.setattr(
        "srf.graph.client.GraphClient.remove_password_credential",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        "srf.graph.client.GraphClient.list_owners",
        lambda self, app_id: [],
    )
    monkeypatch.setattr(
        "srf.graph.client.GraphClient.add_owner",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        "srf.keyvault.client.KeyVaultClient.__init__",
        lambda self, *a, **kw: None,
    )
    monkeypatch.setattr(
        "srf.keyvault.client.KeyVaultClient.get_secret",
        lambda self, *a, **kw: "master-secret-value",
    )
    monkeypatch.setattr("srf.keyvault.client.KeyVaultClient.set_secret", set_secret)
    monkeypatch.setattr("srf.reporting.mail.MailReporter.send_report", send_report)

    return {
        "add_password_credential": add_password,
        "set_secret": set_secret,
        "send_report": send_report,
    }


# ---------------------------------------------------------------------------
# Tests — --validate flag
# ---------------------------------------------------------------------------

def test_validate_valid_config(tmp_path, monkeypatch, capsys):
    p = tmp_path / "valid.yaml"
    p.write_text(_BASE_YAML)
    monkeypatch.setattr(sys, "argv", ["srf", "--config", str(p), "--validate"])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 0
    assert "✅" in capsys.readouterr().out


def test_validate_invalid_config(tmp_path, monkeypatch, capsys):
    p = tmp_path / "invalid.yaml"
    p.write_text(_INVALID_YAML)
    monkeypatch.setattr(sys, "argv", ["srf", "--config", str(p), "--validate"])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 1
    assert "❌" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tests — --dry-run flag
# ---------------------------------------------------------------------------

def test_dry_run_no_writes(minimal_yaml, mock_azure, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["srf", "--config", str(minimal_yaml), "--dry-run"])
    main.main()
    assert not mock_azure["add_password_credential"].called
    assert not mock_azure["set_secret"].called
    out = capsys.readouterr().out
    assert "WOULD ROTATE" in out or "WOULD CREATE" in out


def test_dry_run_output_contains_summary(minimal_yaml, mock_azure, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["srf", "--config", str(minimal_yaml), "--dry-run"])
    main.main()
    out = capsys.readouterr().out
    assert "~ WOULD" in out or "NO CHANGE" in out


# ---------------------------------------------------------------------------
# Tests — --no-mail flag
# ---------------------------------------------------------------------------

def test_no_mail_suppresses_send(yaml_with_mail, mock_azure, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["srf", "--config", str(yaml_with_mail), "--no-mail"])
    main.main()
    assert not mock_azure["send_report"].called


def test_mail_sent_without_no_mail(yaml_with_mail, mock_azure, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["srf", "--config", str(yaml_with_mail)])
    main.main()
    mock_azure["send_report"].assert_called_once()


# ---------------------------------------------------------------------------
# Tests — combined flags
# ---------------------------------------------------------------------------

def test_dry_run_and_no_mail_combined(yaml_with_mail, mock_azure, monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["srf", "--config", str(yaml_with_mail), "--dry-run", "--no-mail"],
    )
    main.main()
    assert not mock_azure["add_password_credential"].called
    assert not mock_azure["set_secret"].called
    assert not mock_azure["send_report"].called
