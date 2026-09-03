"""Exact-value validation and canonical hashing primitives for reports."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from ..core import AccountingValidationError

_URI_SCHEMES = frozenset({"http", "https", "urn"})
_RESERVED_PREFIXES = frozenset({"xml", "xmlns", "xbrli", "link", "xlink", "iso4217"})
_FACT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_XML_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")
_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _book_reference(source_mapping: Mapping[str, object]) -> str:
    """Resolve the compatible book fields while rejecting conflicting identities."""
    book_value = source_mapping.get("book_reference")
    accounting_value = source_mapping.get("accounting_book_reference")
    if book_value is None:
        return _required_text(accounting_value, "accounting_book_reference")
    book_reference = _required_text(book_value, "book_reference")
    if accounting_value is not None and _required_text(
        accounting_value, "accounting_book_reference"
    ) != book_reference:
        raise AccountingValidationError("book references do not match")
    return book_reference


def _mapping_text(source_mapping: Mapping[str, object], field_name: str) -> str:
    """Read one required canonical text field from a mapping."""
    return _required_text(source_mapping.get(field_name), field_name)


def _required_text(raw_value: object, field_name: str) -> str:
    """Require a non-empty string without leading or trailing whitespace."""
    if not isinstance(raw_value, str) or not raw_value or raw_value.strip() != raw_value:
        raise AccountingValidationError(f"{field_name} must be a canonical non-empty string")
    return raw_value


def _optional_text(raw_value: object, field_name: str) -> str:
    """Normalize an absent optional string while rejecting non-canonical values."""
    if raw_value is None:
        return ""
    if not isinstance(raw_value, str) or raw_value.strip() != raw_value:
        raise AccountingValidationError(f"{field_name} must be a canonical string")
    return raw_value


def _absolute_uri(raw_value: object, field_name: str) -> str:
    """Require an absolute HTTP, HTTPS, or URN identifier."""
    uri_text = _required_text(raw_value, field_name)
    parsed_uri = urlparse(uri_text)
    if parsed_uri.scheme.lower() not in _URI_SCHEMES:
        raise AccountingValidationError(f"{field_name} must be an absolute URI")
    if parsed_uri.scheme.lower() in {"http", "https"} and not parsed_uri.netloc:
        raise AccountingValidationError(f"{field_name} must include an authority")
    if parsed_uri.scheme.lower() == "urn" and not parsed_uri.path:
        raise AccountingValidationError(f"{field_name} must include a URN namespace")
    return uri_text


def _amount(raw_value: object, field_name: str) -> Decimal:
    """Parse one finite exact decimal and normalize signed zero."""
    try:
        decimal_amount = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as error:
        raise AccountingValidationError(f"{field_name} must be a finite decimal") from error
    if not decimal_amount.is_finite():
        raise AccountingValidationError(f"{field_name} must be a finite decimal")
    return abs(decimal_amount) if decimal_amount == 0 else decimal_amount


def _amount_text(decimal_amount: Decimal) -> str:
    """Return a non-exponential canonical decimal string."""
    return "0" if decimal_amount == 0 else format(decimal_amount, "f")


def _json_bytes(raw_value: object, error_message: str) -> bytes:
    """Serialize one value into canonical UTF-8 JSON bytes."""
    try:
        json_text = json.dumps(
            raw_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise AccountingValidationError(error_message) from error
    return json_text.encode("utf-8")


def _digest(raw_bytes: bytes) -> str:
    """Return a namespaced SHA-256 digest."""
    return "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
