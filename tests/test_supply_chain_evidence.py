"""Supply-chain evidence generation contracts for release artifacts."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_supply_chain_evidence import generate_supply_chain_evidence


class SupplyChainEvidenceTests(unittest.TestCase):
    """Keep wheel checksums and SPDX SBOM evidence deterministic and source-bound."""

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
            first = generate_supply_chain_evidence(
                wheel_path=wheel,
                project_path=project,
                output_directory=root / "first",
                source_sha=source_sha,
                source_date_epoch=1_787_000_000,
            )
            second = generate_supply_chain_evidence(
                wheel_path=wheel,
                project_path=project,
                output_directory=root / "second",
                source_sha=source_sha,
                source_date_epoch=1_787_000_000,
            )

            self.assertEqual(first.wheel_sha256, second.wheel_sha256)
            self.assertEqual(first.sbom_path.read_bytes(), second.sbom_path.read_bytes())
            self.assertEqual(first.checksums_path.read_bytes(), second.checksums_path.read_bytes())
            expected_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
            self.assertEqual(first.wheel_sha256, expected_sha)
            self.assertEqual(
                first.checksums_path.read_text(encoding="utf-8"),
                f"{expected_sha}  {wheel.name}\n",
            )
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


if __name__ == "__main__":
    unittest.main()
