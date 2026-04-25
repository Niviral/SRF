from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from srf.run_id.service import RunIdInfo, RunIdService

# ─── Helpers ──────────────────────────────────────────────────────────────────

_CLI_ENV: dict[str, str] = {}

_GHA_SCHEDULE_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "schedule",
    "GITHUB_RUN_ID": "14766323456",
}

_GHA_DISPATCH_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_RUN_ID": "14766323456",
}

_GHA_PUSH_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_RUN_ID": "99999999999",
}

_GHA_PR_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_RUN_ID": "123",
}

_GHA_UNKNOWN_EVENT_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_EVENT_NAME": "discussion",
    "GITHUB_RUN_ID": "777",
}


def _make_service(env: dict[str, str]) -> RunIdService:
    with patch.dict("os.environ", env, clear=True):
        return RunIdService()


# ─── Version and variant ──────────────────────────────────────────────────────


def test_generated_uuid_is_version_8():
    svc = _make_service(_CLI_ENV)
    u = uuid.UUID(svc.run_id)
    assert u.version == 8


def test_generated_uuid_has_correct_variant():
    svc = _make_service(_CLI_ENV)
    i = uuid.UUID(svc.run_id).int
    variant = (i >> 62) & 0x3
    assert variant == 0b10


# ─── CLI origin ───────────────────────────────────────────────────────────────


def test_cli_origin_detected():
    svc = _make_service(_CLI_ENV)
    assert svc.origin == "cli"


def test_cli_event_returns_cli():
    svc = _make_service(_CLI_ENV)
    assert svc.event == "cli"


def test_cli_origin_bit_is_zero():
    svc = _make_service(_CLI_ENV)
    i = uuid.UUID(svc.run_id).int
    origin_bit = (i >> 61) & 0x1
    assert origin_bit == 0


def test_cli_github_run_id_is_none():
    svc = _make_service(_CLI_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.github_run_id is None


# ─── GitHub Actions origin ────────────────────────────────────────────────────


def test_gha_origin_detected():
    svc = _make_service(_GHA_SCHEDULE_ENV)
    assert svc.origin == "github_actions"


def test_gha_origin_bit_is_one():
    svc = _make_service(_GHA_SCHEDULE_ENV)
    i = uuid.UUID(svc.run_id).int
    origin_bit = (i >> 61) & 0x1
    assert origin_bit == 1


def test_gha_schedule_event():
    svc = _make_service(_GHA_SCHEDULE_ENV)
    assert svc.event == "schedule"


def test_gha_workflow_dispatch_event():
    svc = _make_service(_GHA_DISPATCH_ENV)
    assert svc.event == "workflow_dispatch"


def test_gha_push_event():
    svc = _make_service(_GHA_PUSH_ENV)
    assert svc.event == "push"


def test_gha_pull_request_event():
    svc = _make_service(_GHA_PR_ENV)
    assert svc.event == "pull_request"


def test_gha_unknown_event_is_other():
    svc = _make_service(_GHA_UNKNOWN_EVENT_ENV)
    assert svc.event == "other"


# ─── GitHub run ID encoding ───────────────────────────────────────────────────


def test_gha_run_id_is_encoded_correctly():
    svc = _make_service(_GHA_SCHEDULE_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.github_run_id == 14766323456


def test_gha_run_id_large_value():
    svc = _make_service(_GHA_PUSH_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.github_run_id == 99999999999


# ─── Timestamp encoding ───────────────────────────────────────────────────────


def test_timestamp_is_within_reasonable_range():
    import time
    before = int(time.time() * 1000)
    svc = _make_service(_CLI_ENV)
    after = int(time.time() * 1000)
    info = RunIdService.decode(svc.run_id)
    assert before <= info.timestamp_ms <= after


def test_timestamp_is_positive():
    svc = _make_service(_CLI_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.timestamp_ms > 0


# ─── Decode ───────────────────────────────────────────────────────────────────


def test_decode_version_is_8():
    svc = _make_service(_GHA_DISPATCH_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.version == 8


def test_decode_datetime_utc_is_aware():
    from datetime import timezone
    svc = _make_service(_CLI_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.datetime_utc.tzinfo is not None
    assert info.datetime_utc.utcoffset().total_seconds() == 0


def test_round_trip_cli():
    svc = _make_service(_CLI_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.origin == "cli"
    assert info.github_run_id is None


def test_round_trip_gha_schedule():
    svc = _make_service(_GHA_SCHEDULE_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.origin == "github_actions"
    assert info.event == "schedule"
    assert info.github_run_id == 14766323456


def test_round_trip_gha_push():
    svc = _make_service(_GHA_PUSH_ENV)
    info = RunIdService.decode(svc.run_id)
    assert info.origin == "github_actions"
    assert info.event == "push"
    assert info.github_run_id == 99999999999


# ─── short_id ─────────────────────────────────────────────────────────────────


def test_short_id_is_13_chars():
    svc = _make_service(_CLI_ENV)
    assert len(svc.short_id) == 13


def test_short_id_is_prefix_of_run_id():
    svc = _make_service(_CLI_ENV)
    assert svc.run_id.startswith(svc.short_id)


# ─── Stability ────────────────────────────────────────────────────────────────


def test_run_id_is_stable_across_calls():
    svc = _make_service(_CLI_ENV)
    assert svc.run_id == svc.run_id


def test_two_instances_have_different_run_ids():
    """Two RunIdService instances should produce different UUIDs (random bits)."""
    svc1 = _make_service(_CLI_ENV)
    svc2 = _make_service(_CLI_ENV)
    # Very unlikely to collide (12 + 21 random bits); test would be flaky only
    # if secrets.randbits returns identical values twice in a row.
    assert svc1.run_id != svc2.run_id
