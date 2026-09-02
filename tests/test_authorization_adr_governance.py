"""Governance contracts for the purpose-bound authorization decision record."""

from __future__ import annotations

from pathlib import Path
import unittest


ADR = Path("docs/adr/0064-purpose-bound-authorization.md")
COLLIDING_PREDECESSOR = Path("docs/adr/0055-purpose-bound-authorization.md")


class AuthorizationAdrGovernanceTests(unittest.TestCase):
    """Keep the authorization ADR uniquely numbered and unaccepted before integration."""

    def test_authorization_adr_has_unique_number_and_proposed_status(self) -> None:
        """Concurrent ADR numbering and Draft evidence cannot create a false Accepted decision."""
        self.assertTrue(ADR.is_file(), "purpose-bound authorization must own ADR 0064")
        self.assertFalse(
            COLLIDING_PREDECESSOR.exists(),
            "ADR 0055 is owned by the reconciliation dependency root",
        )
        text = ADR.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# ADR 0064: Purpose-bound application authorization"))
        self.assertIn("## Status\n\nProposed", text)
        self.assertNotIn("## Status\n\nAccepted", text)


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
