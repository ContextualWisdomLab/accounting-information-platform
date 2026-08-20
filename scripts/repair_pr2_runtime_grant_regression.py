"""Align runtime-login regression and docs with purpose-limited DB privileges."""

from __future__ import annotations

from pathlib import Path


def remove_obsolete_period_update_assertion() -> None:
    """Remove the stale test claim that ordinary runtime may update fiscal periods."""
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


def align_runtime_tenant_documentation() -> None:
    """Replace the legacy request-controlled tenant-GUC instruction for runtime logins."""
    path = Path("docs/OPERABILITY.md")
    text = path.read_text(encoding="utf-8")
    stale = (
        "The request/session boundary must set `app.tenant_account_id` from validated "
        "tenant authority before tenant-scoped SQL."
    )
    current = (
        "Application runtime tenant authority is resolved from the immutable PostgreSQL "
        "`session_user` through `runtime_tenant_binding`; only non-runtime administrator/test "
        "compatibility sessions use `app.tenant_account_id`."
    )
    if stale in text:
        text = text.replace(stale, current, 1)
    elif current not in text:
        raise SystemExit("runtime tenant-authority documentation anchor drifted")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    """Converge runtime regression evidence and operator guidance on least privilege."""
    remove_obsolete_period_update_assertion()
    align_runtime_tenant_documentation()


if __name__ == "__main__":
    main()
