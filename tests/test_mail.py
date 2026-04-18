"""Tests for MailReporter — HTML/text content and SMTP interaction."""
from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from srf.config.models import MailConfig
from srf.keyvault.client import KeyVaultClient
from srf.reporting.mail import MailReporter
from srf.rotation.rotator import RotationResult


NOW = datetime.now(tz=timezone.utc)
KV_ID = "/subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv"

MAIL_CFG = MailConfig(
    smtp_host="smtp.test",
    smtp_port=587,
    smtp_user="bot@test.com",
    smtp_password_keyvault_id=KV_ID,
    smtp_password_secret_name="smtp-pass",
    from_address="bot@test.com",
    to_addresses=["ops@test.com", "dev@test.com"],
)


def _make_reporter(smtp_password="smtp-secret"):
    kv = MagicMock(spec=KeyVaultClient)
    kv.get_secret.return_value = smtp_password

    def kv_factory(_kv_id):
        return kv

    return MailReporter(mail_config=MAIL_CFG, keyvault_client_factory=kv_factory), kv


def _results():
    return [
        RotationResult(
            name="sp-rotated",
            app_id="app-r",
            rotated=True,
            new_expiry=NOW + timedelta(days=365),
            current_expiry=NOW + timedelta(days=3),
            keyvault_name="sp-kv",
        ),
        RotationResult(
            name="sp-skipped",
            app_id="app-s",
            rotated=False,
            current_expiry=NOW + timedelta(days=60),
            keyvault_name="sp-kv",
        ),
        RotationResult(
            name="sp-failed",
            app_id="app-f",
            rotated=False,
            error="Graph API timeout",
            keyvault_name="sp-kv",
        ),
    ]


# ---------------------------------------------------------------------------
# SMTP interaction
# ---------------------------------------------------------------------------

def test_send_report_uses_starttls_and_login(monkeypatch):
    reporter, _ = _make_reporter()
    smtp_mock = MagicMock()
    smtp_instance = MagicMock()
    smtp_mock.return_value.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_mock.return_value.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(smtplib, "SMTP", smtp_mock)
    reporter.send_report(_results())

    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("bot@test.com", "smtp-secret")


def test_send_report_sends_to_all_recipients(monkeypatch):
    reporter, _ = _make_reporter()
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, user, pwd): pass
        def sendmail(self, from_addr, to_addrs, msg):
            sent["from"] = from_addr
            sent["to"] = to_addrs
            sent["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    reporter.send_report(_results())

    assert sent["from"] == "bot@test.com"
    assert "ops@test.com" in sent["to"]
    assert "dev@test.com" in sent["to"]


def test_send_report_fetches_smtp_password_from_kv(monkeypatch):
    reporter, kv = _make_reporter()

    class FakeSMTP:
        def __init__(self, h, p): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, *a): pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    reporter.send_report(_results())

    kv.get_secret.assert_called_once_with("smtp-pass")


# ---------------------------------------------------------------------------
# Report content
# ---------------------------------------------------------------------------

def test_plain_report_contains_rotated_sp(monkeypatch):
    reporter, _ = _make_reporter()
    results = _results()
    plain = reporter._build_plain(results, "2026-01-01 00:00 UTC")

    assert "sp-rotated" in plain
    assert "app-r" in plain
    assert "sp-kv" in plain


def test_plain_report_contains_skipped_sp_with_expiry(monkeypatch):
    reporter, _ = _make_reporter()
    results = _results()
    plain = reporter._build_plain(results, "2026-01-01 00:00 UTC")

    assert "sp-skipped" in plain
    assert "app-s" in plain
    # current expiry date should appear
    expected_date = (NOW + timedelta(days=60)).strftime("%Y-%m-%d")
    assert expected_date in plain


def test_plain_report_contains_failed_sp_with_error(monkeypatch):
    reporter, _ = _make_reporter()
    results = _results()
    plain = reporter._build_plain(results, "2026-01-01 00:00 UTC")

    assert "sp-failed" in plain
    assert "Graph API timeout" in plain


def test_html_report_contains_all_sections(monkeypatch):
    reporter, _ = _make_reporter()
    html = reporter._build_html(_results(), "2026-01-01 00:00 UTC")

    assert "sp-rotated" in html
    assert "sp-skipped" in html
    assert "sp-failed" in html
    assert "Graph API timeout" in html
    assert "not expiring within threshold" in html


def test_html_report_shows_new_expiry_for_rotated(monkeypatch):
    reporter, _ = _make_reporter()
    results = _results()
    html = reporter._build_html(results, "2026-01-01 00:00 UTC")
    expected_date = (NOW + timedelta(days=365)).strftime("%Y-%m-%d")
    assert expected_date in html
