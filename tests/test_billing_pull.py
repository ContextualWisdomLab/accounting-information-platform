"""Billing pull rejection tokens stay stable and do not invent a Billing status."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.billing_pull import (
    JournalProposalPage,
    accept_billing_proposal_pull,
    _rejected_proposal,
    _rejection_reason_code,
)

_TENANT = "urn:cwl:tenant_001"
_BILLING_ORIGIN = "https://billing.example.test"
_DR_ORIGIN = "https://billing-dr.example.test"


def _pull_payload(billing_base_url: object | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"tenant_reference": _TENANT}
    if billing_base_url is not None:
        payload["billing_base_url"] = billing_base_url
    return payload


def _accept_pull(payload: dict[str, object]) -> dict[str, object]:
    return accept_billing_proposal_pull(payload, "postgresql://unused", _TENANT)


class BillingPullRejectionReasonTests(unittest.TestCase):
    """Map operator validation sentences to stable snake_case reason codes."""

    def test_maps_known_messages_and_defaults_unknown_text(self) -> None:
        """Each advertised token is derived from the existing error sentence."""
        cases = (
            (
                "Account role contract_liability is not mapped on this book.",
                "unknown_account_role",
            ),
            (
                "No account_role_mapping is effective for this book and accounting date.",
                "unknown_account_role",
            ),
            ("journal proposal must balance", "unbalanced_journal"),
            (
                "accounting date belongs to a closed fiscal period",
                "closed_period",
            ),
            (
                "Fiscal period 2026-08 is soft_closed. Open that period or post into an open period",
                "closed_period",
            ),
            ("Fiscal period 2026-08 is hard_closed.", "closed_period"),
            (
                "pull tenant_reference does not match the bound tenant.",
                "cross_tenant",
            ),
            (
                "debit_amount must be a canonical decimal string.",
                "invalid_amount",
            ),
            (
                "amount must be a canonical non-negative decimal",
                "invalid_amount",
            ),
            ("line number must be positive", "proposal_validation_failed"),
        )
        for message, reason_code in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    _rejection_reason_code(AccountingValidationError(message)),
                    reason_code,
                )

    def test_rejected_proposal_uses_billing_identity_and_next_action(self) -> None:
        """Rejected rows keep Billing identity and the operator next-action sentence."""
        error = AccountingValidationError(
            "Account role contract_liability is not mapped on this book. "
            "Create the account_role_mapping row, then retry posting."
        )
        document = _rejected_proposal(
            {
                "proposal_id": "019d7b92-6ff5-7a7f-b61c-962c0f4bf619",
                "idempotency_key": "urn:cwl:tenant_001:invoice_draft:x",
            },
            error,
        )
        missing_identity = _rejected_proposal({}, error)

        self.assertEqual(
            document,
            {
                "proposal_id": "019d7b92-6ff5-7a7f-b61c-962c0f4bf619",
                "idempotency_key": "urn:cwl:tenant_001:invoice_draft:x",
                "rejection_reason_code": "unknown_account_role",
                "rejection_message": str(error),
            },
        )
        self.assertEqual(missing_identity["proposal_id"], "")
        self.assertEqual(missing_identity["idempotency_key"], "")


class BillingPullOriginAllowlistTests(unittest.TestCase):
    """POST /billing-proposal-pulls trusts env origins, not a request-body host."""

    def test_body_origin_off_allowlist_does_not_fetch(self) -> None:
        """A client cannot point AIS at loopback, link-local, or an unknown host."""
        attackers = (
            "http://127.0.0.1:9",
            "http://localhost:9",
            "http://169.254.169.254/",
            "http://[fe80::1]:9",
            "https://internal.example.test",
            "file:///tmp/billing",
        )
        env = {
            "BILLING_BASE_URL": _BILLING_ORIGIN,
            "BILLING_ALLOWED_ORIGINS": _DR_ORIGIN,
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch(
                "accounting_information_platform.billing_pull.pull_validated_journal_proposals"
            ) as pull:
                for attacker in attackers:
                    with self.subTest(attacker=attacker):
                        pull.reset_mock()
                        if attacker.startswith("file:"):
                            pattern = "http or https origin"
                        else:
                            pattern = "allowed Billing origin"
                        with self.assertRaisesRegex(AccountingValidationError, pattern):
                            _accept_pull(_pull_payload(attacker))
                        pull.assert_not_called()

    def test_empty_env_does_not_trust_request_body_origin(self) -> None:
        """A body URL is not an origin of last resort when BILLING_BASE_URL is unset."""
        with mock.patch.dict(
            os.environ, {"BILLING_BASE_URL": "", "BILLING_ALLOWED_ORIGINS": ""}, clear=False
        ):
            with mock.patch(
                "accounting_information_platform.billing_pull.pull_validated_journal_proposals"
            ) as pull:
                with self.assertRaisesRegex(AccountingValidationError, "Set BILLING_BASE_URL"):
                    _accept_pull(_pull_payload("http://127.0.0.1:9"))
                pull.assert_not_called()

    def test_omitted_body_uses_env_origin_and_matching_body_replays_it(self) -> None:
        """Omit uses BILLING_BASE_URL; a matching body still fetches that allowlisted origin."""
        empty_page = JournalProposalPage((), None)
        env = {
            "BILLING_BASE_URL": f"{_BILLING_ORIGIN}/",
            "BILLING_ALLOWED_ORIGINS": f" {_DR_ORIGIN} ",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch(
                "accounting_information_platform.billing_pull.pull_validated_journal_proposals",
                return_value=empty_page,
            ) as pull:
                omitted = _accept_pull(_pull_payload())
                matching = _accept_pull(_pull_payload(f"{_BILLING_ORIGIN}/v1"))
                extra = _accept_pull(_pull_payload(_DR_ORIGIN))
                loopback_env = dict(env)
                loopback_env["BILLING_BASE_URL"] = "http://127.0.0.1:18765"
                with mock.patch.dict(os.environ, loopback_env, clear=False):
                    allowlisted_loopback = _accept_pull(
                        _pull_payload("http://127.0.0.1:18765")
                    )

        self.assertEqual(omitted, {"posting_receipts": [], "rejected_proposals": []})
        self.assertEqual(matching, omitted)
        self.assertEqual(extra, omitted)
        self.assertEqual(allowlisted_loopback, omitted)
        self.assertEqual(
            [call.args[0] for call in pull.call_args_list],
            [
                _BILLING_ORIGIN,
                _BILLING_ORIGIN,
                _DR_ORIGIN,
                "http://127.0.0.1:18765",
            ],
        )

    def test_allowlist_normalizes_default_ports_and_ipv6(self) -> None:
        """Default ports and an explicitly allowlisted IPv6 origin still fetch."""
        empty_page = JournalProposalPage((), None)
        with mock.patch(
            "accounting_information_platform.billing_pull.pull_validated_journal_proposals",
            return_value=empty_page,
        ) as pull:
            with mock.patch.dict(
                os.environ,
                {
                    "BILLING_BASE_URL": _BILLING_ORIGIN,
                    "BILLING_ALLOWED_ORIGINS": f",{_BILLING_ORIGIN},,{_DR_ORIGIN},",
                },
                clear=False,
            ):
                _accept_pull(_pull_payload(""))
                _accept_pull(_pull_payload("https://BILLING.EXAMPLE.TEST:443"))
                _accept_pull(_pull_payload("https://billing-dr.example.test:443"))
            with mock.patch.dict(
                os.environ,
                {
                    "BILLING_BASE_URL": "http://billing.example.test",
                    "BILLING_ALLOWED_ORIGINS": "http://[fe80::1]:18765",
                },
                clear=False,
            ):
                _accept_pull(_pull_payload("http://billing.example.test:80"))
                _accept_pull(_pull_payload("http://[fe80::1]:18765"))

        self.assertEqual(
            [call.args[0] for call in pull.call_args_list],
            [
                _BILLING_ORIGIN,
                _BILLING_ORIGIN,
                _DR_ORIGIN,
                "http://billing.example.test",
                "http://[fe80::1]:18765",
            ],
        )


if __name__ == "__main__":
    unittest.main()
