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
      master_secret_name: sec
    secrets:
      - name: sp1
        obj_id: app-001
        keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
        secret_name: sp1-secret
""")

FULL_YAML = textwrap.dedent("""\
    main:
      tenant_id: tid
      master_client_id: cid
      master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
      master_secret_name: sec
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
        obj_id: app-001
        keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
        secret_name: sp1-secret
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
    # tenant_id is still required — omitting it must raise a validation error
    bad = textwrap.dedent("""\
        main:
          master_client_id: mid
        secrets: []
    """)
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(bad)

    with pytest.raises(Exception):
        load_config(str(cfg_file))


def test_threshold_validity_defaults(tmp_path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(MINIMAL_YAML)

    cfg = load_config(str(cfg_file))

    assert cfg.main.threshold_days == 7
    assert cfg.main.validity_days == 365


def test_threshold_validity_explicit(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
          master_client_id: cid
          master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
          master_secret_name: sec
          threshold_days: 14
          validity_days: 180
        secrets:
          - name: sp1
            obj_id: app-001
            keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
            secret_name: sp1-secret
    """)
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(yaml_text)

    cfg = load_config(str(cfg_file))

    assert cfg.main.threshold_days == 14
    assert cfg.main.validity_days == 180


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


def test_validity_days_must_exceed_threshold(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
          master_client_id: cid
          master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
          master_secret_name: sec
          threshold_days: 30
          validity_days: 30
        secrets: []
    """)
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(yaml_text)

    with pytest.raises(Exception, match="validity_days"):
        load_config(str(cfg_file))


def test_threshold_days_negative_raises(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
          master_client_id: cid
          master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
          master_secret_name: sec
          threshold_days: -1
        secrets: []
    """)
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(yaml_text)

    with pytest.raises(Exception):
        load_config(str(cfg_file))


def test_validity_days_invalid_value_raises(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
          master_client_id: cid
          master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
          master_secret_name: sec
          validity_days: 200
        secrets: []
    """)
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(yaml_text)

    with pytest.raises(Exception):
        load_config(str(cfg_file))


def test_master_owners_default_empty(tmp_path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(MINIMAL_YAML)

    cfg = load_config(str(cfg_file))

    assert cfg.main.master_owners == []


def test_master_owners_from_yaml(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
          master_client_id: cid
          master_keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv
          master_secret_name: sec
          master_owners:
            - 00000000-0000-0000-0000-000000000001
            - 00000000-0000-0000-0000-000000000002
        secrets:
          - name: sp1
            obj_id: app-001
            keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
            secret_name: sp1-secret
    """)
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(yaml_text)

    cfg = load_config(str(cfg_file))

    assert cfg.main.master_owners == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]


def test_per_secret_threshold_and_validity_days(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
        secrets:
          - name: sp1
            obj_id: app-001
            keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
            secret_name: sp1-secret
            threshold_days: 30
            validity_days: 180
    """)
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(yaml_text)

    cfg = load_config(str(cfg_file))

    assert cfg.secrets[0].threshold_days == 30
    assert cfg.secrets[0].validity_days == 180


def test_per_secret_defaults_are_none(tmp_path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(MINIMAL_YAML)

    cfg = load_config(str(cfg_file))

    assert cfg.secrets[0].threshold_days is None
    assert cfg.secrets[0].validity_days is None


def test_per_secret_validity_must_exceed_threshold(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
        secrets:
          - name: sp1
            obj_id: app-001
            keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
            secret_name: sp1-secret
            threshold_days: 60
            validity_days: 60
    """)
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text(yaml_text)

    with pytest.raises(Exception, match="validity_days"):
        load_config(str(cfg_file))


def test_cleanup_old_secrets_default_false(tmp_path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(MINIMAL_YAML)

    cfg = load_config(str(cfg_file))

    assert cfg.main.cleanup_old_secrets is False


def test_cleanup_old_secrets_can_be_enabled(tmp_path):
    yaml_text = textwrap.dedent("""\
        main:
          tenant_id: tid
          cleanup_old_secrets: true
        secrets:
          - name: sp1
            obj_id: app-001
            keyvault_id: /subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv
            secret_name: sp1-secret
    """)
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(yaml_text)

    cfg = load_config(str(cfg_file))

    assert cfg.main.cleanup_old_secrets is True
