from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from srf.auth.provider import AuthProvider
from srf.config.models import load_config
from srf.graph.client import GraphClient
from srf.keyvault.client import KeyVaultClient
from srf.ownership.checker import OwnershipChecker, OwnershipResult
from srf.reporting.mail import MailReporter
from srf.rotation.rotator import RotationResult, SecretRotator
from srf.run_id.service import RunIdService
from srf.runner.parallel import ParallelRunner

logger = logging.getLogger(__name__)


def _make_kv_factory(credential):
    def factory(keyvault_id: str) -> KeyVaultClient:
        return KeyVaultClient(credential=credential, keyvault_id=keyvault_id)

    return factory


def _print_summary(results: list[RotationResult], run_id: Optional[str] = None) -> None:
    rotated = [r for r in results if r.rotated]
    skipped = [
        r for r in results if not r.rotated and r.error is None and not r.dry_run
    ]
    dry_run_results = [r for r in results if r.dry_run]
    failed = [r for r in results if not r.rotated and r.error is not None]

    col = "{:<20} {:<38} {:<26} {:<26} {}"
    header = col.format("NAME", "APP ID", "STATUS", "EXPIRY", "DETAIL")
    sep = "-" * len(header)

    print(f"\n{'=' * len(header)}")
    print("Azure SP Secret Rotation — Summary")
    print(f"Run: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    if run_id:
        print(f"Run ID: {run_id}")
    print(sep)
    print(header)
    print(sep)

    for r in rotated:
        detail = f"vault={r.keyvault_name}"
        if r.kv_secret_missing:
            detail += " (kv-secret-missing)"
        print(
            col.format(r.name[:19], r.app_id, "✓ ROTATED", _fmt(r.new_expiry), detail)
        )
        for warning in r.cleanup_warnings:
            print(f"  ⚠ CLEANUP WARNING: {warning}")
    for r in skipped:
        print(
            col.format(
                r.name[:19],
                r.app_id,
                "– SKIPPED",
                _fmt(r.current_expiry),
                "not expiring soon",
            )
        )
    for r in dry_run_results:
        if r.rotation_needed or r.was_created:
            label = "WOULD CREATE" if r.was_created else "WOULD ROTATE"
            reason = (
                "kv-secret-missing"
                if r.kv_secret_missing
                else f"vault={r.keyvault_name}"
            )
            print(
                col.format(
                    r.name[:19], r.app_id, f"~ {label}", _fmt(r.current_expiry), reason
                )
            )
        else:
            print(
                col.format(
                    r.name[:19],
                    r.app_id,
                    "– NO CHANGE",
                    _fmt(r.current_expiry),
                    "not expiring soon (dry-run)",
                )
            )
    for r in failed:
        print(
            col.format(
                r.name[:19],
                r.app_id,
                "✗ FAILED",
                "",
                (r.error or "")[:60],
            )
        )

    print(sep)
    print(
        f"Total: {len(results)}  |  Rotated: {len(rotated)}  |  Skipped: {len(skipped)}  |  Failed: {len(failed)}"
    )
    print("=" * len(header))


def _print_ownership_summary(ownership_results: list[OwnershipResult]) -> None:
    if not ownership_results:
        return
    checked = [r for r in ownership_results if r.checked]
    skipped = [r for r in ownership_results if not r.checked]
    added_any = [r for r in checked if r.owners_added]
    failed = [r for r in checked if r.error is not None]
    warning = [r for r in checked if r.warning is not None]

    col = "{:<20} {:<38} {:<12} {}"
    header = col.format("NAME", "APP ID", "STATUS", "DETAIL")
    sep = "-" * len(header)

    print(f"\n{'=' * len(header)}")
    print("Azure SP Ownership — Summary")
    print(sep)
    print(header)
    print(sep)

    for r in skipped:
        print(
            col.format(
                r.name[:19], r.app_id, "SKIPPED", "no required_owners configured"
            )
        )
    for r in checked:
        if r.dry_run:
            if r.owners_would_add:
                print(
                    col.format(
                        r.name[:19],
                        r.app_id,
                        "~ WOULD UPDATE",
                        f"would_add={r.owners_would_add}",
                    )
                )
            else:
                print(col.format(r.name[:19], r.app_id, "– OK", "owners OK (dry-run)"))
        elif r.error:
            print(col.format(r.name[:19], r.app_id, "✗ FAILED", (r.error or "")[:60]))
        elif r.owners_added:
            print(
                col.format(
                    r.name[:19], r.app_id, "✓ UPDATED", f"added={r.owners_added}"
                )
            )
        elif r.warning:
            print(
                col.format(r.name[:19], r.app_id, "⚠ WARNING", (r.warning or "")[:60])
            )
        else:
            print(col.format(r.name[:19], r.app_id, "– OK", f"all owners present"))

    print(sep)
    print(
        f"Total: {len(ownership_results)}  |  Updated: {len(added_any)}  |  "
        f"Skipped: {len(skipped)}  |  Failed: {len(failed)}"
    )
    print("=" * len(header))


def _fmt(dt) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        from datetime import timezone as tz

        dt = dt.replace(tzinfo=tz.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _print_decoded_run_id(run_id_str: str) -> int:
    """Decode and pretty-print a UUID v8 SRF run identifier."""
    from srf.run_id.service import RunIdService

    try:
        info = RunIdService.decode(run_id_str)
    except (ValueError, AttributeError) as exc:
        print(
            f"Error: '{run_id_str}' is not a valid SRF run ID ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1

    if info.version != 8:
        print(
            f"Warning: UUID version is {info.version}, expected 8. Results may be incorrect.",
            file=sys.stderr,
        )

    gha_run_url = (
        f"  github_run_url : https://github.com/<owner>/<repo>/actions/runs/{info.github_run_id}"
        if info.github_run_id is not None
        else ""
    )

    print(
        f"""
SRF Run ID Decoder
══════════════════════════════════════════════════
  run_id         : {run_id_str}
  version        : {info.version}
  timestamp_ms   : {info.timestamp_ms}
  datetime_utc   : {info.datetime_utc.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]} UTC
  origin         : {info.origin}
  event          : {info.event}
  github_run_id  : {info.github_run_id if info.github_run_id is not None else "N/A (CLI run)"}
{gha_run_url}══════════════════════════════════════════════════""".strip()
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Azure SP client-secret rotation tool")
    subparsers = parser.add_subparsers(dest="command")

    # ── decode subcommand ──────────────────────────────────────────────────────
    decode_parser = subparsers.add_parser(
        "decode",
        help="Decode a UUID v8 SRF run identifier and print its fields",
    )
    decode_parser.add_argument(
        "run_id", metavar="RUN_ID", help="UUID v8 run ID to decode"
    )

    # ── rotate subcommand (default when no subcommand given) ───────────────────
    rotate_parser = subparsers.add_parser(
        "rotate",
        help="Rotate Azure SP client secrets (default when no subcommand is given)",
    )
    rotate_parser.add_argument(
        "--config",
        default="input.yaml",
        help="Path to YAML config (default: input.yaml)",
    )
    rotate_parser.add_argument(
        "--workers", type=int, default=5, help="Max parallel workers (default: 5)"
    )
    rotate_parser.add_argument(
        "--threshold-days",
        type=int,
        default=None,
        help="Days before expiry to trigger rotation (default: 7)",
    )
    rotate_parser.add_argument(
        "--validity-days",
        type=int,
        default=None,
        help="Validity of new secrets in days (default: 365)",
    )
    rotate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without making any writes",
    )
    rotate_parser.add_argument(
        "--no-mail",
        action="store_true",
        help="Suppress email report even if mail config is present",
    )
    rotate_parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate input YAML against config.schema.json and exit",
    )
    rotate_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for SRF modules (overrides SRF_LOG_LEVEL)",
    )

    # Re-attach rotate flags to the top-level parser so that bare `main.py`
    # (no subcommand) still works — this preserves backwards compatibility.
    parser.add_argument("--config", default="input.yaml", help=argparse.SUPPRESS)
    parser.add_argument("--workers", type=int, default=5, help=argparse.SUPPRESS)
    parser.add_argument(
        "--threshold-days", type=int, default=None, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--validity-days", type=int, default=None, help=argparse.SUPPRESS
    )
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-mail", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--validate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command == "decode":
        return _print_decoded_run_id(args.run_id)

    # ── rotate (explicit subcommand or no subcommand at all) ───────────────────
    # Logging level priority: --debug flag > SRF_LOG_LEVEL env var > WARNING default.
    # Only srf.* loggers are elevated — third-party loggers (azure, msgraph, kiota,
    # urllib3) stay at WARNING to prevent leaking tokens or request bodies.
    _ENV_LOG_LEVEL = "LOG_LEVEL"
    _VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if args.debug:
        log_level = logging.DEBUG
    else:
        env_level = os.environ.get(_ENV_LOG_LEVEL, "").upper()
        if env_level and env_level not in _VALID_LEVELS:
            print(
                f"WARNING: invalid {_ENV_LOG_LEVEL}={env_level!r}, expected one of {sorted(_VALID_LEVELS)}. Defaulting to WARNING.",
                file=sys.stderr,
            )
            env_level = ""
        log_level = getattr(logging, env_level) if env_level else logging.WARNING
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("srf").setLevel(log_level)
    logging.getLogger(__name__).setLevel(log_level)

    if args.validate:
        import json
        import pathlib

        import jsonschema
        import yaml as _yaml

        schema_path = pathlib.Path(__file__).parent / "config.schema.json"
        schema = json.loads(schema_path.read_text())
        raw = _yaml.safe_load(open(args.config))
        try:
            jsonschema.validate(instance=raw, schema=schema)
            print("✅ Config is valid.")
        except jsonschema.ValidationError as e:
            print(f"❌ Validation error: {e.message}")
            sys.exit(1)
        sys.exit(0)

    config = load_config(args.config)
    logger.info(
        "Config loaded from %s — %d SP(s) configured", args.config, len(config.secrets)
    )

    run_id_svc = RunIdService()
    print(
        f"Run ID: {run_id_svc.run_id}  origin={run_id_svc.origin}  event={run_id_svc.event}"
    )
    logger.debug(
        "run_id=%s origin=%s event=%s",
        run_id_svc.run_id,
        run_id_svc.origin,
        run_id_svc.event,
    )

    threshold = (
        args.threshold_days
        if args.threshold_days is not None
        else config.main.threshold_days
    )
    validity = (
        args.validity_days
        if args.validity_days is not None
        else config.main.validity_days
    )

    auth = AuthProvider(config.main)
    master_credential = auth.get_master_credential()
    logger.info(
        "Starting rotation run: dry_run=%s, threshold_days=%s, validity_days=%s",
        args.dry_run,
        threshold,
        validity,
    )

    graph = GraphClient(credential=master_credential)
    kv_factory = _make_kv_factory(master_credential)

    rotator = SecretRotator(
        graph_client=graph,
        keyvault_client_factory=kv_factory,
        threshold_days=threshold,
        validity_days=validity,
        dry_run=args.dry_run,
        run_id=run_id_svc.run_id,
        cleanup_old_secrets=config.main.cleanup_old_secrets,
    )
    ownership_checker = OwnershipChecker(
        graph_client=graph,
        master_owners=config.main.master_owners,
        dry_run=args.dry_run,
    )
    runner = ParallelRunner(
        rotator=rotator, ownership_checker=ownership_checker, max_workers=args.workers
    )
    rotation_results, ownership_results = runner.run(config.secrets)

    _print_summary(rotation_results, run_id=run_id_svc.run_id)
    _print_ownership_summary(ownership_results)

    if not args.no_mail and config.mail:
        print("\nSending email report...")
        try:
            reporter = MailReporter(
                mail_config=config.mail,
                keyvault_client_factory=kv_factory,
            )
            reporter.send_report(rotation_results)
            print("Email sent.")
        except Exception as exc:
            print(
                f"WARNING: email report failed ({type(exc).__name__}) — rotation results above are complete."
            )

    failed_count = sum(1 for r in rotation_results if r.error)
    ownership_failed_count = sum(1 for r in ownership_results if r.error)
    return 1 if (failed_count + ownership_failed_count) > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
