from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from srf.auth.provider import AuthProvider
from srf.config.models import load_config
from srf.graph.client import GraphClient
from srf.keyvault.client import KeyVaultClient
from srf.ownership.checker import OwnershipChecker, OwnershipResult
from srf.reporting.mail import MailReporter
from srf.rotation.rotator import RotationResult, SecretRotator
from srf.runner.parallel import ParallelRunner


def _make_kv_factory(credential):
    def factory(keyvault_id: str) -> KeyVaultClient:
        return KeyVaultClient(credential=credential, keyvault_id=keyvault_id)
    return factory


def _print_summary(results: list[RotationResult]) -> None:
    rotated = [r for r in results if r.rotated]
    skipped = [r for r in results if not r.rotated and r.error is None and not r.dry_run]
    dry_run_results = [r for r in results if r.dry_run]
    failed = [r for r in results if not r.rotated and r.error is not None]

    col = "{:<20} {:<38} {:<26} {:<26} {}"
    header = col.format("NAME", "APP ID", "STATUS", "EXPIRY", "DETAIL")
    sep = "-" * len(header)

    print(f"\n{'='*len(header)}")
    print("Azure SP Secret Rotation — Summary")
    print(f"Run: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(sep)
    print(header)
    print(sep)

    for r in rotated:
        print(col.format(
            r.name[:19], r.app_id,
            "✓ ROTATED",
            _fmt(r.new_expiry),
            f"vault={r.keyvault_name}",
        ))
    for r in skipped:
        print(col.format(
            r.name[:19], r.app_id,
            "– SKIPPED",
            _fmt(r.current_expiry),
            "not expiring soon",
        ))
    for r in dry_run_results:
        if r.rotation_needed or r.was_created:
            label = "WOULD CREATE" if r.was_created else "WOULD ROTATE"
            print(col.format(
                r.name[:19], r.app_id,
                f"~ {label}",
                _fmt(r.current_expiry),
                f"vault={r.keyvault_name}",
            ))
        else:
            print(col.format(
                r.name[:19], r.app_id,
                "– NO CHANGE",
                _fmt(r.current_expiry),
                "not expiring soon (dry-run)",
            ))
    for r in failed:
        print(col.format(
            r.name[:19], r.app_id,
            "✗ FAILED",
            "",
            (r.error or "")[:60],
        ))

    print(sep)
    print(f"Total: {len(results)}  |  Rotated: {len(rotated)}  |  Skipped: {len(skipped)}  |  Failed: {len(failed)}")
    print("=" * len(header))


def _print_ownership_summary(ownership_results: list[OwnershipResult]) -> None:
    if not ownership_results:
        return
    checked = [r for r in ownership_results if r.checked]
    skipped = [r for r in ownership_results if not r.checked]
    added_any = [r for r in checked if r.owners_added]
    failed = [r for r in checked if r.error is not None]

    col = "{:<20} {:<38} {:<12} {}"
    header = col.format("NAME", "APP ID", "STATUS", "DETAIL")
    sep = "-" * len(header)

    print(f"\n{'='*len(header)}")
    print("Azure SP Ownership — Summary")
    print(sep)
    print(header)
    print(sep)

    for r in skipped:
        print(col.format(r.name[:19], r.app_id, "SKIPPED", "no required_owners configured"))
    for r in checked:
        if r.dry_run:
            if r.owners_would_add:
                print(col.format(r.name[:19], r.app_id, "~ WOULD UPDATE", f"would_add={r.owners_would_add}"))
            else:
                print(col.format(r.name[:19], r.app_id, "– OK", "owners OK (dry-run)"))
        elif r.error:
            print(col.format(r.name[:19], r.app_id, "✗ FAILED", (r.error or "")[:60]))
        elif r.owners_added:
            print(col.format(r.name[:19], r.app_id, "✓ UPDATED", f"added={r.owners_added}"))
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Azure SP client-secret rotation tool")
    parser.add_argument("--config", default="input.yaml", help="Path to YAML config (default: input.yaml)")
    parser.add_argument("--workers", type=int, default=5, help="Max parallel workers (default: 5)")
    parser.add_argument("--threshold-days", type=int, default=None, help="Days before expiry to trigger rotation (default: 7)")
    parser.add_argument("--validity-days", type=int, default=None, help="Validity of new secrets in days (default: 365)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without making any writes")
    parser.add_argument("--no-mail", action="store_true", help="Suppress email report even if mail config is present")
    parser.add_argument("--validate", action="store_true", help="Validate input YAML against config.schema.json and exit")
    args = parser.parse_args()

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

    threshold = args.threshold_days if args.threshold_days is not None else config.main.threshold_days
    validity = args.validity_days if args.validity_days is not None else config.main.validity_days

    auth = AuthProvider(config.main)
    master_credential = auth.get_master_credential()

    graph = GraphClient(credential=master_credential)
    kv_factory = _make_kv_factory(master_credential)

    rotator = SecretRotator(
        graph_client=graph,
        keyvault_client_factory=kv_factory,
        threshold_days=threshold,
        validity_days=validity,
        dry_run=args.dry_run,
    )
    ownership_checker = OwnershipChecker(graph_client=graph, master_owners=config.main.master_owners, dry_run=args.dry_run)
    runner = ParallelRunner(rotator=rotator, ownership_checker=ownership_checker, max_workers=args.workers)
    rotation_results, ownership_results = runner.run(config.secrets)

    _print_summary(rotation_results)
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
            print(f"WARNING: email report failed ({type(exc).__name__}) — rotation results above are complete.")

    failed_count = sum(1 for r in rotation_results if r.error)
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
