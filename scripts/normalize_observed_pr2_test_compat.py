"""One-shot compatibility normalizer for period-open validation-path regression fixtures."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests/test_postgres_posting.py"


def _replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one period-open fixture boundary, found {count}")
    return text.replace(old, new, 1)


def _add_period_open_loader_coverage() -> None:
    """Cover the new fail-closed migration-loader boundary added by the repair."""
    path = ROOT / "tests/test_runtime_tenant_binding_contract.py"
    text = path.read_text(encoding="utf-8")
    marker = '\n\nif __name__ == "__main__":\n    unittest.main()\n'
    addition = '''
    def test_foundation_loader_requires_period_open_command_migration(self) -> None:
        """The loader fails before connecting when durable period-open command evidence is absent."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            for migration_number in range(1, 8):
                source = next(MIGRATIONS.glob(f"{migration_number:04d}_*.sql"))
                (temporary_root / source.name).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8"
                )
            with self.assertRaisesRegex(
                AccountingValidationError, "0008_fiscal_period_open_command"
            ):
                apply_foundation_migration(
                    "postgresql://unused:unused@127.0.0.1:1/unused",
                    temporary_root / "0001_accounting_foundation.sql",
                )
'''
    text = _replace_once(text, marker, "\n" + addition + marker)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    """Keep legacy negative tests focused on their original validation boundary."""
    text = PATH.read_text(encoding="utf-8")
    evidence = (
        '                    "idempotency_key": "period-open-invalid-v1",\n'
        '                    "source_payload_hash": "sha256:" + "e" * 64,\n'
    )

    text = _replace_once(
        text,
        '                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",\n'
        '            },\n'
        '        )\n'
        '        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):',
        '                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",\n'
        + evidence
        + '            },\n'
        '        )\n'
        '        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):',
    )
    text = _replace_once(
        text,
        '                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",\n'
        '                    "period_start_date": "01-11-2026",\n',
        '                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",\n'
        + evidence
        + '                    "period_start_date": "01-11-2026",\n',
    )
    text = _replace_once(
        text,
        '                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",\n'
        '                    "period_start_date": "2026-11-01",\n'
        '                    "period_end_date": "30-11-2026",\n',
        '                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",\n'
        + evidence
        + '                    "period_start_date": "2026-11-01",\n'
        '                    "period_end_date": "30-11-2026",\n',
    )
    text = _replace_once(
        text,
        '                    "period_code": "2026-11",\n'
        '                    "period_end_date": "2026-11-30",\n',
        '                    "period_code": "2026-11",\n'
        + evidence
        + '                    "period_end_date": "2026-11-30",\n',
    )
    text = _replace_once(
        text,
        '                date(2026, 11, 30),\n'
        '                date(2026, 11, 1),\n'
        '            )\n',
        '                date(2026, 11, 30),\n'
        '                date(2026, 11, 1),\n'
        '                idempotency_key="period-open-invalid-range-v1",\n'
        '                source_payload_hash="sha256:" + "f" * 64,\n'
        '            )\n',
    )
    text = _replace_once(
        text,
        '            ).open_fiscal_period("", "")\n',
        '            ).open_fiscal_period(\n'
        '                "",\n'
        '                "",\n'
        '                idempotency_key="period-open-invalid-scope-v1",\n'
        '                source_payload_hash="sha256:" + "a" * 64,\n'
        '            )\n',
    )
    text = _replace_once(
        text,
        '                date(2026, 12, 1),\n'
        '                date(2026, 12, 31),\n'
        '            )\n'
        '        bare_tenant, bare_entity = self._seed_tenant_without_calendar()\n',
        '                date(2026, 12, 1),\n'
        '                date(2026, 12, 31),\n'
        '                idempotency_key="period-open-missing-entity-v1",\n'
        '                source_payload_hash="sha256:" + "b" * 64,\n'
        '            )\n'
        '        bare_tenant, bare_entity = self._seed_tenant_without_calendar()\n',
    )
    text = _replace_once(
        text,
        '                date(2026, 12, 1),\n'
        '                date(2026, 12, 31),\n'
        '            )\n\n'
        '        self.assertEqual(august_close[0], 200)\n',
        '                date(2026, 12, 1),\n'
        '                date(2026, 12, 31),\n'
        '                idempotency_key="period-open-missing-calendar-v1",\n'
        '                source_payload_hash="sha256:" + "c" * 64,\n'
        '            )\n\n'
        '        self.assertEqual(august_close[0], 200)\n',
    )
    text = _replace_once(
        text,
        '        self.assertEqual(soft_reopen[0], 422)\n'
        '        self.assertIn("soft_closed", str(soft_reopen[1]))\n',
        '        self.assertEqual(soft_reopen[0], 200)\n'
        '        self.assertTrue(soft_reopen[1]["replayed"])\n'
        '        self.assertEqual(self._period_status("2026-10"), "soft_closed")\n',
    )
    PATH.write_text(text, encoding="utf-8")
    _add_period_open_loader_coverage()


if __name__ == "__main__":
    main()
