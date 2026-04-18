"""Tests for SecretRotator — expiry logic and rotation flow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from srf.config.models import SecretConfig
from srf.rotation.rotator import SecretRotator, RotationResult


KV_ID = "/subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/sp-kv"

NOW = datetime.now(tz=timezone.utc)


def _cred(days_from_now: int):
    c = MagicMock()
    c.key_id = "key-old"
    c.end_date_time = NOW + timedelta(days=days_from_now)
    return c


def _make_secret_cfg(description=None) -> SecretConfig:
    return SecretConfig(
        name="sp1",
        app_id="app-0001",
        keyvault_id=KV_ID,
        keyvault_secret_name="sp1-secret",
        keyvault_secret_description=description,
    )


def _make_rotator(graph, kv):
    return SecretRotator(
        graph_client=graph,
        keyvault_client_factory=lambda _: kv,
    )


# ---------------------------------------------------------------------------
# needs_rotation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days,expected", [
    (-1, True),   # already expired
    (0, True),    # expires today
    (6, True),    # within 7-day threshold
    (7, True),    # exactly at threshold boundary (<=)
    (8, False),   # just outside threshold
    (30, False),  # well in the future
])
def test_needs_rotation_threshold(days, expected):
    graph = MagicMock()
    kv = MagicMock()
    rotator = _make_rotator(graph, kv)
    cred = _cred(days)
    result, _ = rotator.needs_rotation([cred])
    assert result is expected


def test_needs_rotation_empty_returns_true():
    rotator = _make_rotator(MagicMock(), MagicMock())
    result, expiry = rotator.needs_rotation([])
    assert result is True
    assert expiry is None


def test_needs_rotation_returns_soonest_expiry():
    rotator = _make_rotator(MagicMock(), MagicMock())
    creds = [_cred(20), _cred(10), _cred(30)]
    _, soonest = rotator.needs_rotation(creds)
    expected = NOW + timedelta(days=10)
    assert abs((soonest - expected).total_seconds()) < 2


# ---------------------------------------------------------------------------
# rotate — no rotation needed
# ---------------------------------------------------------------------------

def test_rotate_skipped_when_not_expiring():
    graph = MagicMock()
    graph.list_password_credentials.return_value = [_cred(30)]

    rotator = _make_rotator(graph, MagicMock())
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is False
    assert result.error is None
    assert result.keyvault_name == "sp-kv"


# ---------------------------------------------------------------------------
# rotate — rotation performed
# ---------------------------------------------------------------------------

def test_rotate_performs_rotation():
    new_key_id = "key-new"
    new_cred = MagicMock()
    new_cred.key_id = new_key_id
    new_cred.secret_text = "new-secret-value"
    new_cred.end_date_time = NOW + timedelta(days=365)

    old_cred = _cred(-5)  # already expired

    graph = MagicMock()
    graph.list_password_credentials.return_value = [old_cred]
    graph.add_password_credential.return_value = new_cred

    kv = MagicMock()
    rotator = _make_rotator(graph, kv)
    result = rotator.rotate(_make_secret_cfg(description="desc"))

    assert result.rotated is True
    assert result.error is None
    assert result.keyvault_name == "sp-kv"
    assert result.new_expiry == new_cred.end_date_time

    kv.set_secret.assert_called_once_with(
        name="sp1-secret",
        value="new-secret-value",
        description="desc",
    )
    graph.remove_password_credential.assert_called_once_with(
        app_id="app-0001",
        key_id="key-old",
    )


def test_rotate_new_cred_not_removed():
    """The newly created credential must not be deleted."""
    new_cred = MagicMock()
    new_cred.key_id = "key-new"
    new_cred.secret_text = "val"
    new_cred.end_date_time = NOW + timedelta(days=365)

    old_cred = _cred(-1)
    old_cred.key_id = "key-old"

    graph = MagicMock()
    graph.list_password_credentials.return_value = [old_cred]
    graph.add_password_credential.return_value = new_cred

    rotator = _make_rotator(graph, MagicMock())
    rotator.rotate(_make_secret_cfg())

    # remove should only be called for the OLD key
    call_args = [c.kwargs["key_id"] for c in graph.remove_password_credential.call_args_list]
    assert "key-new" not in call_args
    assert "key-old" in call_args


# ---------------------------------------------------------------------------
# rotate — error handling
# ---------------------------------------------------------------------------

def test_rotate_returns_error_result_on_graph_failure():
    graph = MagicMock()
    graph.list_password_credentials.side_effect = RuntimeError("Graph API down")

    rotator = _make_rotator(graph, MagicMock())
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is False
    assert "Graph API down" in result.error


def test_rotate_returns_error_result_on_kv_failure():
    new_cred = MagicMock()
    new_cred.key_id = "k-new"
    new_cred.secret_text = "v"
    new_cred.end_date_time = NOW + timedelta(days=365)

    graph = MagicMock()
    graph.list_password_credentials.return_value = [_cred(-1)]
    graph.add_password_credential.return_value = new_cred

    kv = MagicMock()
    kv.set_secret.side_effect = RuntimeError("KV write failed")

    rotator = _make_rotator(graph, kv)
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is False
    assert "KV write failed" in result.error


def test_rotate_cleanup_failure_recorded_as_warning():
    """If old credential removal fails, it should be in cleanup_warnings, not error."""
    new_cred = MagicMock()
    new_cred.key_id = "k-new"
    new_cred.secret_text = "v"
    new_cred.end_date_time = NOW + timedelta(days=365)

    old_cred = _cred(-5)
    old_cred.key_id = "k-old"

    graph = MagicMock()
    graph.list_password_credentials.return_value = [old_cred]
    graph.add_password_credential.return_value = new_cred
    graph.remove_password_credential.side_effect = RuntimeError("Graph error")

    rotator = _make_rotator(graph, MagicMock())
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is True
    assert result.error is None
    assert len(result.cleanup_warnings) == 1
    assert "k-old" in result.cleanup_warnings[0]
    assert "RuntimeError" in result.cleanup_warnings[0]

def _make_dry_rotator(graph, kv):
    return SecretRotator(
        graph_client=graph,
        keyvault_client_factory=lambda _: kv,
        dry_run=True,
    )


def test_dry_run_skips_writes_when_rotation_needed():
    graph = MagicMock()
    graph.list_password_credentials.return_value = [_cred(-1)]  # expired

    kv = MagicMock()
    rotator = _make_dry_rotator(graph, kv)
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is False
    assert result.dry_run is True
    assert result.rotation_needed is True
    graph.add_password_credential.assert_not_called()
    kv.set_secret.assert_not_called()


def test_dry_run_skips_when_not_expiring():
    graph = MagicMock()
    graph.list_password_credentials.return_value = [_cred(30)]

    rotator = _make_dry_rotator(graph, MagicMock())
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is False
    assert result.dry_run is True
    assert result.rotation_needed is False


def test_was_created_true_when_no_prior_credentials():
    new_cred = MagicMock()
    new_cred.key_id = "key-new"
    new_cred.secret_text = "s"
    new_cred.end_date_time = NOW + timedelta(days=365)

    graph = MagicMock()
    graph.list_password_credentials.return_value = []
    graph.add_password_credential.return_value = new_cred

    rotator = _make_rotator(graph, MagicMock())
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is True
    assert result.was_created is True


def test_was_created_false_when_credentials_exist():
    new_cred = MagicMock()
    new_cred.key_id = "key-new"
    new_cred.secret_text = "s"
    new_cred.end_date_time = NOW + timedelta(days=365)

    graph = MagicMock()
    graph.list_password_credentials.return_value = [_cred(-1)]
    graph.add_password_credential.return_value = new_cred

    rotator = _make_rotator(graph, MagicMock())
    result = rotator.rotate(_make_secret_cfg())

    assert result.rotated is True
    assert result.was_created is False


def test_dry_run_was_created_true_no_prior_creds():
    graph = MagicMock()
    graph.list_password_credentials.return_value = []

    kv = MagicMock()
    rotator = _make_dry_rotator(graph, kv)
    result = rotator.rotate(_make_secret_cfg())

    assert result.dry_run is True
    assert result.was_created is True
    graph.add_password_credential.assert_not_called()
    kv.set_secret.assert_not_called()
