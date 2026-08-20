"""Security regressions for the standalone accounting HTTP listener."""

from __future__ import annotations

import unittest

from accounting_information_platform.http_api import run_journal_proposal_server


class ServerBindSecurityTests(unittest.TestCase):
    """Keep the unauthenticated stdlib listener private by default."""

    def test_runner_defaults_to_loopback_without_explicit_host(self) -> None:
        """Omitting host must never expose accounting commands on all interfaces."""
        server = run_journal_proposal_server(
            database_url="postgresql://postgres:postgres@127.0.0.1:5432/accounting_test",
            tenant_reference="urn:cwl:tenant:bind_security_test",
            port=0,
            serve=lambda: None,
        )
        self.addCleanup(server.server_close)

        self.assertEqual(server.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
