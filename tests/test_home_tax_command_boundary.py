"""Behavioral regressions for the public HomeTax command boundary."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from accounting_information_platform.accept import accept_home_tax_submission
from accounting_information_platform.core import AccountingValidationError


_TENANT_REFERENCE = "urn:cwl:tenant_test"
_ENTITY_REFERENCE = "urn:cwl:legal_entity:TEST"
_BOOK_REFERENCE = "urn:cwl:accounting_book:TEST"
_PERIOD_REFERENCE = "urn:cwl:accounting:fiscal_period:2026-08"
_COMMAND_KEY = "urn:cwl:home_tax_submission:test:2026-08:v1"


def _loadable_register() -> dict[str, object]:
    """Return one complete VAT register document accepted by the command boundary."""
    return {
        "tenant_reference": _TENANT_REFERENCE,
        "legal_entity_reference": _ENTITY_REFERENCE,
        "accounting_book_reference": _BOOK_REFERENCE,
        "book_reference": _BOOK_REFERENCE,
        "fiscal_period_reference": _PERIOD_REFERENCE,
        "as_of_date": "2026-08-31",
        "chart_account_code": "210100",
        "account_role_code": "tax_payable",
        "issued_amount": "2500",
        "voided_amount": "0",
        "closing_amount": "2500",
    }


class _RecordingLedger:
    """Minimal command collaborator that records persistence arguments without a database."""

    instances: list["_RecordingLedger"] = []

    def __init__(self, _database_url: str, tenant_reference: str) -> None:
        self.tenant_reference = tenant_reference
        self.persist_kwargs: dict[str, object] | None = None
        type(self).instances.append(self)

    def load_vat_period_register(
        self, _entity_reference: str, _book_reference: str, _period_code: str
    ) -> dict[str, object]:
        """Return deterministic, complete VAT evidence for the command under test."""
        return _loadable_register()

    def persist_home_tax_submission(self, **kwargs: object) -> dict[str, object]:
        """Capture the durable command identity passed by the acceptance layer."""
        self.persist_kwargs = dict(kwargs)
        return {
            "tenant_reference": self.tenant_reference,
            "submission_status_code": "rejected",
            "rejection_reason_code": str(kwargs["rejection_reason_code"]),
        }


class HomeTaxCommandBoundaryTests(unittest.TestCase):
    """Require an explicit retry identity before HomeTax scope or persistence work."""

    def setUp(self) -> None:
        _RecordingLedger.instances.clear()

    @staticmethod
    def _payload(*, include_key: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "tenant_reference": _TENANT_REFERENCE,
            "legal_entity_reference": _ENTITY_REFERENCE,
            "book_reference": _BOOK_REFERENCE,
            "fiscal_period_reference": _PERIOD_REFERENCE,
        }
        if include_key:
            payload["idempotency_key"] = _COMMAND_KEY
        return payload

    def test_missing_idempotency_key_fails_before_ledger_work(self) -> None:
        """A write attempt without command identity fails before reading the VAT register."""
        with mock.patch(
            "accounting_information_platform.accept.PostgresPostingLedger",
            _RecordingLedger,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
                accept_home_tax_submission(
                    self._payload(include_key=False),
                    "postgresql://unused",
                    _TENANT_REFERENCE,
                )
        self.assertEqual(_RecordingLedger.instances, [])

    def test_empty_idempotency_key_fails_before_ledger_work(self) -> None:
        """An empty command identity is rejected before loading statutory evidence."""
        payload = self._payload(include_key=True)
        payload["idempotency_key"] = ""
        with mock.patch(
            "accounting_information_platform.accept.PostgresPostingLedger",
            _RecordingLedger,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
                accept_home_tax_submission(payload, "postgresql://unused", _TENANT_REFERENCE)
        self.assertEqual(_RecordingLedger.instances, [])

    def test_command_passes_exact_idempotency_key_to_durable_boundary(self) -> None:
        """A valid command carries the unchanged tenant-scoped key into persistence."""
        with mock.patch(
            "accounting_information_platform.accept.PostgresPostingLedger",
            _RecordingLedger,
        ), mock.patch.dict(os.environ, {"ACCOUNTING_HOMETAX_CREDENTIAL": "test-only"}):
            document = accept_home_tax_submission(
                self._payload(include_key=True),
                "postgresql://unused",
                _TENANT_REFERENCE,
            )

        self.assertEqual(document["submission_status_code"], "rejected")
        self.assertEqual(document["rejection_reason_code"], "hometax_transport_unavailable")
        self.assertEqual(len(_RecordingLedger.instances), 1)
        persist_kwargs = _RecordingLedger.instances[0].persist_kwargs
        self.assertIsNotNone(persist_kwargs)
        assert persist_kwargs is not None
        self.assertEqual(persist_kwargs.get("submission_idempotency_key"), _COMMAND_KEY)
        self.assertEqual(persist_kwargs.get("register_document"), _loadable_register())


if __name__ == "__main__":
    unittest.main()
