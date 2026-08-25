"""Supply-chain evidence generation contracts for release artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.generate_supply_chain_evidence import generate_supply_chain_evidence


class SupplyChainEvidenceTests(unittest.TestCase):
    """Keep wheel checksums, source provenance, and SPDX evidence deterministic."""

    def test_generates_deterministic_spdx_and_checksum_for_exact_wheel(self) -> None:
        """The same source, epoch, project metadata and wheel bytes generate identical evidence."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "pyproject.toml"
            project.write_text(
                '[project]\nname = "accounting-information-platform"\nversion = "0.1.0"\n',
                encoding="utf-8",
            )
            wheel = root / "accounting_information_platform-0.1.0-py3-none-any.whl"
            wheel.write_bytes(b"deterministic-wheel")
            source_sha = "a" * 40
            source_date_epoch = 1_787_000_000
            first = generate_supply_chain_evidence(
                wheel_path=wheel,
                project_path=project,
                output_directory=root / "first",
                source_sha=source_sha,
                source_date_epoch=source_date_epoch,
            )
            second = generate_supply_chain_evidence(
                wheel_path=wheel,
                project_path=project,
                output_directory=root / "second",
                source_sha=source_sha,
                source_date_epoch=source_date_epoch,
            )

            self.assertEqual(first.wheel_sha256, second.wheel_sha256)
            self.assertEqual(first.sbom_path.read_bytes(), second.sbom_path.read_bytes())
            self.assertEqual(
                first.provenance_path.read_bytes(), second.provenance_path.read_bytes()
            )
            self.assertEqual(first.checksums_path.read_bytes(), second.checksums_path.read_bytes())
            expected_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
            self.assertEqual(first.wheel_sha256, expected_sha)

            document = json.loads(first.sbom_path.read_text(encoding="utf-8"))
            self.assertEqual(document["spdxVersion"], "SPDX-2.3")
            self.assertEqual(document["dataLicense"], "CC0-1.0")
            self.assertEqual(document["documentDescribes"], ["SPDXRef-Package"])
            package = document["packages"][0]
            self.assertEqual(package["name"], "accounting-information-platform")
            self.assertEqual(package["versionInfo"], "0.1.0")
            self.assertEqual(
                package["checksums"],
                [{"algorithm": "SHA256", "checksumValue": expected_sha}],
            )
            self.assertEqual(package["primaryPackagePurpose"], "LIBRARY")
            self.assertEqual(
                package["externalRefs"][0]["referenceLocator"],
                "pkg:pypi/accounting-information-platform@0.1.0",
            )
            self.assertIn(source_sha, document["documentNamespace"])
            self.assertRegex(
                document["creationInfo"]["created"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            )

            provenance = json.loads(first.provenance_path.read_text(encoding="utf-8"))
            self.assertEqual(provenance["schema_version"], 1)
            self.assertEqual(
                provenance["source_repository"],
                "https://github.com/ContextualWisdomLab/accounting-information-platform",
            )
            self.assertEqual(provenance["source_sha"], source_sha)
            self.assertEqual(provenance["source_date_epoch"], source_date_epoch)
            self.assertEqual(provenance["artifact_file_name"], wheel.name)
            self.assertEqual(provenance["artifact_sha256"], expected_sha)
            self.assertEqual(provenance["sbom_file_name"], first.sbom_path.name)
            self.assertEqual(
                provenance["sbom_sha256"],
                hashlib.sha256(first.sbom_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(provenance["build_definition"], ".github/workflows/ci.yml")

            evidence_digests = {
                wheel.name: expected_sha,
                first.sbom_path.name: hashlib.sha256(first.sbom_path.read_bytes()).hexdigest(),
                first.provenance_path.name: hashlib.sha256(
                    first.provenance_path.read_bytes()
                ).hexdigest(),
            }
            expected_checksums = "".join(
                f"{digest}  {name}\n" for name, digest in evidence_digests.items()
            )
            self.assertEqual(
                first.checksums_path.read_text(encoding="utf-8"), expected_checksums
            )

    def test_rejects_non_sha1_source_identity_and_missing_wheel(self) -> None:
        """Evidence generation fails closed when source identity or artifact input is invalid."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "pyproject.toml"
            project.write_text(
                '[project]\nname = "aip"\nversion = "1"\n', encoding="utf-8"
            )
            wheel = root / "missing.whl"
            with self.assertRaisesRegex(ValueError, "source_sha"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="not-a-sha",
                    source_date_epoch=1,
                )
            with self.assertRaisesRegex(FileNotFoundError, "wheel"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="b" * 40,
                    source_date_epoch=1,
                )

    def test_rejects_unrepresented_runtime_dependencies(self) -> None:
        """The SBOM gate cannot silently omit a newly declared runtime dependency."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "pyproject.toml"
            project.write_text(
                '[project]\nname = "aip"\nversion = "1"\ndependencies = ["example>=1"]\n',
                encoding="utf-8",
            )
            wheel = root / "aip-1-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            with self.assertRaisesRegex(ValueError, "runtime dependencies"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="c" * 40,
                    source_date_epoch=1,
                )

    def test_rejects_invalid_project_metadata_and_source_timestamp(self) -> None:
        """Malformed PEP 621 metadata and timestamps cannot produce release evidence."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wheel = root / "aip-1-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            project = root / "pyproject.toml"

            with self.assertRaisesRegex(FileNotFoundError, "project metadata"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )

            project.write_text("[tool]\nname = 'aip'\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"\[project\]"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )

            project.write_text('[project]\nname = 1\nversion = "1"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "project.name"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )
            project.write_text('[project]\nname = " "\nversion = "1"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "project.name"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )

            project.write_text('[project]\nname = "aip"\nversion = 1\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "project.version"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )
            project.write_text('[project]\nname = "aip"\nversion = " "\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "project.version"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )

            project.write_text(
                '[project]\nname = "aip"\nversion = "1"\ndependencies = "not-a-list"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "project.dependencies"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )
            project.write_text(
                '[project]\nname = "aip"\nversion = "1"\ndependencies = [1]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "project.dependencies"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )
            project.write_text(
                '[project]\nname = "aip"\nversion = "1"\ndependencies = [" "]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "project.dependencies"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=1,
                )

            project.write_text('[project]\nname = "aip"\nversion = "1"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source_date_epoch"):
                generate_supply_chain_evidence(
                    wheel_path=wheel,
                    project_path=project,
                    output_directory=root / "out",
                    source_sha="d" * 40,
                    source_date_epoch=-1,
                )

    def test_command_line_entrypoint_emits_wheel_digest(self) -> None:
        """The checked-in command-line entrypoint emits evidence for one valid artifact."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "pyproject.toml"
            project.write_text('[project]\nname = "aip"\nversion = "1"\n', encoding="utf-8")
            wheel = root / "aip-1-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            output = io.StringIO()
            arguments = [
                "generate_supply_chain_evidence.py",
                "--wheel",
                str(wheel),
                "--project",
                str(project),
                "--output-directory",
                str(root / "out"),
                "--source-sha",
                "e" * 40,
                "--source-date-epoch",
                "1",
            ]
            with mock.patch.object(sys, "argv", arguments), redirect_stdout(output):
                runpy.run_path(
                    str(Path(__file__).parents[1] / "scripts/generate_supply_chain_evidence.py"),
                    run_name="__main__",
                )
            self.assertEqual(output.getvalue().strip(), hashlib.sha256(b"wheel").hexdigest())


if __name__ == "__main__":
    unittest.main()
