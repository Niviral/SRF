from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable

from srf.config.models import MailConfig
from srf.keyvault.client import KeyVaultClient
from srf.rotation.rotator import RotationResult


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


class MailReporter:
    """Build and send a rotation summary email via SMTP."""

    def __init__(
        self,
        mail_config: MailConfig,
        keyvault_client_factory: Callable[[str], KeyVaultClient],
    ) -> None:
        self._cfg = mail_config
        self._kv_factory = keyvault_client_factory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_smtp_password(self) -> str:
        kv = self._kv_factory(self._cfg.smtp_password_keyvault_id)
        return kv.get_secret(self._cfg.smtp_password_secret_name)

    def _build_plain(self, results: list[RotationResult], run_ts: str) -> str:
        rotated = [r for r in results if r.rotated]
        skipped = [r for r in results if not r.rotated and r.error is None]
        failed = [r for r in results if not r.rotated and r.error is not None]

        lines = [
            "Azure SP Secret Rotation Report",
            f"Run time: {run_ts}",
            "=" * 60,
            "",
        ]

        lines += [f"ROTATED ({len(rotated)})", "-" * 40]
        if rotated:
            for r in rotated:
                lines.append(
                    f"  {r.name} | app_id={r.app_id} | "
                    f"new_expiry={_fmt_dt(r.new_expiry)} | "
                    f"vault={r.keyvault_name} | "
                    f"prev_expiry={_fmt_dt(r.current_expiry)}"
                )
        else:
            lines.append("  (none)")
        lines.append("")

        lines += [f"SKIPPED – not expiring soon ({len(skipped)})", "-" * 40]
        if skipped:
            for r in skipped:
                lines.append(
                    f"  {r.name} | app_id={r.app_id} | "
                    f"current_expiry={_fmt_dt(r.current_expiry)}"
                )
        else:
            lines.append("  (none)")
        lines.append("")

        lines += [f"FAILED ({len(failed)})", "-" * 40]
        if failed:
            for r in failed:
                lines.append(f"  {r.name} | app_id={r.app_id} | error={r.error}")
        else:
            lines.append("  (none)")

        return "\n".join(lines)

    def _build_html(self, results: list[RotationResult], run_ts: str) -> str:
        rotated = [r for r in results if r.rotated]
        skipped = [r for r in results if not r.rotated and r.error is None]
        failed = [r for r in results if not r.rotated and r.error is not None]

        def rows(items, cols_fn) -> str:
            if not items:
                return f'<tr><td colspan="5" style="color:#888">none</td></tr>'
            return "".join(f"<tr>{cols_fn(r)}</tr>" for r in items)

        rotated_rows = rows(
            rotated,
            lambda r: (
                f"<td>{r.name}</td><td>{r.app_id}</td>"
                f"<td style='color:green'>{_fmt_dt(r.new_expiry)}</td>"
                f"<td>{r.keyvault_name}</td>"
                f"<td>{_fmt_dt(r.current_expiry)}</td>"
            ),
        )
        skipped_rows = rows(
            skipped,
            lambda r: (
                f"<td>{r.name}</td><td>{r.app_id}</td>"
                f"<td>{_fmt_dt(r.current_expiry)}</td>"
                f"<td colspan='2'>not expiring within threshold</td>"
            ),
        )
        failed_rows = rows(
            failed,
            lambda r: (
                f"<td>{r.name}</td><td>{r.app_id}</td>"
                f"<td colspan='3' style='color:red'>{r.error}</td>"
            ),
        )

        th = "style='background:#f2f2f2;padding:6px 12px;border:1px solid #ccc'"
        td_style = "padding:6px 12px;border:1px solid #ddd"

        return f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;font-size:14px">
<h2>Azure SP Secret Rotation Report</h2>
<p>Run time: <strong>{run_ts}</strong></p>

<h3 style="color:#2c7a2c">Rotated ({len(rotated)})</h3>
<table style="border-collapse:collapse;width:100%">
  <tr>
    <th {th}>Name</th><th {th}>App ID</th><th {th}>New Expiry</th>
    <th {th}>Key Vault</th><th {th}>Previous Expiry</th>
  </tr>
  {rotated_rows}
</table>

<h3>Skipped – not expiring soon ({len(skipped)})</h3>
<table style="border-collapse:collapse;width:100%">
  <tr>
    <th {th}>Name</th><th {th}>App ID</th><th {th}>Current Expiry</th>
    <th {th} colspan="2">Reason</th>
  </tr>
  {skipped_rows}
</table>

<h3 style="color:#c0392b">Failed ({len(failed)})</h3>
<table style="border-collapse:collapse;width:100%">
  <tr>
    <th {th}>Name</th><th {th}>App ID</th><th {th} colspan="3">Error</th>
  </tr>
  {failed_rows}
</table>
</body></html>"""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_report(self, results: list[RotationResult]) -> None:
        run_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        plain = self._build_plain(results, run_ts)
        html = self._build_html(results, run_ts)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[SP Rotation] Report – {run_ts}"
        msg["From"] = self._cfg.from_address
        msg["To"] = ", ".join(self._cfg.to_addresses)
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        password = self._fetch_smtp_password()
        with smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(self._cfg.smtp_user, password)
            server.sendmail(self._cfg.from_address, self._cfg.to_addresses, msg.as_string())
