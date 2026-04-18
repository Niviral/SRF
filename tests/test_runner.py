"""Tests for ParallelRunner — concurrency and error isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from srf.config.models import SecretConfig
from srf.ownership.checker import OwnershipChecker, OwnershipResult
from srf.rotation.rotator import RotationResult, SecretRotator
from srf.runner.parallel import ParallelRunner


KV_ID = "/subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv"
NOW = datetime.now(tz=timezone.utc)


def _cfg(name, app_id):
    return SecretConfig(
        name=name,
        app_id=app_id,
        keyvault_id=KV_ID,
        keyvault_secret_name=f"{name}-secret",
    )


def _ok_result(cfg: SecretConfig) -> RotationResult:
    return RotationResult(name=cfg.name, app_id=cfg.app_id, rotated=True, new_expiry=NOW + timedelta(days=365))


def test_runner_returns_all_results():
    secrets = [_cfg("sp1", "a1"), _cfg("sp2", "a2"), _cfg("sp3", "a3")]

    rotator = MagicMock(spec=SecretRotator)
    rotator.rotate.side_effect = [_ok_result(s) for s in secrets]

    runner = ParallelRunner(rotator=rotator, max_workers=3)
    rotation_results, ownership_results = runner.run(secrets)

    assert len(rotation_results) == 3
    assert all(r.rotated for r in rotation_results)
    assert ownership_results == []


def test_runner_continues_after_one_failure():
    sp1 = _cfg("sp1", "a1")
    sp2 = _cfg("sp2", "a2")
    sp3 = _cfg("sp3", "a3")
    secrets = [sp1, sp2, sp3]

    def fake_rotate(cfg):
        if cfg.name == "sp2":
            raise RuntimeError("Unexpected crash")
        return _ok_result(cfg)

    rotator = MagicMock(spec=SecretRotator)
    rotator.rotate.side_effect = fake_rotate

    runner = ParallelRunner(rotator=rotator, max_workers=3)
    rotation_results, ownership_results = runner.run(secrets)

    assert len(rotation_results) == 3
    names = {r.name for r in rotation_results}
    assert names == {"sp1", "sp2", "sp3"}

    failed = [r for r in rotation_results if r.name == "sp2"]
    assert len(failed) == 1
    assert failed[0].rotated is False
    assert "Unexpected crash" in (failed[0].error or "")


def test_runner_respects_max_workers(monkeypatch):
    """Verify ThreadPoolExecutor is initialised with the configured max_workers."""
    import concurrent.futures as cf
    captured = {}

    original_init = cf.ThreadPoolExecutor.__init__

    def patched_init(self, max_workers=None, **kwargs):
        captured["max_workers"] = max_workers
        original_init(self, max_workers=max_workers, **kwargs)

    monkeypatch.setattr(cf.ThreadPoolExecutor, "__init__", patched_init)

    secrets = [_cfg("sp1", "a1")]
    rotator = MagicMock(spec=SecretRotator)
    rotator.rotate.return_value = _ok_result(secrets[0])

    runner = ParallelRunner(rotator=rotator, max_workers=2)
    runner.run(secrets)

    assert captured["max_workers"] == 2


def test_runner_empty_secrets():
    rotator = MagicMock(spec=SecretRotator)
    runner = ParallelRunner(rotator=rotator)
    rotation_results, ownership_results = runner.run([])
    assert rotation_results == []
    assert ownership_results == []
    rotator.rotate.assert_not_called()


def test_runner_ownership_results():
    """Ownership results are returned alongside rotation results when ownership_checker is provided."""
    secrets = [_cfg("sp1", "a1"), _cfg("sp2", "a2")]

    rotator = MagicMock(spec=SecretRotator)
    rotator.rotate.side_effect = [_ok_result(s) for s in secrets]

    ownership_checker = MagicMock(spec=OwnershipChecker)
    ownership_checker.check_and_update.side_effect = [
        OwnershipResult(name="sp1", app_id="a1", checked=True, owners_added=["u1"]),
        OwnershipResult(name="sp2", app_id="a2", checked=False),
    ]

    runner = ParallelRunner(rotator=rotator, ownership_checker=ownership_checker, max_workers=2)
    rotation_results, ownership_results = runner.run(secrets)

    assert len(rotation_results) == 2
    assert len(ownership_results) == 2
    assert all(r.rotated for r in rotation_results)
    ownership_names = {r.name for r in ownership_results}
    assert ownership_names == {"sp1", "sp2"}

