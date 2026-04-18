"""Tests for YAML loading and Pydantic config models."""
from __future__ import annotations

import textwrap

import pytest

from srf.config.models import AppConfig, load_config


MINIMAL_YAML = textwrap.dedent("""\
    main:
      tenant_id: tid
      master_client_id: cid
      master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
      master_keyvault_secret_name: sec
    secrets:
      - name: sp1
        app_id: app-001
        keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
        keyvault_secret_name: sp1-secret
""")

FULL_YAML = textwrap.dedent("""\
    main:
      tenant_id: tid
      master_client_id: cid
      master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
      master_keyvault_secret_name: sec
    mail:
      smtp_host: smtp.host
      smtp_port: 465
      smtp_user: u@host
      smtp_password_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
      smtp_password_secret_name: smtp-pass
      from_address: from@host
      to_addresses:
        - a@b.com
    secrets:
      - name: sp1
        app_id: app-001
        keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
        keyvault_secret_name: sp1-secret
        keyvault_secret_description: "A description"
""")


def test_load_minimal_yaml(tmp_path, monkeypatch):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(MINIMAL_YAML)

    cfg = load_config(str(cfg_file))

    assert isinstance(cfg, AppConfig)
    assert cfg.main.tenant_id == "tid"
    assert cfg.mail is None
    assert len(cfg.secrets) == 1
    assert cfg.secrets[0].keyvault_secret_description is None


def test_load_full_yaml(tmp_path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(FULL_YAML)

    cfg = load_config(str(cfg_file))

    assert cfg.mail is not None
    assert cfg.mail.smtp_port == 465
    assert cfg.mail.to_addresses == ["a@b.com"]
    assert cfg.secrets[0].keyvault_secret_description == "A description"


def test_missing_required_field_raises(tmp_path):
    bad = textwrap.dedent("""\
        main:
          tenant_id: tid
        secrets: []
    """)
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(bad)

    with pytest.raises(Exception):
        load_config(str(cfg_file))


def test_default_smtp_port(mail_config):
    from srf.config.models import MailConfig
    m = MailConfig(
        smtp_host="h",
        smtp_user="u",
        smtp_password_keyvault_id="/subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/k",
        smtp_password_secret_name="s",
        from_address="f@e",
        to_addresses=["t@e"],
    )
    assert m.smtp_port == 587
