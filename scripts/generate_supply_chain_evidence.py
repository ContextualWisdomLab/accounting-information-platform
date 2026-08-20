"""Generate deterministic checksum and SPDX 2.3 evidence for one built wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class SupplyChainEvidence:
    """Paths and digest emitted for one exact wheel artifact."""

    wheel_sha256: str
    checksums_path: Path
    sbom_path: Path


def _sha256(path: Path) -> str:
    """Return the lowercase SHA-256 digest for *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_identity(project_path: Path) -> tuple[str, str]:
    """Return project name and version from PEP 621 metadata."""
    with project_path.open("rb") as stream:
        document = tomllib.load(stream)
    project = document.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml must contain a [project] table")
    name = project.get("name")
    version = project.get("version")
    dependencies = project.get("dependencies", [])
    if not isinstance(name, str) or not name.strip():
        raise ValueError("project.name must be a non-empty string")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("project.version must be a non-empty string")
    if not isinstance(dependencies, list) or any(
        not isinstance(item, str) or not item.strip() for item in dependencies
    ):
        raise ValueError("project.dependencies must be an array of non-empty strings")
    if dependencies:
        raise ValueError(
            "runtime dependencies require explicit SPDX relationship generation before attestation"
        )
    return name.strip(), version.strip()


def _created_timestamp(source_date_epoch: int) -> str:
    """Return a deterministic UTC SPDX timestamp for *source_date_epoch*."""
    if source_date_epoch < 0:
        raise ValueError("source_date_epoch must be non-negative")
    return datetime.fromtimestamp(source_date_epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_supply_chain_evidence(
    *,
    wheel_path: Path,
    project_path: Path,
    output_directory: Path,
    source_sha: str,
    source_date_epoch: int,
) -> SupplyChainEvidence:
    """Generate deterministic SHA256SUMS and SPDX 2.3 JSON for one exact wheel."""
    if _SOURCE_SHA_PATTERN.fullmatch(source_sha) is None:
        raise ValueError("source_sha must be a lowercase 40-character Git commit SHA")
    if not wheel_path.is_file():
        raise FileNotFoundError(f"wheel artifact does not exist: {wheel_path}")
    if not project_path.is_file():
        raise FileNotFoundError(f"project metadata does not exist: {project_path}")

    project_name, project_version = _project_identity(project_path)
    wheel_sha256 = _sha256(wheel_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    checksums_path = output_directory / "SHA256SUMS"
    sbom_path = output_directory / "sbom.spdx.json"

    checksums_path.write_text(
        f"{wheel_sha256}  {wheel_path.name}\n",
        encoding="utf-8",
    )
    package_spdx_id = "SPDXRef-Package"
    sbom = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "created": _created_timestamp(source_date_epoch),
            "creators": [
                "Organization: ContextualWisdomLab",
                "Tool: scripts/generate_supply_chain_evidence.py",
            ],
        },
        "dataLicense": "CC0-1.0",
        "documentDescribes": [package_spdx_id],
        "documentNamespace": (
            "https://github.com/ContextualWisdomLab/accounting-information-platform/"
            f"sbom/{source_sha}/{wheel_sha256}"
        ),
        "name": f"{project_name}-{project_version}-{source_sha[:12]}",
        "packages": [
            {
                "SPDXID": package_spdx_id,
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": wheel_sha256}
                ],
                "downloadLocation": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceLocator": f"pkg:pypi/{project_name}@{project_version}",
                        "referenceType": "purl",
                    }
                ],
                "filesAnalyzed": False,
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "name": project_name,
                "primaryPackagePurpose": "LIBRARY",
                "supplier": "Organization: ContextualWisdomLab",
                "versionInfo": project_version,
            }
        ],
        "relationships": [
            {
                "relatedSpdxElement": package_spdx_id,
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
            }
        ],
        "spdxVersion": "SPDX-2.3",
    }
    sbom_path.write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return SupplyChainEvidence(
        wheel_sha256=wheel_sha256,
        checksums_path=checksums_path,
        sbom_path=sbom_path,
    )


def main() -> None:
    """Generate evidence from command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    args = parser.parse_args()
    evidence = generate_supply_chain_evidence(
        wheel_path=args.wheel,
        project_path=args.project,
        output_directory=args.output_directory,
        source_sha=args.source_sha,
        source_date_epoch=args.source_date_epoch,
    )
    print(evidence.wheel_sha256)


if __name__ == "__main__":
    main()
