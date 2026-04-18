from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from srf.auth.provider import AuthProvider
from srf.config.models import load_config
from srf.graph.client import GraphClient
from srf.keyvault.client import KeyVaultClient
from srf.reporting.mail import MailReporter
from srf.rotation.rotator import RotationResult, SecretRotator
from srf.runner.parallel import ParallelRunner


def _make_kv_factory(credential):
    def factory(keyvault_id: str) -> KeyVaultClient:
        return KeyVaultClient(credential=credential, keyvault_id=keyvault_id)
    return factory


def _print_summary(results: list[RotationResult]) -> None:
    rotated = [r for r in results if r.rotated]
    skipped = [r for r in results if not r.rotated and r.error is None]
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
    parser.add_argument("--threshold-days", type=int, default=7, help="Days before expiry to trigger rotation (default: 7)")
    parser.add_argument("--validity-days", type=int, default=365, help="Validity of new secrets in days (default: 365)")
    args = parser.parse_args()

    config = load_config(args.config)

    auth = AuthProvider(config.main)
    master_credential = auth.get_master_credential()

    graph = GraphClient(credential=master_credential)
    kv_factory = _make_kv_factory(master_credential)

    rotator = SecretRotator(
        graph_client=graph,
        keyvault_client_factory=kv_factory,
        threshold_days=args.threshold_days,
        validity_days=args.validity_days,
    )
    runner = ParallelRunner(rotator=rotator, max_workers=args.workers)
    results = runner.run(config.secrets)

    _print_summary(results)

    if config.mail:
        print("\nSending email report...")
        reporter = MailReporter(
            mail_config=config.mail,
            keyvault_client_factory=kv_factory,
        )
        reporter.send_report(results)
        print("Email sent.")

    failed_count = sum(1 for r in results if r.error)
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
