"""Align runtime-login regression with purpose-limited fiscal-period privileges."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    """Remove the obsolete assertion that ordinary runtime may update fiscal periods."""
    path = Path("tests/test_postgres_posting.py")
    text = path.read_text(encoding="utf-8")
    start_marker = "            own_period_write = connection.execute(\n"
    end_marker = '\n\n        self._set_period_status("soft_closed")\n'
    start = text.find(start_marker)
    if start < 0:
        if "test_real_runtime_login_is_tenant_bound_and_cannot_bypass_controls" in text:
            return
        raise SystemExit("runtime fiscal-period UPDATE regression anchor drifted")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit("runtime fiscal-period UPDATE regression end anchor drifted")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


if __name__ == "__main__":
    main()
