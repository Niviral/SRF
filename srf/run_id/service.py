from __future__ import annotations

import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# ─── Event code constants ─────────────────────────────────────────────────────

_EVENT_SCHEDULE          = 0
_EVENT_WORKFLOW_DISPATCH = 1
_EVENT_PUSH              = 2
_EVENT_PULL_REQUEST      = 3
_EVENT_OTHER             = 4

_EVENT_MAP: dict[str, int] = {
    "schedule":          _EVENT_SCHEDULE,
    "workflow_dispatch": _EVENT_WORKFLOW_DISPATCH,
    "push":              _EVENT_PUSH,
    "pull_request":      _EVENT_PULL_REQUEST,
}

_EVENT_NAMES: dict[int, str] = {v: k for k, v in _EVENT_MAP.items()}
_EVENT_NAMES[_EVENT_OTHER] = "other"

# ─── Result dataclass ─────────────────────────────────────────────────────────


@dataclass
class RunIdInfo:
    """Decoded fields from an SRF UUID v8 run identifier."""

    version:       int
    timestamp_ms:  int
    datetime_utc:  datetime
    origin:        str            # "cli" | "github_actions"
    event:         str            # "schedule" | "workflow_dispatch" | "push" | "pull_request" | "other"
    github_run_id: Optional[int]  # None for CLI runs


# ─── RunIdService ─────────────────────────────────────────────────────────────


class RunIdService:
    """Generates and decodes UUID v8 run identifiers for SRF.

    The UUID encodes the Unix millisecond timestamp, the run origin
    (CLI vs GitHub Actions), the triggering event type, and the GitHub
    Actions run ID — allowing any rotated credential to be traced back
    to the exact automation run that created it.

    UUID v8 bit layout (128 bits):
        bits  0–47  : Unix ms timestamp           (48 bits, time-ordered)
        bits 48–51  : version = 0b1000            (4 bits,  fixed per RFC 9562)
        bits 52–63  : random                      (12 bits, uniqueness within ms)
        bits 64–65  : variant = 0b10              (2 bits,  fixed per RFC 4122)
        bit  66     : origin  (0=CLI, 1=GHA)      (1 bit)
        bits 67–69  : event_code                  (3 bits,  see _EVENT_MAP)
        bits 70–106 : github_run_id               (37 bits, 0 for CLI runs)
        bits 107–127: random                      (21 bits, additional uniqueness)
    """

    def __init__(self) -> None:
        self._run_id: str = self._generate()

    # ------------------------------------------------------------------

    @property
    def run_id(self) -> str:
        """Full UUID v8 string, stable for the lifetime of this service instance."""
        return self._run_id

    @property
    def short_id(self) -> str:
        """First 13 characters of the UUID (timestamp + version nibble, e.g. '019dc4f8-3611').

        Safe to use as an Azure AD credential ``display_name`` prefix.
        Timestamp is decodable from this prefix alone.
        """
        return self._run_id[:13]

    @property
    def origin(self) -> str:
        """'github_actions' or 'cli'."""
        return self.decode(self._run_id).origin

    @property
    def event(self) -> str:
        """GitHub Actions event name ('schedule', 'workflow_dispatch', etc.) or 'other'/'cli'."""
        info = self.decode(self._run_id)
        return info.event if info.origin == "github_actions" else "cli"

    # ------------------------------------------------------------------

    @staticmethod
    def _detect_context() -> tuple[int, int, int]:
        """Return (origin_bit, event_code, github_run_id) from the process environment."""
        if os.environ.get("GITHUB_ACTIONS") == "true":
            event_name = os.environ.get("GITHUB_EVENT_NAME", "")
            event_code = _EVENT_MAP.get(event_name, _EVENT_OTHER)
            run_id     = int(os.environ.get("GITHUB_RUN_ID", "0") or "0")
            return 1, event_code, run_id
        return 0, 0, 0

    @staticmethod
    def _build_uuid8(
        timestamp_ms: int,
        origin:       int,
        event_code:   int,
        run_id:       int,
        rand12:       int,
        rand21:       int,
    ) -> str:
        """Pack the six components into a UUID v8 string."""
        u = 0
        u |= (timestamp_ms & 0xFFFFFFFFFFFF) << 80   # bits  0–47: timestamp
        u |= 0x8                              << 76   # bits 48–51: version = 8
        u |= (rand12  & 0xFFF)               << 64   # bits 52–63: rand_a
        u |= 0b10                            << 62   # bits 64–65: variant = 0b10
        u |= (origin  & 0x1)                 << 61   # bit  66:    origin
        u |= (event_code & 0x7)              << 58   # bits 67–69: event_code
        u |= (run_id  & 0x1FFFFFFFFF)        << 21   # bits 70–106: github_run_id
        u |= (rand21  & 0x1FFFFF)                    # bits 107–127: rand_b
        h = f"{u:032x}"
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _generate(self) -> str:
        """Generate a fresh UUID v8 for this run."""
        ts_ms                       = int(time.time() * 1000)
        origin, event_code, run_id  = self._detect_context()
        rand12                      = secrets.randbits(12)
        rand21                      = secrets.randbits(21)
        return self._build_uuid8(ts_ms, origin, event_code, run_id, rand12, rand21)

    # ------------------------------------------------------------------

    @staticmethod
    def decode(run_id_str: str) -> RunIdInfo:
        """Decode a UUID v8 SRF run identifier into its constituent fields."""
        i = uuid.UUID(run_id_str).int

        ts_ms       = (i >> 80) & 0xFFFFFFFFFFFF
        version     = (i >> 76) & 0xF
        origin_bit  = (i >> 61) & 0x1
        event_code  = (i >> 58) & 0x7
        run_id_raw  = (i >> 21) & 0x1FFFFFFFFF

        origin = "github_actions" if origin_bit else "cli"
        event  = _EVENT_NAMES.get(event_code, "other") if origin_bit else "cli"
        return RunIdInfo(
            version      = version,
            timestamp_ms = ts_ms,
            datetime_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
            origin       = origin,
            event        = event,
            github_run_id= run_id_raw if origin_bit else None,
        )
