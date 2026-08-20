"""HTTP boundary regressions for request-body framing and HomeTax conflicts."""

from __future__ import annotations

import io
import unittest
from email.message import Message
from unittest import mock

from accounting_information_platform import IdempotencyConflictError
from accounting_information_platform.http_api import JournalProposalHandler


class _BodyHarness:
    """Minimal request-handler state for exercising ``_read_body`` directly."""

    def __init__(self, headers: Message, body: bytes = b"") -> None:
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.errors: list[tuple[int, str]] = []

    def _write_error(self, status_code: int, error_message: str) -> None:
        self.errors.append((status_code, error_message))


class HttpBodyFramingContractTests(unittest.TestCase):
    """Reject missing, duplicated, or malformed Content-Length before body parsing."""

    def _read(self, values: tuple[str, ...], body: bytes = b"{}") -> tuple[bytes | None, list[tuple[int, str]]]:
        headers = Message()
        for value in values:
            headers.add_header("Content-Length", value)
        harness = _BodyHarness(headers, body)
        result = JournalProposalHandler._read_body(harness)  # type: ignore[arg-type]
        return result, harness.errors

    def test_missing_content_length_is_400(self) -> None:
        """A POST without framing identity is a client error, not an empty JSON body."""
        body, errors = self._read(())
        self.assertIsNone(body)
        self.assertEqual(errors[0][0], 400)
        self.assertIn("Content-Length", errors[0][1])

    def test_duplicate_content_length_is_400(self) -> None:
        """Two Content-Length fields are ambiguous even when their values match."""
        body, errors = self._read(("2", "2"))
        self.assertIsNone(body)
        self.assertEqual(errors[0][0], 400)
        self.assertIn("Content-Length", errors[0][1])

    def test_non_ascii_decimal_content_length_is_400(self) -> None:
        """Signs, whitespace, underscores, and non-ASCII digits never become a length."""
        for value in ("+2", " 2", "2 ", "2_0", "٢"):
            with self.subTest(value=value):
                body, errors = self._read((value,))
                self.assertIsNone(body)
                self.assertEqual(errors[0][0], 400)

    def test_single_ascii_decimal_length_reads_exact_body(self) -> None:
        """One valid ASCII decimal Content-Length preserves the existing body read."""
        body, errors = self._read(("2",), b"{}tail")
        self.assertEqual(body, b"{}")
        self.assertEqual(errors, [])


class HomeTaxConflictHttpContractTests(unittest.TestCase):
    """Changed HomeTax evidence returns conflict rather than not-found."""

    def test_home_tax_idempotency_conflict_is_409(self) -> None:
        """The specialized idempotency conflict must be mapped before generic validation."""
        handler = object.__new__(JournalProposalHandler)
        handler.server = mock.Mock(database_url="postgresql://unused")
        handler._bound_tenant_header = mock.Mock(return_value="urn:cwl:tenant_001")  # type: ignore[method-assign]
        handler._read_json_object = mock.Mock(  # type: ignore[method-assign]
            return_value={"tenant_reference": "urn:cwl:tenant_001"}
        )
        handler._write_error = mock.Mock()  # type: ignore[method-assign]
        handler._write_json = mock.Mock()  # type: ignore[method-assign]
        with mock.patch(
            "accounting_information_platform.http_api.accept_home_tax_submission",
            side_effect=IdempotencyConflictError("changed HomeTax command"),
        ):
            JournalProposalHandler._post_home_tax_submission(handler, b"{}")
        handler._write_error.assert_called_once()
        self.assertEqual(handler._write_error.call_args.args[0], 409)
        handler._write_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
