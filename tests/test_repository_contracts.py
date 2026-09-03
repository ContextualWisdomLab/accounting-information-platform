"""Repository-level contract and supply-chain tests."""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

from scripts.validate_repository import (
    APPEND_ONLY_JOURNAL_MUTATION_ERROR,
    COVERAGE_CP313_MANYLINUX_X86_64_WHEEL_HASH,
    COVERAGE_UNIVERSAL_WHEEL_HASH,
    PSYCOPG_BINARY_CP314_MANYLINUX_X86_64_WHEEL_HASH,
    main,
    find_mutable_action_references,
    find_placeholder_tokens,
    validate_append_only_journal_sql,
    validate_public_docstrings,
    validate_quality_requirements,
    validate_repository,
    validate_sql_object_names,
    _iter_contract_files,
)


ROOT = Path(__file__).resolve().parents[1]
IGNORE_PATTERNS = shutil.ignore_patterns(
    ".git",
    ".venv",
    ".codegraph",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "*.sock",
)


class RepositoryContractTests(unittest.TestCase):
    """Verify checked-in architecture, SQL, schemas, and CI contracts."""

    def test_repository_contracts_are_valid(self) -> None:
        """The complete initial repository must satisfy every deterministic gate."""
        self.assertEqual(validate_repository(ROOT), ())

    def test_billing_pull_opens_https_with_default_ssl_context(self) -> None:
        """Billing HTTPS must verify certificates without HTTPSConnection or urllib."""
        source = (
            ROOT / "src" / "accounting_information_platform" / "billing_pull.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("HTTPSConnection", source)
        self.assertNotIn("urlopen", source)
        self.assertIn("create_default_context", source)

    def test_missing_files_are_reported(self) -> None:
        """A partial checkout produces an actionable missing-file error."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            errors = validate_repository(Path(temporary_directory))
        self.assertIn("missing required file: README.md", errors)

    def test_generated_directories_are_not_treated_as_contracts(self) -> None:
        """Local virtualenvs, indexes, and build output cannot fail source validation."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "tracked.md").write_text("TODO", encoding="utf-8")
            for directory in (".venv", ".codegraph", "build", "dist"):
                generated = root / directory
                generated.mkdir()
                (generated / "generated.md").write_text("TODO", encoding="utf-8")

            self.assertEqual(_iter_contract_files(root), (root / "tracked.md",))

    def test_mutable_action_reference_is_rejected(self) -> None:
        """Mutable action tags cannot represent exact-head evidence."""
        self.assertEqual(
            find_mutable_action_references("- uses: actions/checkout@v4\n"),
            ("actions/checkout@v4",),
        )
        self.assertEqual(
            find_mutable_action_references(
                "- uses: actions/checkout@631c942040754b6e095e929c1677c07e10ed4f87\n"
            ),
            (),
        )

    def test_single_word_sql_objects_are_rejected(self) -> None:
        """Schemas, tables, and columns require two-word snake_case names."""
        sql = """
        CREATE SCHEMA finance;
        CREATE TABLE finance.journal (
            journal_id uuid,
            status text
        );
        """
        self.assertEqual(
            validate_sql_object_names(sql),
            (
                "schema name must contain at least two snake_case words: finance",
                "table name must contain at least two snake_case words: journal",
                "column name must contain at least two snake_case words: status",
            ),
        )

    def test_single_word_policy_and_function_names_are_rejected(self) -> None:
        """Policies and functions require two-word snake_case names."""
        sql = """
        CREATE POLICY isolation ON accounting_core.general_journal
            USING (true);
        CREATE POLICY IF NOT EXISTS tenant ON accounting_core.journal_entry_line
            USING (true);
        CREATE FUNCTION helper() RETURNS uuid LANGUAGE sql AS $$ SELECT uuidv7(); $$;
        CREATE OR REPLACE FUNCTION accounting_core.guard() RETURNS uuid LANGUAGE sql AS $$ SELECT uuidv7(); $$;
        """
        self.assertEqual(
            validate_sql_object_names(sql),
            (
                "policy name must contain at least two snake_case words: isolation",
                "policy name must contain at least two snake_case words: tenant",
                "function name must contain at least two snake_case words: helper",
                "function name must contain at least two snake_case words: guard",
            ),
        )

    def test_two_word_policy_and_function_names_are_accepted(self) -> None:
        """Valid two-word policy and function names still pass the naming gate."""
        sql = """
        CREATE POLICY tenant_isolation ON accounting_core.general_journal
            USING (true);
        CREATE POLICY IF NOT EXISTS journal_entry_isolation
            ON accounting_core.journal_entry_line
            USING (true);
        CREATE FUNCTION current_tenant_id() RETURNS uuid LANGUAGE sql AS $$ SELECT uuidv7(); $$;
        CREATE OR REPLACE FUNCTION accounting_core.current_tenant_account_id() RETURNS uuid LANGUAGE sql AS $$ SELECT uuidv7(); $$;
        """
        self.assertEqual(validate_sql_object_names(sql), ())

    def test_placeholders_are_rejected(self) -> None:
        """Accepted architecture and plans cannot retain unresolved placeholders."""
        self.assertEqual(
            find_placeholder_tokens("Complete.\nTO" + "DO: later.\n"),
            ("TODO",),
        )

    def test_schema_authority_and_status_contracts_are_explicit(self) -> None:
        """The platform consumes proposals and owns only posting receipts."""
        proposal = self._schema("accounting-journal-proposal.schema.json")
        receipt = self._schema("accounting-posting-receipt.schema.json")
        policy = self._schema("accounting-policy-manifest.schema.json")

        self.assertEqual(proposal["x-cwl-authority"], "metering-billing-platform")
        self.assertEqual(receipt["x-cwl-authority"], "accounting-information-platform")
        self.assertEqual(policy["x-cwl-authority"], "accounting-information-platform")
        self.assertEqual(
            policy["properties"]["account_mappings"]["x-cwl-unique-items-by"],
            "account_role_code",
        )
        self.assertNotIn("proposal_status_code", proposal["required"])
        self.assertNotIn("proposal_status_code", proposal["properties"])
        self.assertIn("proposal_status", proposal["required"])
        self.assertEqual(
            set(proposal["properties"]["proposal_status"]["enum"]),
            {"draft", "validated", "exported", "rejected"},
        )
        self.assertNotIn("posted", proposal["properties"]["proposal_status"]["enum"])
        self.assertEqual(
            set(receipt["properties"]["posting_status_code"]["enum"]),
            {"posted", "held", "rejected", "reversed"},
        )

    def test_proposal_schema_forbids_retained_earnings_role(self) -> None:
        """Billing proposal contract cannot claim AIS close-only retained_earnings."""
        proposal = self._schema("accounting-journal-proposal.schema.json")
        account_role = proposal["$defs"]["journal_line"]["properties"][
            "account_role_code"
        ]
        self.assertEqual(account_role["type"], "string")
        self.assertEqual(account_role["pattern"], "^[a-z][a-z0-9_]{1,63}$")
        self.assertEqual(account_role["not"], {"const": "retained_earnings"})

    def test_proposal_schema_requires_uuid_proposal_id(self) -> None:
        """Billing proposal_id cannot include a colon or :reversal suffix."""
        proposal = self._schema("accounting-journal-proposal.schema.json")
        proposal_id = proposal["properties"]["proposal_id"]
        self.assertEqual(proposal_id["type"], "string")
        self.assertEqual(proposal_id["format"], "uuid")
        self.assertEqual(
            proposal_id["pattern"],
            "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        )

    def test_proposal_schema_rejects_more_than_six_fractional_digits(self) -> None:
        """positive_decimal cannot outrun journal_entry_line numeric(38, 6)."""
        proposal = self._schema("accounting-journal-proposal.schema.json")
        positive = proposal["$defs"]["positive_decimal"]
        non_negative = proposal["$defs"]["non_negative_decimal"]
        scale = "^(0|[1-9][0-9]*)(\\.[0-9]{1,6})?$"
        zero = re.compile("^0+(\\.0+)?$")
        amount = re.compile(scale)

        self.assertEqual(positive["type"], "string")
        self.assertEqual(positive["pattern"], scale)
        self.assertEqual(positive["not"], {"pattern": "^0+(\\.0+)?$"})
        self.assertEqual(non_negative["pattern"], scale)
        accepted = ("1", "25000", "25000.50", "0.5", "0.50", "0.000001")
        rejected = ("0.0000010", "0.1234567", "1.1234567", "0", "0.0", "0.000000")
        for value in accepted:
            with self.subTest(accepted=value):
                self.assertIsNotNone(amount.fullmatch(value))
                self.assertIsNone(zero.fullmatch(value))
        for value in rejected:
            with self.subTest(rejected=value):
                self.assertTrue(
                    amount.fullmatch(value) is None or zero.fullmatch(value) is not None
                )

    def test_repository_reports_integrated_violations(self) -> None:
        """The aggregate validator reports mutable CI, placeholders, and SQL drift."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_root = Path(temporary_directory) / "repository"
            shutil.copytree(ROOT, copied_root, ignore=IGNORE_PATTERNS)
            readme = copied_root / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "\nTO" + "DO: later.\n",
                encoding="utf-8",
            )
            workflow = copied_root / ".github/workflows/ci.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8")
                + "\n# uses: actions/checkout@v4\n",
                encoding="utf-8",
            )
            migration = copied_root / "database/migrations/0001_accounting_foundation.sql"
            migration.write_text(
                migration.read_text(encoding="utf-8")
                + "\nCREATE TABLE accounting_core.bad (\n    status text\n);\n",
                encoding="utf-8",
            )
            errors = validate_repository(copied_root)
        self.assertIn("unresolved placeholder in README.md: TODO", errors)
        self.assertIn(
            "mutable GitHub Action reference in .github/workflows/ci.yml: actions/checkout@v4",
            errors,
        )
        self.assertIn(
            "table name must contain at least two snake_case words: bad", errors
        )
        self.assertIn(
            "column name must contain at least two snake_case words: status", errors
        )

    def test_schema_metadata_failures_are_reported(self) -> None:
        """Malformed or open schema roots fail closed with stable diagnostics."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            schemas = root / "schemas"
            schemas.mkdir()
            (schemas / "invalid.schema.json").write_text("{", encoding="utf-8")
            (schemas / "open.schema.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://example.invalid/draft",
                        "$id": "http://schemas.invalid/open",
                        "type": "array",
                        "additionalProperties": True,
                    }
                ),
                encoding="utf-8",
            )
            errors = validate_repository(root)
        joined = "\n".join(errors)
        self.assertIn("invalid JSON in invalid.schema.json", joined)
        self.assertIn("schema must declare Draft 2020-12: open.schema.json", errors)
        self.assertIn("schema must have an HTTPS $id: open.schema.json", errors)
        self.assertIn("schema root must be an object: open.schema.json", errors)
        self.assertIn(
            "schema root must reject additional properties: open.schema.json", errors
        )

    def test_public_docstring_contract_rejects_undocumented_symbols(self) -> None:
        """Every shipped public module, class, method, and function is documented."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory) / "src"
            package = source_root / "sample_package"
            package.mkdir(parents=True)
            (package / "module.py").write_text(
                '"""Documented module."""\n\ndef undocumented():\n    return 1\n',
                encoding="utf-8",
            )
            (package / "missing_module_doc.py").write_text(
                'def documented():\n    """Documented function."""\n    return 1\n',
                encoding="utf-8",
            )
            errors = validate_public_docstrings(source_root)
            missing_root_errors = validate_public_docstrings(
                Path(temporary_directory) / "absent"
            )
        self.assertEqual(
            errors,
            (
                "missing module docstring: sample_package/missing_module_doc.py",
                "missing public docstring: sample_package/module.py:undocumented",
            ),
        )
        self.assertEqual(missing_root_errors, ())

    def test_quality_requirements_require_ci_coverage_wheels_and_packaging_backend(
        self,
    ) -> None:
        """Exact-head CI cannot resolve an unhashed native coverage wheel or missing backend."""
        unhashed = validate_quality_requirements("coverage==7.15.4\n")
        self.assertIn("quality dependencies must be hash locked", unhashed)
        self.assertIn(
            "coverage must pin the universal py3-none-any wheel hash", unhashed
        )
        self.assertIn(
            "coverage must pin the CPython 3.13 manylinux x86_64 wheel hash",
            unhashed,
        )
        self.assertIn(
            "quality dependencies must pin setuptools for no-build-isolation packaging",
            unhashed,
        )
        self.assertIn(
            "quality dependencies must pin wheel for no-build-isolation packaging",
            unhashed,
        )
        self.assertIn(
            "quality dependencies must pin packaging for setuptools license metadata",
            unhashed,
        )
        self.assertIn(
            "quality dependencies must pin psycopg for PostgreSQL persistence tests",
            unhashed,
        )
        self.assertIn(
            "quality dependencies must pin psycopg-binary for PostgreSQL persistence tests",
            unhashed,
        )

        universal_only = validate_quality_requirements(
            "coverage==7.15.4 \\\n"
            f"    --hash=sha256:{COVERAGE_UNIVERSAL_WHEEL_HASH}\n"
            "setuptools==84.0.0 \\\n"
            "    --hash=sha256:"
            "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670\n"
            "wheel==0.48.0 \\\n"
            "    --hash=sha256:"
            "3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab\n"
            "packaging==26.3 \\\n"
            "    --hash=sha256:"
            "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c\n"
            "psycopg==3.3.4 \\\n"
            "    --hash=sha256:"
            "b6bbc25ccf05c8fad3b061d9db2ef0909a555171b84b07f29458a447253d679a\n"
            "psycopg-binary==3.3.4 \\\n"
            "    --hash=sha256:"
            "c677c4ad433cb7150c8cd304a0769ae3bcfbe5ea0676eb53faa7b1443b16d0d3\n"
        )
        self.assertEqual(
            universal_only,
            (
                "coverage must pin the CPython 3.13 manylinux x86_64 wheel hash",
                "psycopg-binary must pin the CPython 3.14 manylinux x86_64 wheel hash",
            ),
        )

        orphan_and_unpinned = validate_quality_requirements(
            f"--hash=sha256:{COVERAGE_UNIVERSAL_WHEEL_HASH}\n"
            "not-a-pinned-requirement\n"
            "setuptools==84.0.0\n"
            "wheel==0.48.0\n"
            "packaging==26.3\n"
            "psycopg==3.3.4\n"
            "psycopg-binary==3.3.4\n"
        )
        self.assertIn(
            "hash lock is not attached to a quality dependency", orphan_and_unpinned
        )
        self.assertIn(
            "unrecognized quality dependency line: not-a-pinned-requirement",
            orphan_and_unpinned,
        )
        self.assertIn("quality dependencies must pin coverage", orphan_and_unpinned)
        self.assertIn("setuptools must be hash locked", orphan_and_unpinned)
        self.assertIn("wheel must be hash locked", orphan_and_unpinned)
        self.assertIn("packaging must be hash locked", orphan_and_unpinned)
        self.assertIn("psycopg must be hash locked", orphan_and_unpinned)
        self.assertIn("psycopg-binary must be hash locked", orphan_and_unpinned)

        inline_valid = validate_quality_requirements(
            "coverage==7.15.4 "
            f"--hash=sha256:{COVERAGE_UNIVERSAL_WHEEL_HASH} "
            f"--hash=sha256:{COVERAGE_CP313_MANYLINUX_X86_64_WHEEL_HASH}\n"
            "setuptools==84.0.0 --hash=sha256:"
            "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670\n"
            "wheel==0.48.0 --hash=sha256:"
            "3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab\n"
            "packaging==26.3 --hash=sha256:"
            "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c\n"
            "psycopg==3.3.4 --hash=sha256:"
            "b6bbc25ccf05c8fad3b061d9db2ef0909a555171b84b07f29458a447253d679a\n"
            "psycopg-binary==3.3.4 --hash=sha256:"
            "c677c4ad433cb7150c8cd304a0769ae3bcfbe5ea0676eb53faa7b1443b16d0d3 "
            f"--hash=sha256:{PSYCOPG_BINARY_CP314_MANYLINUX_X86_64_WHEEL_HASH}\n"
            "# comment and blank lines are ignored\n\n"
        )
        self.assertEqual(inline_valid, ())

    def test_quality_requirements_include_central_python_314_psycopg_wheel(self) -> None:
        """The central coverage image must install the native PostgreSQL wheel."""
        quality_requirements = (ROOT / "requirements-quality.txt").read_text(
            encoding="utf-8"
        )
        psycopg_binary_stanza = re.search(
            r"(?ms)^psycopg-binary==3\.3\.4 \\\n"
            r"(?:    --hash=sha256:[0-9a-f]{64}(?: \\\n|\n))+",
            quality_requirements,
        )
        self.assertIsNotNone(psycopg_binary_stanza)
        self.assertIn(
            f"--hash=sha256:{PSYCOPG_BINARY_CP314_MANYLINUX_X86_64_WHEEL_HASH}",
            psycopg_binary_stanza.group(0) if psycopg_binary_stanza else "",
        )
        without_cp314 = quality_requirements.replace(
            f"    --hash=sha256:{PSYCOPG_BINARY_CP314_MANYLINUX_X86_64_WHEEL_HASH}\n",
            "",
        )
        self.assertIn(
            "psycopg-binary must pin the CPython 3.14 manylinux x86_64 wheel hash",
            validate_quality_requirements(without_cp314),
        )
        wrong_version = (
            without_cp314
            + "\npsycopg-binary==3.3.5 \\\n"
            f"    --hash=sha256:{PSYCOPG_BINARY_CP314_MANYLINUX_X86_64_WHEEL_HASH}\n"
        )
        self.assertIn(
            "psycopg-binary must pin the CPython 3.14 manylinux x86_64 wheel hash",
            validate_quality_requirements(wrong_version),
        )

        duplicate_version = quality_requirements + (
            "\npsycopg-binary==3.3.4\n"
        )
        self.assertIn(
            "quality dependency stanza appears more than once: psycopg-binary==3.3.4",
            validate_quality_requirements(duplicate_version),
        )

        for equivalent_name in ("psycopg_binary", "psycopg.binary"):
            equivalent_duplicate = quality_requirements + (
                f"\n{equivalent_name}==3.3.4\n"
            )
            self.assertIn(
                "quality dependency stanza appears more than once: psycopg-binary==3.3.4",
                validate_quality_requirements(equivalent_duplicate),
            )

    def test_append_only_journal_sql_rejects_update_and_delete(self) -> None:
        """Migrations cannot UPDATE or DELETE posted journal tables."""
        forbidden_statements = (
            "UPDATE accounting_core.general_journal SET journal_status_code = 'posted';",
            "UPDATE journal_entry_line SET debit_amount = 0;",
            "UPDATE ONLY general_journal SET journal_status_code = 'posted';",
            "DELETE FROM accounting_core.journal_entry_line;",
            "DELETE FROM general_journal;",
            "DELETE FROM ONLY journal_entry_line;",
            "TRUNCATE accounting_core.general_journal;",
            "TRUNCATE TABLE ONLY accounting_core.journal_entry_line;",
            "DROP TABLE accounting_core.general_journal;",
            'DROP TABLE IF EXISTS "accounting_core"."journal_entry_line" CASCADE;',
            "UPDATE \"accounting_core\".\"general_journal\" SET journal_status_code = 'posted';",
        )
        for statement in forbidden_statements:
            with self.subTest(statement=statement):
                self.assertEqual(
                    validate_append_only_journal_sql(statement),
                    (APPEND_ONLY_JOURNAL_MUTATION_ERROR,),
                )

    def test_append_only_journal_sql_allows_unrelated_mutations(self) -> None:
        """Unrelated UPDATE or DELETE of other tables remains valid."""
        allowed_statements = (
            "UPDATE accounting_core.chart_account SET account_class_code = 'asset';",
            "UPDATE ONLY accounting_core.account_role_mapping SET policy_version = '1';",
            "DELETE FROM accounting_core.account_role_mapping;",
            "DELETE FROM ONLY accounting_reporting.trial_balance_line;",
            "CREATE TRIGGER journal_balance_guard AFTER UPDATE ON general_journal "
            "FOR EACH ROW EXECUTE FUNCTION accounting_core.guard_journal();",
        )
        for statement in allowed_statements:
            with self.subTest(statement=statement):
                self.assertEqual(validate_append_only_journal_sql(statement), ())

    def test_repository_reports_journal_update_and_unhashed_dependency(self) -> None:
        """An UPDATE of general_journal fails the repository validator."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_root = Path(temporary_directory) / "repository"
            shutil.copytree(ROOT, copied_root, ignore=IGNORE_PATTERNS)
            mutation = copied_root / "database/migrations/0099_update_general_journal.sql"
            mutation.write_text(
                "UPDATE accounting_core.general_journal "
                "SET journal_status_code = 'posted';\n",
                encoding="utf-8",
            )
            (copied_root / "requirements-quality.txt").write_text(
                "coverage==7.15.4\n", encoding="utf-8"
            )
            errors = validate_repository(copied_root)
        self.assertIn(APPEND_ONLY_JOURNAL_MUTATION_ERROR, errors)
        self.assertIn("quality dependencies must be hash locked", errors)

    def test_repository_allows_unrelated_migration_update_and_delete(self) -> None:
        """Unrelated UPDATE or DELETE on other tables still passes the validator."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_root = Path(temporary_directory) / "repository"
            shutil.copytree(ROOT, copied_root, ignore=IGNORE_PATTERNS)
            mutation = copied_root / "database/migrations/0099_unrelated_mutation.sql"
            mutation.write_text(
                "UPDATE accounting_core.chart_account "
                "SET account_name = account_name;\n"
                "DELETE FROM accounting_reporting.trial_balance_line;\n",
                encoding="utf-8",
            )
            errors = validate_repository(copied_root)
        self.assertEqual(errors, ())

    def test_repository_reports_destructive_sql_and_unhashed_dependency(self) -> None:
        """Destructive journal SQL and mutable quality resolution fail closed."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            copied_root = Path(temporary_directory) / "repository"
            shutil.copytree(ROOT, copied_root, ignore=IGNORE_PATTERNS)
            migration = copied_root / "database/migrations/0001_accounting_foundation.sql"
            migration.write_text(
                migration.read_text(encoding="utf-8")
                + "\nDELETE FROM accounting_core.general_journal;\n",
                encoding="utf-8",
            )
            (copied_root / "requirements-quality.txt").write_text(
                "coverage==7.15.4\n", encoding="utf-8"
            )
            errors = validate_repository(copied_root)
        self.assertIn(APPEND_ONLY_JOURNAL_MUTATION_ERROR, errors)
        self.assertIn("quality dependencies must be hash locked", errors)

    def test_duplicate_schema_identity_is_rejected(self) -> None:
        """Two checked-in schemas cannot claim the same global contract identity."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            schemas = root / "schemas"
            schemas.mkdir()
            schema = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://schemas.example.test/duplicate",
                "type": "object",
                "additionalProperties": False,
            }
            (schemas / "first.schema.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )
            (schemas / "second.schema.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )
            errors = validate_repository(root)
        self.assertIn(
            "duplicate schema $id: https://schemas.example.test/duplicate", errors
        )

    def test_command_entrypoint_returns_process_status(self) -> None:
        """The CLI prints clean evidence or actionable contract failures."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([str(ROOT)]), 0)
        self.assertIn("repository contracts valid", output.getvalue())

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([temporary_directory]), 1)
        self.assertIn("missing required file: README.md", output.getvalue())

        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["validate_repository", str(ROOT)]):
            with redirect_stdout(output):
                self.assertEqual(main(None), 0)

        output = io.StringIO()
        with mock.patch("scripts.validate_repository.Path.cwd", return_value=ROOT):
            with redirect_stdout(output):
                self.assertEqual(main([]), 0)

    def _schema(self, name: str) -> dict[str, object]:
        return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
