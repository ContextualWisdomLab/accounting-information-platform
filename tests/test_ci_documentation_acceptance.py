"""Regression contract for authority-bearing documentation acceptance in CI."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_MAPPING_KEY_PATTERN = re.compile(
    r"""(?mx)
    (?:^[ \t]+(?:\?[ \t]+)?|[{,][ \t]*(?:\?[ \t]+)?)
    (?:&[^ \t\r\n]+[ \t]+)?
    (?P<key>
        "(?:\\.|[^"\\])*"
        |
        '(?:''|[^'])*'
        |
        [A-Za-z_][A-Za-z0-9_-]*
    )
    (?:
        [ \t]*:
        |
        [ \t]*\n[ \t]*:
    )
    """
)
_EVENT_ALIAS_PATTERN = re.compile(
    r"(?m)^[ \t]*\*(?P<alias>[A-Za-z0-9_-]+)(?=[ \t]*(?:#.*)?$)"
)
_SIMPLE_YAML_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\u0085",
    "_": "\u00a0",
    "L": "\u2028",
    "P": "\u2029",
}


def _decode_double_quoted_yaml_key(raw_key: str) -> str:
    """Decode YAML escapes needed to compare one double-quoted mapping key."""
    encoded_key = raw_key[1:-1]
    decoded_key: list[str] = []
    index = 0
    while index < len(encoded_key):
        character = encoded_key[index]
        if character != "\\":
            decoded_key.append(character)
            index += 1
            continue
        if index + 1 >= len(encoded_key):
            return raw_key
        escape_code = encoded_key[index + 1]
        if escape_code in {"x", "u", "U"}:
            width = {"x": 2, "u": 4, "U": 8}[escape_code]
            digits_start = index + 2
            digits_end = digits_start + width
            digits = encoded_key[digits_start:digits_end]
            if len(digits) != width or re.fullmatch(r"[0-9A-Fa-f]+", digits) is None:
                return raw_key
            try:
                decoded_key.append(chr(int(digits, 16)))
            except ValueError:
                return raw_key
            index = digits_end
            continue
        replacement = _SIMPLE_YAML_ESCAPES.get(escape_code)
        if replacement is None:
            return raw_key
        decoded_key.append(replacement)
        index += 2
    return "".join(decoded_key)


def _decode_yaml_mapping_key(raw_key: str) -> str:
    """Return the semantic key represented by one YAML mapping-key token."""
    if raw_key.startswith('"'):
        return _decode_double_quoted_yaml_key(raw_key)
    if raw_key.startswith("'"):
        return raw_key[1:-1].replace("''", "'")
    return raw_key


class _PathFilterDetector:
    """Find path filters or event aliases that can hide them without a YAML dependency."""

    @staticmethod
    def search(trigger_block: str) -> re.Match[str] | None:
        """Return the first path-filter key or fail-closed event alias."""
        alias_match = _EVENT_ALIAS_PATTERN.search(trigger_block)
        if alias_match is not None:
            return alias_match
        for match in _MAPPING_KEY_PATTERN.finditer(trigger_block):
            if _decode_yaml_mapping_key(match.group("key")) in {
                "paths",
                "paths-ignore",
            }:
                return match
        return None


PATH_FILTER = _PathFilterDetector()


class AccountingDocumentationCiAcceptanceTests(unittest.TestCase):
    """Keep documentation changes inside exact-head and integrated-head acceptance."""

    def test_path_filter_detection_rejects_inline_yaml_values(self) -> None:
        """Inline path filters must be detected as strongly as block-style filters."""
        for trigger_block in (
            "    paths: ['src/**']\n",
            "    paths-ignore: ['docs/**', '*.md']\n",
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "inline YAML path filters would let documentation bypass Accounting "
                    "Foundation CI acceptance",
                )

    def test_path_filter_detection_rejects_flow_style_event_mappings(self) -> None:
        """Flow-style event mappings cannot hide paths or paths-ignore filters."""
        for trigger_block in (
            "    pull_request: {paths: ['src/**']}\n",
            "    push: {paths-ignore: ['docs/**', '*.md']}\n",
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "flow-style YAML path filters would let documentation bypass Accounting "
                    "Foundation CI acceptance",
                )

    def test_path_filter_detection_rejects_quoted_yaml_keys(self) -> None:
        """Quoted YAML mapping keys cannot hide paths or paths-ignore filters."""
        for trigger_block in (
            '    "paths": ["src/**"]\n',
            "    'paths-ignore': ['docs/**', '*.md']\n",
            '    pull_request: {"paths": ["src/**"]}\n',
            "    push: {'paths-ignore': ['docs/**', '*.md']}\n",
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "quoted YAML path-filter keys would let documentation bypass Accounting "
                    "Foundation CI acceptance",
                )

    def test_path_filter_detection_rejects_escaped_quoted_yaml_keys(self) -> None:
        """YAML escapes that decode to path-filter keys cannot bypass acceptance."""
        for trigger_block in (
            '    "pa\\u0074hs": ["src/**"]\n',
            '    "paths\\x2dignore": ["docs/**"]\n',
            '    pull_request: {"pa\\u0074hs": ["src/**"]}\n',
            '    push: {"paths\\x2dignore": ["docs/**"]}\n',
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "escaped quoted YAML path-filter keys would let documentation bypass "
                    "Accounting Foundation CI acceptance",
                )

    def test_path_filter_detection_rejects_explicit_mapping_keys(self) -> None:
        """Explicit YAML mapping-key syntax cannot hide path-filter keys."""
        for trigger_block in (
            "    ? paths\n    : ['src/**']\n",
            "    ? 'paths-ignore'\n    : ['docs/**', '*.md']\n",
            '    ? "pa\\u0074hs"\n    : ["src/**"]\n',
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "explicit YAML mapping keys would let documentation bypass Accounting "
                    "Foundation CI acceptance",
                )

    def test_path_filter_detection_rejects_anchored_mapping_keys(self) -> None:
        """YAML node anchors cannot hide a semantic paths or paths-ignore key."""
        for trigger_block in (
            "    &path_filter paths: ['src/**']\n",
            "    &path_filter 'paths-ignore': ['docs/**', '*.md']\n",
            '    &path_filter "pa\\u0074hs": ["src/**"]\n',
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "anchored YAML path-filter keys would let documentation bypass Accounting "
                    "Foundation CI acceptance",
                )

    def test_path_filter_detection_rejects_event_aliases(self) -> None:
        """An event alias cannot hide an anchored mapping that carries path filters."""
        for trigger_block in (
            " *path_filter\n",
            " *docs_filter # anchor may be defined outside the event block\n",
        ):
            with self.subTest(trigger_block=trigger_block):
                self.assertIsNotNone(
                    PATH_FILTER.search(trigger_block),
                    "an aliased event mapping could hide paths or paths-ignore outside the "
                    "trigger block and bypass Accounting Foundation CI acceptance",
                )

    def test_accounting_ci_does_not_ignore_documentation_changes(self) -> None:
        """Docs and Markdown changes must not bypass repository accounting validation."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        pull_request_block = workflow.split("  pull_request:", 1)[1].split(
            "  push:", 1
        )[0]
        push_block = workflow.split("  push:", 1)[1].split("\npermissions:", 1)[0]

        for trigger_block in (pull_request_block, push_block):
            self.assertIsNone(
                PATH_FILTER.search(trigger_block),
                "Accounting Foundation CI pull_request/push triggers must not define "
                "paths or paths-ignore filters or opaque event aliases; authority-bearing "
                "documentation must receive the same exact-head acceptance as source changes.",
            )


if __name__ == "__main__":
    unittest.main()
