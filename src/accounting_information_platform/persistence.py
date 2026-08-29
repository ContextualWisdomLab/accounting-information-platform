"""PostgreSQL adapter that preserves PostingLedger invariants on durable rows."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Mapping
from uuid import UUID

from .core import (
    AccountBalance,
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalProposal,
    PeriodCloseReceipt,
    PostedJournalLine,
    PostingLedger,
    PostingReceipt,
    _reversal_command_hash,
    _require_code,
    _require_currency,
    _require_proposal_id,
    _require_reference,
)

_SQL_SKIP_DATE = date.min
_SQL_SKIP_DATETIME = datetime(1, 1, 1, tzinfo=timezone.utc)
_SQL_SKIP_UUID = UUID(int=0)
_CLOSING_JOURNAL_PATTERN = "urn:cwl:accounting:general_journal:period_closing:%"
_READINESS_CONNECT_TIMEOUT_SECONDS = 5
_READINESS_STATEMENT_TIMEOUT_MILLISECONDS = 5_000
_READINESS_FUNCTIONS = (
    "accounting_core.guard_journal_line_book_scope()",
    "accounting_core.current_tenant_account_id()",
    "accounting_core.guard_period_insert()",
    "accounting_core.assert_journal_balance()",
    "accounting_core.guard_reversal_temporal_order()",
    "accounting_core.guard_reversal_lineage_insert()",
    "accounting_core.reject_finalized_fact_mutation()",
    "accounting_core.guard_finalized_journal_extension()",
    "accounting_integration.reject_period_open_command_mutation()",
    "accounting_core.guard_soft_close_evidence_update()",
    "accounting_integration.reject_statement_mutation()",
    "accounting_core.reject_reconciliation_run_scope_mutation()",
)
_READINESS_COLUMNS = (
    ("accounting_core", "chart_account", "account_class_code"),
    ("accounting_reporting", "trial_balance_snapshot", "close_idempotency_key"),
    (
        "accounting_core",
        "accounting_book_period_control",
        "soft_close_idempotency_key",
    ),
    (
        "accounting_core",
        "accounting_book_period_control",
        "soft_close_source_payload_hash",
    ),
    (
        "accounting_core",
        "accounting_book_period_control",
        "soft_close_source_journal_count",
    ),
    ("accounting_core", "bank_account_assignment", "assignment_idempotency_key"),
    ("accounting_core", "bank_account_assignment", "assignment_command_hash"),
)
_READINESS_RLS_POLICIES = (
    ("accounting_core", "account_role_mapping", "account_mapping_isolation"),
    ("accounting_core", "accounting_book", "accounting_book_isolation"),
    ("accounting_core", "accounting_book_period_control", "accounting_book_period_isolation"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_isolation"),
    ("accounting_core", "bank_account_record", "bank_account_record_isolation"),
    ("accounting_core", "chart_account", "chart_account_isolation"),
    ("accounting_core", "fiscal_calendar", "fiscal_calendar_isolation"),
    ("accounting_core", "fiscal_period", "fiscal_period_isolation"),
    ("accounting_core", "general_journal", "general_journal_isolation"),
    ("accounting_core", "journal_entry_line", "journal_entry_isolation"),
    ("accounting_core", "journal_match_allocation", "journal_match_allocation_isolation"),
    ("accounting_core", "journal_reversal", "journal_reversal_isolation"),
    ("accounting_core", "journal_source_reference", "journal_source_isolation"),
    ("accounting_core", "legal_entity_record", "legal_entity_isolation"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_isolation"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_isolation"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_isolation"),
    ("accounting_core", "reconciliation_match", "reconciliation_match_isolation"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_isolation"),
    ("accounting_core", "statement_match_allocation", "statement_match_allocation_isolation"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_isolation"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_isolation"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_detail_isolation"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_isolation"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_isolation"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_isolation"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_isolation"),
    ("accounting_integration", "outbox_event", "outbox_event_isolation"),
    ("accounting_integration", "posting_receipt", "posting_receipt_isolation"),
    ("accounting_reporting", "trial_balance_line", "trial_line_isolation"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_snapshot_isolation"),
)
_READINESS_RLS_TABLES = tuple(
    (schema_name, table_name)
    for schema_name, table_name, _policy_name in _READINESS_RLS_POLICIES
)
_READINESS_TENANT_FUNCTION_FINGERPRINT = "9c2cfaea74d193cadc39f46c242dd9a5"
_READINESS_COLUMN_FINGERPRINTS = (
    ("accounting_core", "account_role_mapping", 10, "dedc64e5c9fd53c0c38be2d14e3cffae2f2a879ba903d2994e49371988fe9974"),
    ("accounting_core", "accounting_book", 9, "7399e895b1d329ce2225db6e7efbb9ee7b33870ccc25324f9417e7f9c6df12d3"),
    ("accounting_core", "accounting_book_period_control", 10, "34c15ac952d044644030ebd1572abea1c1735504fe42b6ab3b12b21f4a16363d"),
    ("accounting_core", "bank_account_assignment", 11, "3f0525ce187b8177a450795d3152be09456c484921d3519225877b14a00762c3"),
    ("accounting_core", "bank_account_record", 6, "789c6f67bef0dfe092c9a8cc01c6565fe760324dba55441cb78dbed422140933"),
    ("accounting_core", "chart_account", 10, "00cf50d6767e93dd5c8e08c7548d5a106f79e1d2cab788520feebcfc734c7774"),
    ("accounting_core", "fiscal_calendar", 5, "98592e7dd88b3cd557e34cb569ad56b4a44f6c77c9c6a2eafcf8a78ad40ffed8"),
    ("accounting_core", "fiscal_period", 9, "9ebd9155188a34619d03e2d5e062870d5fa4b3696481b07d75726ed0869a08cb"),
    ("accounting_core", "general_journal", 15, "7a4c9f3ceaabb0c6b62aa84d29ccfaba558a1ef05dbea56fe618d4fac9a67213"),
    ("accounting_core", "journal_entry_line", 10, "c67aba7bfb7afffd4b934474c1120901c6527de471255ded778b58b89ee0fbc7"),
    ("accounting_core", "journal_match_allocation", 7, "6e02b58f45c91e4d4d9be19a3e4b296e6b79c1fa117eeb4d6d18f3a2424a4e02"),
    ("accounting_core", "journal_reversal", 6, "d14f8508cdc2427c20a12de4c01110257e3eea125122205b13bec718aa7e0357"),
    ("accounting_core", "journal_source_reference", 6, "b6da66657b76cfde57a7d29d3b54ba9f1e0face4a42556bebc9519e2bf9e658c"),
    ("accounting_core", "legal_entity_record", 8, "8d3ca7767d97cd3cb1f1079aede5a64851dd13838427bae8ec0f89d3a9521a5b"),
    ("accounting_core", "reconciliation_candidate", 9, "3604bec1f054717623d5625101e071fb12e99a05db311d94f1b7e47874fb14d8"),
    ("accounting_core", "reconciliation_evidence", 9, "5ca30a43ae8e40291166ac2c0d58be4ae97f0ac017d68185768b56c3bd29068a"),
    ("accounting_core", "reconciliation_exception", 9, "c5d64cb854348e796a02b91c7d2e036209fff504030b3503f6418ee7c9340d76"),
    ("accounting_core", "reconciliation_match", 7, "6a819f98e37334047d135ea107c449bcbe73ec699fe1253e77224e1c95e28877"),
    ("accounting_core", "reconciliation_run", 12, "0e0bb7771504d6765f84d401ce8c93806af9b6da3d1232777331f44d1a1fb99b"),
    ("accounting_core", "runtime_tenant_binding", 7, "737cb26fefab313e0d1493ee29efc2eb8179012eef41b9bcc72515cddba4b062"),
    ("accounting_core", "statement_match_allocation", 7, "443d000c078ecc946af417feaf2ce326b666e925d21c393d88cc7ad05c0634f3"),
    ("accounting_core", "tenant_account", 3, "84cca03fd15500baf0a1cc69d7c16ccd84e0fd4ca9868226e9a49a5408bacfa3"),
    ("accounting_integration", "bank_statement_artifact", 6, "1579cf1bc24f49d82fb726a56f5a7e8b9e5e90546ea61bad4d8ddc3a0db0c2a5"),
    ("accounting_integration", "bank_statement_entry", 23, "901945fdfa37b3fc88c6b2a2c3b12e11ebedb9a73c6ac396446408b10ac247ba"),
    ("accounting_integration", "bank_statement_entry_detail", 13, "f911fd0ecd7bc4a2f2a9c3464f9be5cdcd426345cc372194c06e0263e9a303c8"),
    ("accounting_integration", "bank_statement_record", 16, "b4b952b4190cffd0f4728b7bb36b2806f5d3f410a33088f40a8674df5ef43f27"),
    ("accounting_integration", "fiscal_period_open_command", 9, "a5fe15c4aa442c5758f02eeaef109d4e094dfb69f35fe0f41e44179a41cb419b"),
    ("accounting_integration", "home_tax_submission", 14, "a5a57e31b530af134d7b12a641089552e361b5ec534965bb287e34b032788341"),
    ("accounting_integration", "journal_proposal_record", 9, "cd9b60e16c9c915fb80b5f60fce351d9694aced846c21f3af615adb7b059768e"),
    ("accounting_integration", "outbox_event", 8, "cf0a8d212e2f703996882c05573facba683b240f3fbffa6df3d0caaab55a4ef3"),
    ("accounting_integration", "posting_receipt", 8, "901d09055edb9d560ba09118e5843ad8ef6145b882ef55ea6ee1e3fb9acda942"),
    ("accounting_reporting", "trial_balance_line", 7, "d4444986d7f37011866fed3125238240f4fbda402764bf20ef1f3c7c453729fd"),
    ("accounting_reporting", "trial_balance_snapshot", 10, "c000256659212235d9c0d38cf4ee6842b79ddd72f10abce709e28a36e4d70e42"),
)
_READINESS_TABLES = tuple(
    f"{schema_name}.{table_name}"
    for schema_name, table_name, _column_count, _fingerprint in _READINESS_COLUMN_FINGERPRINTS
)
_READINESS_CONSTRAINTS = (
    # PostgreSQL 18 pg_get_constraintdef() fingerprints cover every
    # migration-defined primary, unique, foreign-key, and check constraint.
    ("accounting_core", "account_role_mapping", "account_role_mapping_check", "c", "fd403eae41fb814541dbc5e90844922d"),
    ("accounting_core", "account_role_mapping", "account_role_mapping_pkey", "p", "76fdee80b79368a32b2c4c844135ce3e"),
    ("accounting_core", "account_role_mapping", "account_role_mapping_tenant_account_id_accounting_book_id_a_key", "u", "894d030515bbd8998e504e35256591e4"),
    ("accounting_core", "account_role_mapping", "account_role_mapping_tenant_account_id_accounting_book_id_fkey", "f", "92838a9a2e8af53916aaad6f380006bc"),
    ("accounting_core", "account_role_mapping", "account_role_mapping_tenant_account_id_chart_account_id_fkey", "f", "9b66fdb37b8a17cf6404327a5aeb7091"),
    ("accounting_core", "accounting_book", "accounting_book_check", "c", "fd403eae41fb814541dbc5e90844922d"),
    ("accounting_core", "accounting_book", "accounting_book_pkey", "p", "52adcd1348a12c7cabe0e69e0606d277"),
    ("accounting_core", "accounting_book", "accounting_book_reporting_currency_code_check", "c", "f99eb68af09c3a782e5ac99a232de29f"),
    ("accounting_core", "accounting_book", "accounting_book_tenant_account_id_accounting_book_id_key", "u", "40ff8d31661dc3dc28637746f3064c2d"),
    ("accounting_core", "accounting_book", "accounting_book_tenant_account_id_legal_entity_id_accountin_key", "u", "c1668255ec27423abc948861c1f45812"),
    ("accounting_core", "accounting_book", "accounting_book_tenant_account_id_legal_entity_id_book_role_key", "u", "70e5dbaf731086de511713ad7d06b54a"),
    ("accounting_core", "accounting_book", "accounting_book_tenant_account_id_legal_entity_id_fkey", "f", "2ae6e1f0ff555615d99a9a2238228ae5"),
    ("accounting_core", "accounting_book_period_control", "accounting_book_period_contro_tenant_account_id_accounting__key", "u", "792c8be17d0f6063cf4b8bfa3dd9b5aa"),
    ("accounting_core", "accounting_book_period_control", "accounting_book_period_contro_tenant_account_id_accounting_fkey", "f", "92838a9a2e8af53916aaad6f380006bc"),
    ("accounting_core", "accounting_book_period_control", "accounting_book_period_contro_tenant_account_id_accounting_key1", "u", "359a39451b69e5dbb5bb8771fd41e3f7"),
    ("accounting_core", "accounting_book_period_control", "accounting_book_period_contro_tenant_account_id_fiscal_per_fkey", "f", "4952d239e1577ff446787f11cdcca1a6"),
    ("accounting_core", "accounting_book_period_control", "accounting_book_period_control_period_status_code_check", "c", "e2d9e685bf489c5de53b2c9cbac1035f"),
    ("accounting_core", "accounting_book_period_control", "accounting_book_period_control_pkey", "p", "84d64414709cd929d16184762f5a469d"),
    ("accounting_core", "accounting_book_period_control", "soft_close_evidence_complete_check", "c", "b0c01ee22824af75c5faa9e6fef705e1"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_check", "c", "fd403eae41fb814541dbc5e90844922d"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_command_hash_format", "c", "2e3d46754adf91011085059e5910a8ab"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_command_key_present", "c", "f38926f6b77818270f4cd81692cf91df"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_pkey", "p", "f2a4fcdf8b3066184293dca827b0cfd0"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_reconciliation_scope_identity", "u", "0f9f3199e1b43dd4299899a26c144b63"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_tenant_account_id_accounting_book__fkey", "f", "92838a9a2e8af53916aaad6f380006bc"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_tenant_account_id_accounting_book_fkey1", "f", "092a0fa1372eeaa82f4c5d904b1f386e"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_tenant_account_id_bank_account_assi_key", "u", "e8ea3eb723b0603482cdee35eac637e6"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_tenant_account_id_bank_account_rec_fkey", "f", "f7b61bd255e18838b0102aaab5bbc818"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_tenant_account_id_legal_entity_id__fkey", "f", "4a313a10faf28875b126e02a8b635cd9"),
    ("accounting_core", "bank_account_assignment", "bank_account_assignment_tenant_account_id_legal_entity_id_fkey", "f", "2ae6e1f0ff555615d99a9a2238228ae5"),
    ("accounting_core", "bank_account_record", "bank_account_record_account_currency_code_check", "c", "79d3e374eeeef0d6cb410e5d41927401"),
    ("accounting_core", "bank_account_record", "bank_account_record_account_identifier_hash_check", "c", "83186ffd2f84cbb393d02278ab230139"),
    ("accounting_core", "bank_account_record", "bank_account_record_bank_account_reference_check", "c", "9824c77602625f921511e31cf47a0cea"),
    ("accounting_core", "bank_account_record", "bank_account_record_pkey", "p", "854eee056b489d85272d11b1961051df"),
    ("accounting_core", "bank_account_record", "bank_account_record_tenant_account_id_bank_account_record_i_key", "u", "3dbb3f0bde976eb211b5226625c9607c"),
    ("accounting_core", "bank_account_record", "bank_account_record_tenant_account_id_bank_account_referenc_key", "u", "64226960cce0a65cd1552317255094d5"),
    ("accounting_core", "bank_account_record", "bank_account_record_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_core", "chart_account", "account_class_check", "c", "96374f4c8a39b9254ccc77140ad500c6"),
    ("accounting_core", "chart_account", "chart_account_book_identity", "u", "aca5f809983b8764b9999a2abb0ec3a2"),
    ("accounting_core", "chart_account", "chart_account_check", "c", "fd403eae41fb814541dbc5e90844922d"),
    ("accounting_core", "chart_account", "chart_account_normal_balance_code_check", "c", "733618cbddbcc4c4291ea2f65c6bfb36"),
    ("accounting_core", "chart_account", "chart_account_pkey", "p", "52d20b0d4a01d16bdb46e8ca54a7d50f"),
    ("accounting_core", "chart_account", "chart_account_tenant_account_id_accounting_book_id_chart_ac_key", "u", "5e436b04249d0e9443ccdbc41c0b9ce0"),
    ("accounting_core", "chart_account", "chart_account_tenant_account_id_accounting_book_id_fkey", "f", "92838a9a2e8af53916aaad6f380006bc"),
    ("accounting_core", "chart_account", "chart_account_tenant_account_id_chart_account_id_key", "u", "ee4d76e6525ee05bd804657284699d25"),
    ("accounting_core", "fiscal_calendar", "fiscal_calendar_pkey", "p", "125a4192503c3d13452bca3f951112c0"),
    ("accounting_core", "fiscal_calendar", "fiscal_calendar_tenant_account_id_calendar_code_key", "u", "bd566c694bef2af9c53fd675dd748d38"),
    ("accounting_core", "fiscal_calendar", "fiscal_calendar_tenant_account_id_fiscal_calendar_id_key", "u", "9851c3e31bfd2a65b66c0c717e6b4681"),
    ("accounting_core", "fiscal_calendar", "fiscal_calendar_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_core", "fiscal_period", "fiscal_period_check", "c", "6eb04100a75ab5bf6e6441ffd47cbde1"),
    ("accounting_core", "fiscal_period", "fiscal_period_period_status_code_check", "c", "e2d9e685bf489c5de53b2c9cbac1035f"),
    ("accounting_core", "fiscal_period", "fiscal_period_pkey", "p", "08d95d723c7ffccf0977d2c20dcb5bce"),
    ("accounting_core", "fiscal_period", "fiscal_period_tenant_account_id_fiscal_calendar_id_fkey", "f", "1b28425204c7e84a81758b6c8b7858e7"),
    ("accounting_core", "fiscal_period", "fiscal_period_tenant_account_id_fiscal_calendar_id_period_c_key", "u", "824eda55fb0f78af83d34f9fc1a04a32"),
    ("accounting_core", "fiscal_period", "fiscal_period_tenant_account_id_fiscal_period_id_key", "u", "c8d4017fa040f3765dafdccd358aecea"),
    ("accounting_core", "general_journal", "general_journal_functional_currency_code_check", "c", "365e738e4e7e0016ae36410cffb33170"),
    ("accounting_core", "general_journal", "general_journal_journal_status_code_check", "c", "ee280a28ab5af00b280f0b07295ce1fe"),
    ("accounting_core", "general_journal", "general_journal_pkey", "p", "ce75d35c5d091ad059820d128973064a"),
    ("accounting_core", "general_journal", "general_journal_tenant_account_id_accounting_book_id_fkey", "f", "92838a9a2e8af53916aaad6f380006bc"),
    ("accounting_core", "general_journal", "general_journal_tenant_account_id_fiscal_period_id_fkey", "f", "4952d239e1577ff446787f11cdcca1a6"),
    ("accounting_core", "general_journal", "general_journal_tenant_account_id_general_journal_id_key", "u", "814bc76ab1205396b87f3ccb74e494cf"),
    ("accounting_core", "general_journal", "general_journal_tenant_account_id_journal_reference_key", "u", "cddca9293168481abd161bd1b54fbf86"),
    ("accounting_core", "general_journal", "general_journal_tenant_account_id_legal_entity_id_accounti_fkey", "f", "4a313a10faf28875b126e02a8b635cd9"),
    ("accounting_core", "general_journal", "general_journal_tenant_account_id_legal_entity_id_fkey", "f", "2ae6e1f0ff555615d99a9a2238228ae5"),
    ("accounting_core", "general_journal", "general_journal_tenant_account_id_source_proposal_record_i_fkey", "f", "01f31e9185acfc26c8cae43fecbe4650"),
    ("accounting_core", "general_journal", "general_journal_transaction_currency_code_check", "c", "783e4b2d809d1725982a3bf62dfc4b8a"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_check", "c", "fd0923bfd05c850b37ebafe0778b252c"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_credit_amount_check", "c", "628fd63022fcf7de70c6f7f5c9a2bbb5"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_debit_amount_check", "c", "bb716d3ca17c3fd9e2f92206d99abf5c"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_line_number_check", "c", "cf471c99cef0c5cf447706af09b5fa5f"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_pkey", "p", "a5bf3bb5895aa73c26e5708dfe85e202"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_tenant_account_id_chart_account_id_fkey", "f", "9b66fdb37b8a17cf6404327a5aeb7091"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_tenant_account_id_general_journal_id_fkey", "f", "abdd3a5f73d9f1bd1547c06c4565816b"),
    ("accounting_core", "journal_entry_line", "journal_entry_line_tenant_account_id_general_journal_id_lin_key", "u", "65fdbefc6cfdf4635eb0016a1fb42e65"),
    ("accounting_core", "journal_match_allocation", "journal_match_allocation_allocated_amount_check", "c", "91199dfae54e61691629871683a36df6"),
    ("accounting_core", "journal_match_allocation", "journal_match_allocation_journal_reference_check", "c", "6cafa987f2bf77992e30f19b00e19d4d"),
    ("accounting_core", "journal_match_allocation", "journal_match_allocation_pkey", "p", "6d17539b970ea3fec47f8faa17118c89"),
    ("accounting_core", "journal_match_allocation", "journal_match_allocation_reconciliation_match_id_fkey", "f", "d5f6e8c11f721b109273f2398d77085e"),
    ("accounting_core", "journal_match_allocation", "journal_match_allocation_tenant_account_id_reconciliation__fkey", "f", "3e513aba3aedc6a16ea7b1109a804beb"),
    ("accounting_core", "journal_reversal", "journal_reversal_check", "c", "2b3be4c5d7b132831d5740f812b7aa51"),
    ("accounting_core", "journal_reversal", "journal_reversal_pkey", "p", "0a46f8fd47427f7b8aab5d8986fcaf2a"),
    ("accounting_core", "journal_reversal", "journal_reversal_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_core", "journal_reversal", "journal_reversal_tenant_account_id_original_journal_id_fkey", "f", "232f2326b5bbfa5504298670970a3763"),
    ("accounting_core", "journal_reversal", "journal_reversal_tenant_account_id_original_journal_id_key", "u", "c38c5991a7050c0c80bde5bb3a490c8f"),
    ("accounting_core", "journal_reversal", "journal_reversal_tenant_account_id_reversal_journal_id_fkey", "f", "a7e153b5485e61ea1617ac7db79bef9e"),
    ("accounting_core", "journal_reversal", "journal_reversal_tenant_account_id_reversal_journal_id_key", "u", "42e106a6a59832e3ac30200e5a38180d"),
    ("accounting_core", "journal_source_reference", "journal_source_reference_pkey", "p", "f77240c561269f314163b07b1fcd291a"),
    ("accounting_core", "journal_source_reference", "journal_source_reference_source_payload_hash_check", "c", "bd060c8dddfeac59e811ab5fc9185206"),
    ("accounting_core", "journal_source_reference", "journal_source_reference_tenant_account_id_general_journal__key", "u", "fe082776480b5312c0da8d6289336714"),
    ("accounting_core", "journal_source_reference", "journal_source_reference_tenant_account_id_general_journal_fkey", "f", "abdd3a5f73d9f1bd1547c06c4565816b"),
    ("accounting_core", "legal_entity_record", "legal_entity_record_check", "c", "fd403eae41fb814541dbc5e90844922d"),
    ("accounting_core", "legal_entity_record", "legal_entity_record_functional_currency_code_check", "c", "365e738e4e7e0016ae36410cffb33170"),
    ("accounting_core", "legal_entity_record", "legal_entity_record_pkey", "p", "99976dac413c1f85bd3578841a124fa3"),
    ("accounting_core", "legal_entity_record", "legal_entity_record_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_core", "legal_entity_record", "legal_entity_record_tenant_account_id_legal_entity_code_val_key", "u", "47d8ac73817151daa66a2d7909f1fb57"),
    ("accounting_core", "legal_entity_record", "legal_entity_record_tenant_account_id_legal_entity_id_key", "u", "3509106d2bd9146f50dfcc794aa4a322"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_journal_amount_check", "c", "a2306f8d90d3d293ea7415b329dbfec5"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_journal_reference_check", "c", "6cafa987f2bf77992e30f19b00e19d4d"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_pkey", "p", "6af3ffa2bf2af2ec7813fe51b26136a5"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_rule_code_check", "c", "bc6942a27bf31a748162bca5ce57c83e"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_statement_amount_check", "c", "7db4be7bc9e34902f3dae2765a4fdc69"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_statement_entry_reference_check", "c", "245a4de35b1918a37f59fe2063bec2af"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_tenant_account_id_reconciliation__fkey", "f", "3e513aba3aedc6a16ea7b1109a804beb"),
    ("accounting_core", "reconciliation_candidate", "reconciliation_candidate_tenant_account_id_reconciliation_r_key", "u", "25f6f3d408b5ca7c5adc2b8481eaa7fd"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_evidence_payload_hash_check", "c", "a0fc5214b60ab9cb53ef71cf408800fc"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_evidence_reference_check", "c", "a6e693f892ae491937e6856594943666"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_evidence_type_code_check", "c", "9961091d4a31cdf406edf807a9fe22b4"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_pkey", "p", "b351c41e3137d8cc0302c646b9fc22f5"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_tenant_account_id_reconciliation__fkey1", "f", "e2262723980faf75816e9fd0ddea34b6"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_tenant_account_id_reconciliation_r_fkey", "f", "3e513aba3aedc6a16ea7b1109a804beb"),
    ("accounting_core", "reconciliation_evidence", "reconciliation_evidence_tenant_account_id_reconciliation_ru_key", "u", "a549ab88a62c88592e8d82866cc9944d"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_exception_code_check", "c", "10614e198bec347411903c95e599d444"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_next_action_check", "c", "00c8533f2a67ef1393d8ad66f7e7a965"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_owner_reference_check", "c", "64a6c9569e1ec7b788fa73bf564722b2"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_pkey", "p", "c9e6418125f5b0641175d1fbbb021203"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_resolution_status_code_check", "c", "4e5beb9351364966675e902332e2e92c"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_tenant_account_id_reconciliation__fkey", "f", "3e513aba3aedc6a16ea7b1109a804beb"),
    ("accounting_core", "reconciliation_exception", "reconciliation_exception_tenant_account_id_reconciliation_r_key", "u", "37affddefc01e6e3c7a17e0f6d00d322"),
    ("accounting_core", "reconciliation_match", "reconciliation_match_match_status_code_check", "c", "7077caa9b1b02587594ec051075a923d"),
    ("accounting_core", "reconciliation_match", "reconciliation_match_pkey", "p", "1256820721433bd948ab637f52074a6f"),
    ("accounting_core", "reconciliation_match", "reconciliation_match_reconciliation_candidate_id_fkey", "f", "f1fb2e1059df2d2560da0df59078be90"),
    ("accounting_core", "reconciliation_match", "reconciliation_match_tenant_account_id_reconciliation_run__fkey", "f", "3e513aba3aedc6a16ea7b1109a804beb"),
    ("accounting_core", "reconciliation_match", "reconciliation_match_tenant_account_id_reconciliation_run_i_key", "u", "044c572e9275e7532433060adcd72cc6"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_check", "c", "17d0ad9bfe9b90dd5fa6ebfda75e4e3a"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_check1", "c", "cd2472b80af72f910f9686473a760747"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_currency_code_check", "c", "4bfdd32f7cd7c89d5450f23ef6144f58"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_matching_policy_version_check", "c", "e526ec6f84e157f774641decc08a62d1"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_pkey", "p", "163213397f8dc5bbbb352d4fb471f381"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_run_status_code_check", "c", "b71d41855e13698a531ac2d393111104"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_tenant_account_id_legal_entity_id_acco_fkey1", "f", "232b00c9eb87ba38aac41f0e7f7200a1"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_tenant_account_id_legal_entity_id_accou_fkey", "f", "4a313a10faf28875b126e02a8b635cd9"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_tenant_account_id_legal_entity_id_fkey", "f", "2ae6e1f0ff555615d99a9a2238228ae5"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_tenant_account_id_reconciliation_run_id_key", "u", "2d7e119455ca717675c53f54f2ece8e8"),
    ("accounting_core", "runtime_tenant_binding", "runtime_tenant_binding_check", "c", "fd403eae41fb814541dbc5e90844922d"),
    ("accounting_core", "runtime_tenant_binding", "runtime_tenant_binding_pkey", "p", "af7ccf2ac895ce7502ab8439b53a3afe"),
    ("accounting_core", "runtime_tenant_binding", "runtime_tenant_binding_runtime_tenant_binding_id_tenant_acc_key", "u", "6c9695d8d033b1fd223216339c2bb0c2"),
    ("accounting_core", "runtime_tenant_binding", "runtime_tenant_binding_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_core", "statement_match_allocation", "statement_match_allocation_allocated_amount_check", "c", "91199dfae54e61691629871683a36df6"),
    ("accounting_core", "statement_match_allocation", "statement_match_allocation_pkey", "p", "6d17539b970ea3fec47f8faa17118c89"),
    ("accounting_core", "statement_match_allocation", "statement_match_allocation_reconciliation_match_id_fkey", "f", "d5f6e8c11f721b109273f2398d77085e"),
    ("accounting_core", "statement_match_allocation", "statement_match_allocation_statement_entry_reference_check", "c", "245a4de35b1918a37f59fe2063bec2af"),
    ("accounting_core", "statement_match_allocation", "statement_match_allocation_tenant_account_id_reconciliatio_fkey", "f", "3e513aba3aedc6a16ea7b1109a804beb"),
    ("accounting_core", "tenant_account", "tenant_account_pkey", "p", "2b3784ac644769b0b5ed25609b140f13"),
    ("accounting_core", "tenant_account", "tenant_account_tenant_account_code_key", "u", "c7d1b1ee518f08f3907103feadf5b291"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_artifact_byte_length_check", "c", "25700d8635825b6ed3df1dc1c9b2df68"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_artifact_store_reference_check", "c", "236b9449403267a553d8cd80ba0e3087"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_pkey", "p", "aaf955d4f861ea486ae4d2ad0a0a3d16"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_source_artifact_hash_check", "c", "ca5daedf0a828b0488ea77681fa88449"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_tenant_account_id_bank_statement_ar_key", "u", "6659498ed58e8838c241ab74e685f75c"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_tenant_account_id_source_artifact_h_key", "u", "4d677b22bd40bd1938cfbde217351902"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_counterparty_evidence_hash_check", "c", "5c41cc062fb02613715b9bc7ff2e0cb1"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_credit_debit_code_check", "c", "1cf04c46ae4d4b97cc257b3675c5ad69"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_entry_amount_check", "c", "92988afa3b9236871b3489f6eaede0c9"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_entry_currency_code_check", "c", "826c2c906630cc51a033496e88c08030"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_entry_sequence_number_check", "c", "45df5e4b67714f5b184e7d901f1d1219"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_pkey", "p", "15800f49b3f91ddd6971777fbcf8c640"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_source_entry_hash_check", "c", "136856e8d838d35fc457ff467cdabc2e"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_source_locator_path_check", "c", "b68e98a85e3e86f7d30d696e62758ae1"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_tenant_account_id_bank_statement_entry_key", "u", "620081c2c2495fbf9a91cb1c8f2e99b3"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_tenant_account_id_bank_statement_reco_fkey", "f", "b05657531c15d1f3a4b20ad54dd677a8"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_tenant_account_id_bank_statement_recor_key", "u", "9ec9f137980e3be3d923669cc117e473"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_credit_debit_code_check", "c", "1cf04c46ae4d4b97cc257b3675c5ad69"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_detail_amount_check", "c", "033b108d19193ddc9d26bef974fce875"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_detail_currency_code_check", "c", "575ce281a503d150d6851a08b20d10ec"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_detail_sequence_number_check", "c", "f842e3dbb010311fd0043f628efc916b"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_pkey", "p", "d075c1dd6f40460f361a07018cd81c6e"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_source_detail_hash_check", "c", "c70893eeead3339da6f16ce8059a4206"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_source_locator_path_check", "c", "b68e98a85e3e86f7d30d696e62758ae1"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_tenant_account_id_bank_stateme_fkey", "f", "54fcd5664a0426acb8cb0f19429bb6d6"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_tenant_account_id_bank_stateme_key1", "u", "d774cd9afd0e413a2b1b58843110e8aa"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_tenant_account_id_bank_statemen_key", "u", "010ad6d10fa4063040274f3f9b5f6980"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_closing_balance_hash_check", "c", "ecc7eaf4b0636bc01d4272368af0a6d9"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_ingestion_idempotency_key_check", "c", "4594874be2d055c93b16b316c59d18ff"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_message_definition_identifier_check", "c", "35c5a24907adac53c6647bda186b4a3c"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_normalized_payload_hash_check", "c", "894c0a94f0fe3c21a9903823960362bc"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_opening_balance_hash_check", "c", "54a8063b820c77ef1872ab9e7737667b"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_pkey", "p", "29ef019564694e8d91c541c1b51c728b"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_source_artifact_hash_check", "c", "ca5daedf0a828b0488ea77681fa88449"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_statement_identity_reference_check", "c", "de0e829272a82f85e9ec341eab15f28f"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_tenant_account_id_bank_account_recor_fkey", "f", "f7b61bd255e18838b0102aaab5bbc818"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_tenant_account_id_bank_account_record_key", "u", "3b0d0c3762efbcea7fa4484e6d249c7b"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_tenant_account_id_bank_statement_art_fkey", "f", "f6958e54e9a0a06810ce8da65a0b06fa"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_tenant_account_id_bank_statement_reco_key", "u", "c05a8c0654b1ac44f2a151d1745f2357"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_tenant_account_id_ingestion_idempoten_key", "u", "7f98ca9dbd3472e5b04deb1d052be857"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_tenant_account_id_source_artifact_has_key", "u", "4d677b22bd40bd1938cfbde217351902"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_check", "c", "044a111841a7fa95687de15dabe3d77a"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_period_open_idempotency_key_check", "c", "5730a3ae577d0b4166041c1f6f30957a"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_pkey", "p", "38fce57b6e505780d968f335183084fd"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_source_payload_hash_check", "c", "bd060c8dddfeac59e811ab5fc9185206"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_tenant_account_id_fiscal_period__key", "u", "6b745bb8702beb61b65d46b21ff8ddb7"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_tenant_account_id_fiscal_period_fkey", "f", "4952d239e1577ff446787f11cdcca1a6"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_tenant_account_id_legal_entity__fkey", "f", "2ae6e1f0ff555615d99a9a2238228ae5"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_tenant_account_id_period_open_id_key", "u", "793bb48354ed1ce69ff5cc828522698e"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_pkey", "p", "414fa81980ffdedcd2d7194f5c12fcc3"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_register_payload_hash_check", "c", "545054151722ee43c2eaf8a78179dc94"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_rejection_reason_code_check", "c", "9e45f793ba470c47822b4984bba24773"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_source_payload_hash_check", "c", "bd060c8dddfeac59e811ab5fc9185206"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_source_payload_reference_check", "c", "a8c30ba10f288f20ccaa80616074861f"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_submission_idempotency_key_check", "c", "dd6cd9c58c3ead1cf7773c09f39de391"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_submission_status_code_check", "c", "e1498ed3fd56bb72c582ddb6db42f7ba"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_tenant_account_id_accounting_book_id_fkey", "f", "92838a9a2e8af53916aaad6f380006bc"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_tenant_account_id_fiscal_period_id_fkey", "f", "4952d239e1577ff446787f11cdcca1a6"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_tenant_account_id_home_tax_submission_i_key", "u", "269dd08fd8881bbad0cbb31686f64d72"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_tenant_account_id_legal_entity_id_fkey", "f", "2ae6e1f0ff555615d99a9a2238228ae5"),
    ("accounting_integration", "home_tax_submission", "home_tax_submission_tenant_account_id_submission_idempotenc_key", "u", "bff5ba8ed66073f5bedec3269823b459"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_pkey", "p", "60cad40e89c772c6d3542a01b312b9da"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_proposal_contract_version_check", "c", "17c4d4e5ce1fdfe7c9592c42ebd18b56"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_proposal_status_code_check", "c", "70bef7fca926801d5d82d7227ecabb28"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_source_payload_hash_check", "c", "bd060c8dddfeac59e811ab5fc9185206"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_tenant_account_id_external_proposal_key", "u", "7289478b5a739bbcf9271e56c4911639"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_tenant_account_id_idempotency_key_key", "u", "01cbb17c53e588b8bcf06ea9926bfe33"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_record_tenant_account_id_proposal_record_i_key", "u", "9107276ff426e1ba714ebdf14d9d8a3a"),
    ("accounting_integration", "outbox_event", "outbox_event_payload_hash_check", "c", "cb48b6a4c2a0c781d3cb47a1981b82cf"),
    ("accounting_integration", "outbox_event", "outbox_event_pkey", "p", "4372930136ba6cd593285866ed333ae7"),
    ("accounting_integration", "outbox_event", "outbox_event_tenant_account_id_fkey", "f", "d264b1dc903f5f56aa6362492af0914a"),
    ("accounting_integration", "posting_receipt", "posting_receipt_pkey", "p", "4c7ecac00512ce54c61d6021da84ddcc"),
    ("accounting_integration", "posting_receipt", "posting_receipt_receipt_payload_hash_check", "c", "2908949ee55d29075f31c18354d7d2cf"),
    ("accounting_integration", "posting_receipt", "posting_receipt_receipt_status_code_check", "c", "8db3fa357c873e3232570c761122f173"),
    ("accounting_integration", "posting_receipt", "posting_receipt_tenant_account_id_general_journal_id_fkey", "f", "abdd3a5f73d9f1bd1547c06c4565816b"),
    ("accounting_integration", "posting_receipt", "posting_receipt_tenant_account_id_posting_receipt_id_key", "u", "fed205f58cbc62309aca65411d56d4a1"),
    ("accounting_integration", "posting_receipt", "posting_receipt_tenant_account_id_proposal_record_id_fkey", "f", "d783b0ba8b94a20fa644bdc9c1c12d83"),
    ("accounting_integration", "posting_receipt", "posting_receipt_tenant_account_id_proposal_record_id_key", "u", "9107276ff426e1ba714ebdf14d9d8a3a"),
    ("accounting_reporting", "trial_balance_line", "trial_balance_line_credit_total_amount_check", "c", "e895ac2bf9fb6428c70dd42634f93a33"),
    ("accounting_reporting", "trial_balance_line", "trial_balance_line_debit_total_amount_check", "c", "7e1b5a6965c0c7a1dddbd02bacabaa7e"),
    ("accounting_reporting", "trial_balance_line", "trial_balance_line_pkey", "p", "1fdaf3191a55a07d5472ce2060ea1087"),
    ("accounting_reporting", "trial_balance_line", "trial_balance_line_tenant_account_id_chart_account_id_fkey", "f", "9b66fdb37b8a17cf6404327a5aeb7091"),
    ("accounting_reporting", "trial_balance_line", "trial_balance_line_tenant_account_id_trial_balance_snapsho_fkey", "f", "00e8b0d0b0f3778260b34f0a5b0837f3"),
    ("accounting_reporting", "trial_balance_line", "trial_balance_line_tenant_account_id_trial_balance_snapshot_key", "u", "6af95a9042dbcc3203c139111d39cc18"),
    ("accounting_reporting", "trial_balance_snapshot", "close_idempotency_nonempty_check", "c", "7953f37e0cda34bb8fdc1d813cd2b252"),
    ("accounting_reporting", "trial_balance_snapshot", "close_idempotency_tenant_key_unique", "u", "2dd996893c62710a9721e1b8ae457d25"),
    ("accounting_reporting", "trial_balance_snapshot", "close_snapshot_scope_unique", "u", "027773b128c23df5f239a689eb1c29ce"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_pkey", "p", "f97ba301b9f6f0afb0e8a84bbbc79ec5"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_snapshot_currency_code_check", "c", "9c69206729d79fce77ceb109ba8d4f55"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_source_journal_count_check", "c", "8ed2ae583b41cd4afbf1da6edb0e5fc3"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_source_payload_hash_check", "c", "bd060c8dddfeac59e811ab5fc9185206"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_tenant_account_id_accounting_book_i_fkey", "f", "92838a9a2e8af53916aaad6f380006bc"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_tenant_account_id_accounting_book_id_key", "u", "243f309e680c83c30f274419f1f4a468"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_tenant_account_id_fiscal_period_id_fkey", "f", "4952d239e1577ff446787f11cdcca1a6"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_tenant_account_id_legal_entity_id_fkey", "f", "2ae6e1f0ff555615d99a9a2238228ae5"),
    ("accounting_reporting", "trial_balance_snapshot", "trial_balance_snapshot_tenant_account_id_trial_balance_snap_key", "u", "139d8b37509d6b5d8ba1ee479cf64890"),
)
_READINESS_INDEX_DEFINITIONS = (
    # MD5 fingerprints are generated from PostgreSQL 18 pg_get_indexdef().
    (
        "accounting_integration",
        "home_tax_submission_scope_order_index",
        "accounting_integration",
        "home_tax_submission",
        False,
        "",
        "d6b1b688aece25340f915c0a0d9f8b50",
    ),
    (
        "accounting_integration",
        "journal_proposal_tenant_received_index",
        "accounting_integration",
        "journal_proposal_record",
        False,
        "",
        "c8fa3fb6e9260045dd8157d7fe3b8b4b",
    ),
    (
        "accounting_core",
        "general_journal_tenant_period_date_index",
        "accounting_core",
        "general_journal",
        False,
        "",
        "5c426ce4a8f04aad8e99c4b59e5f5b71",
    ),
    (
        "accounting_core",
        "journal_entry_tenant_journal_index",
        "accounting_core",
        "journal_entry_line",
        False,
        "",
        "5072afea7396a298f8cc243fee1a4eb3",
    ),
    (
        "accounting_integration",
        "outbox_event_pending_created_index",
        "accounting_integration",
        "outbox_event",
        False,
        "published_at IS NULL",
        "fc4ccb0e4fca277698e24ae7dc930153",
    ),
    (
        "accounting_core",
        "reversal_event_tenant_reversed_index",
        "accounting_core",
        "journal_reversal",
        False,
        "",
        "71f39d81b1d456e6821af4327cc8fd40",
    ),
    (
        "accounting_integration",
        "posting_receipt_tenant_created_index",
        "accounting_integration",
        "posting_receipt",
        False,
        "",
        "89fc500a7f7d35f860a04ef8f333dd97",
    ),
    (
        "accounting_integration",
        "home_tax_submission_tenant_created_index",
        "accounting_integration",
        "home_tax_submission",
        False,
        "",
        "989da24e0b7905e00bf84c531d0a481c",
    ),
    (
        "accounting_core",
        "runtime_tenant_binding_active_index",
        "accounting_core",
        "runtime_tenant_binding",
        True,
        "valid_to IS NULL",
        "ba8c772c745da4ee6477ecad42b1cc3b",
    ),
    (
        "accounting_core",
        "accounting_book_period_scope_index",
        "accounting_core",
        "accounting_book_period_control",
        False,
        "",
        "cb9f53ec5912bd7f81bdbf7984b302b3",
    ),
    (
        "accounting_core",
        "accounting_book_period_soft_close_key_index",
        "accounting_core",
        "accounting_book_period_control",
        True,
        "soft_close_idempotency_key IS NOT NULL",
        "16a9d03ccaa464e216bbeac73fba113e",
    ),
    (
        "accounting_integration",
        "bank_statement_account_period_index",
        "accounting_integration",
        "bank_statement_record",
        False,
        "",
        "f1c8b50a192c553a9e42d61dfd8d088a",
    ),
    (
        "accounting_integration",
        "bank_statement_entry_order_index",
        "accounting_integration",
        "bank_statement_entry",
        False,
        "",
        "8c916af412e96b617698a04de28265f8",
    ),
    (
        "accounting_core",
        "bank_account_assignment_command_key_scope",
        "accounting_core",
        "bank_account_assignment",
        True,
        "",
        "64ac2bc9357e787bcacaa6a8e1f396e6",
    ),
    (
        "accounting_core",
        "bank_account_assignment_active_book_scope",
        "accounting_core",
        "bank_account_assignment",
        True,
        "valid_to IS NULL",
        "b2630c8c9c671bb11266fba47d1831f3",
    ),
    (
        "accounting_core",
        "reconciliation_run_scope_index",
        "accounting_core",
        "reconciliation_run",
        False,
        "",
        "da3de4858f6a576e7ca77d93dfdcb0e4",
    ),
    (
        "accounting_core",
        "reconciliation_exception_run_index",
        "accounting_core",
        "reconciliation_exception",
        False,
        "",
        "b7170b69a7c4eaaf3924f774c3d12b0b",
    ),
    (
        "accounting_core",
        "reconciliation_evidence_run_index",
        "accounting_core",
        "reconciliation_evidence",
        False,
        "",
        "d4ae874758fc23e85321f68d256d046b",
    ),
    (
        "accounting_core",
        "reconciliation_candidate_run_reference_index",
        "accounting_core",
        "reconciliation_candidate",
        False,
        "",
        "6437127d9851ad2fcae9bdfd27645352",
    ),
    (
        "accounting_core",
        "reconciliation_match_approved_single",
        "accounting_core",
        "reconciliation_match",
        True,
        "match_status_code = 'approved'::text",
        "c2ac9dd58cecb53c83ab231ab07f6b62",
    ),
    (
        "accounting_core",
        "reconciliation_allocation_run_reference_index",
        "accounting_core",
        "statement_match_allocation",
        False,
        "",
        "ec236d72041f66d51a31be2e0edfd886",
    ),
    (
        "accounting_core",
        "journal_allocation_run_reference_index",
        "accounting_core",
        "journal_match_allocation",
        False,
        "",
        "d3f4d334a92af03ef68bda117af03655",
    ),
)
_READINESS_BALANCE_TRIGGERS = (
    # MD5 fingerprints are generated from PostgreSQL 18 pg_get_functiondef() for
    # the checked-in 0005_closed_period_guard.sql definition.
    (
        "accounting_core",
        "general_journal",
        "general_journal_balance_guard",
        "accounting_core",
        "assert_journal_balance",
        21,
        "3747f99334249a1ee8cfdb286f4a2691",
    ),
    (
        "accounting_core",
        "journal_entry_line",
        "journal_entry_balance_guard",
        "accounting_core",
        "assert_journal_balance",
        29,
        "3747f99334249a1ee8cfdb286f4a2691",
    ),
)

_READINESS_CONTROL_TRIGGERS = (
    (
        "accounting_core",
        "journal_entry_line",
        "journal_line_book_scope_guard",
        "accounting_core",
        "guard_journal_line_book_scope",
        23,
        "tenant_account_id,general_journal_id,chart_account_id",
    ),
    (
        "accounting_core",
        "general_journal",
        "closed_period_guard",
        "accounting_core",
        "guard_period_insert",
        7,
        "",
    ),
    (
        "accounting_core",
        "journal_reversal",
        "journal_reversal_first_temporal_guard",
        "accounting_core",
        "guard_reversal_temporal_order",
        7,
        "",
    ),
    (
        "accounting_core",
        "journal_reversal",
        "journal_reversal_second_finalization_guard",
        "accounting_core",
        "guard_reversal_lineage_insert",
        7,
        "",
    ),
    (
        "accounting_core",
        "general_journal",
        "general_journal_immutable_guard",
        "accounting_core",
        "reject_finalized_fact_mutation",
        27,
        "",
    ),
    (
        "accounting_core",
        "journal_entry_line",
        "journal_entry_immutable_guard",
        "accounting_core",
        "reject_finalized_fact_mutation",
        27,
        "",
    ),
    (
        "accounting_core",
        "journal_source_reference",
        "journal_source_immutable_guard",
        "accounting_core",
        "reject_finalized_fact_mutation",
        27,
        "",
    ),
    (
        "accounting_core",
        "journal_reversal",
        "journal_reversal_immutable_guard",
        "accounting_core",
        "reject_finalized_fact_mutation",
        27,
        "",
    ),
    (
        "accounting_integration",
        "posting_receipt",
        "posting_receipt_immutable_guard",
        "accounting_core",
        "reject_finalized_fact_mutation",
        27,
        "",
    ),
    (
        "accounting_integration",
        "journal_proposal_record",
        "journal_proposal_immutable_guard",
        "accounting_core",
        "reject_finalized_fact_mutation",
        27,
        "",
    ),
    (
        "accounting_core",
        "journal_entry_line",
        "journal_entry_finalized_guard",
        "accounting_core",
        "guard_finalized_journal_extension",
        7,
        "",
    ),
    (
        "accounting_core",
        "journal_source_reference",
        "journal_source_finalized_guard",
        "accounting_core",
        "guard_finalized_journal_extension",
        7,
        "",
    ),
    (
        "accounting_integration",
        "fiscal_period_open_command",
        "fiscal_period_open_command_immutable",
        "accounting_integration",
        "reject_period_open_command_mutation",
        27,
        "",
    ),
    (
        "accounting_core",
        "accounting_book_period_control",
        "soft_close_evidence_immutable_guard",
        "accounting_core",
        "guard_soft_close_evidence_update",
        19,
        "soft_close_idempotency_key,soft_close_source_payload_hash,soft_close_source_journal_count",
    ),
    (
        "accounting_integration",
        "bank_statement_artifact",
        "bank_statement_artifact_immutable_guard",
        "accounting_integration",
        "reject_statement_mutation",
        27,
        "",
    ),
    (
        "accounting_integration",
        "bank_statement_record",
        "bank_statement_record_immutable_guard",
        "accounting_integration",
        "reject_statement_mutation",
        27,
        "",
    ),
    (
        "accounting_integration",
        "bank_statement_entry",
        "bank_statement_entry_immutable_guard",
        "accounting_integration",
        "reject_statement_mutation",
        27,
        "",
    ),
    (
        "accounting_integration",
        "bank_statement_entry_detail",
        "bank_statement_entry_detail_immutable_guard",
        "accounting_integration",
        "reject_statement_mutation",
        27,
        "",
    ),
    (
        "accounting_core",
        "reconciliation_run",
        "reconciliation_run_scope_guard",
        "accounting_core",
        "reject_reconciliation_run_scope_mutation",
        19,
        "tenant_account_id,legal_entity_id,accounting_book_id,bank_account_assignment_id,currency_code,bank_cutoff_at,book_cutoff_at,matching_policy_version,knowledge_cutoff_at",
    ),
)

_READINESS_CONTROL_FUNCTION_FINGERPRINTS = {
    ("accounting_core", "guard_journal_line_book_scope"): "d5405804549eebcdf5671e806e5b44cf",
    ("accounting_core", "guard_period_insert"): "9f9279beee5d7f0e5e7d35855a26239a",
    ("accounting_core", "guard_reversal_temporal_order"): "f246ad19efc8aeae4a4ade2447bd385f",
    ("accounting_core", "guard_reversal_lineage_insert"): "05930c3362ed781dbddac4867c1dff3f",
    ("accounting_core", "reject_finalized_fact_mutation"): "bd0805588ceb59b3bf8b3b044562cc07",
    ("accounting_core", "guard_finalized_journal_extension"): "fce00d3dbe97d45c5102908f07ffc0d9",
    ("accounting_integration", "reject_period_open_command_mutation"): "9b876e04b2c18c1f2f68592b261166bc",
    ("accounting_core", "guard_soft_close_evidence_update"): "1df9550334c4dcb6f2f1c4f8e8e6daa7",
    ("accounting_integration", "reject_statement_mutation"): "5a8e89e5e5322c8659dc8eb3dee4858c",
    ("accounting_core", "reject_reconciliation_run_scope_mutation"): "863b6ca92b9ec815f5c5688bc2f74892",
}



class PostgresPostingLedger:
    """Authoritative posting, catalog policy resolution, close, trial balance, and statements on PostgreSQL 18."""

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        """Bind one tenant to a PostgreSQL 18 database URL."""
        if not database_url:
            raise AccountingValidationError(
                "ACCOUNTING_DATABASE_URL is empty. Set a PostgreSQL 18 URL and retry posting."
            )
        _require_reference(tenant_reference, "tenant reference")
        self._database_url = database_url
        self._tenant_reference = tenant_reference
        self._active_connection: object | None = None

    @property
    def journal_count(self) -> int:
        """Return the number of original and reversal journals retained for the tenant."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s
                """,
                (tenant_id,),
            ).fetchone()
        return int(row[0])

    def post(self, proposal: JournalProposal, policy: AccountingPolicy) -> PostingReceipt:
        """Persist *proposal* using a caller-supplied policy, or return its prior receipt."""
        return self._persist_proposal(proposal, policy)

    def post_proposal(self, proposal: JournalProposal) -> PostingReceipt:
        """Resolve AIS catalog policy and persist *proposal*, or return its prior receipt."""
        return self._persist_proposal(proposal, None)

    def post_adjusting_journal(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        journal_date: date,
        idempotency_key: str,
        source_payload_hash: str,
        proposal_id: str,
        transaction_currency: str,
        lines: tuple[PostedJournalLine, ...],
    ) -> None:
        """Persist one AIS-owned adjusting journal through the ordinary post tables."""
        proposal_uuid = _require_proposal_uuid(proposal_id)
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(connection, f"adjusting:{idempotency_key}")
            prior = connection.execute(
                """
                SELECT source_payload_hash
                FROM accounting_integration.journal_proposal_record
                WHERE tenant_account_id = %s AND idempotency_key = %s
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[0] != source_payload_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different payload"
                    )
                return
            legal_entity_id, functional_currency = self._load_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the journal post",
            )
            book_row = connection.execute(
                """
                SELECT accounting_book_id, reporting_currency_code, book_role_code
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s
                  AND legal_entity_id = %s
                  AND book_name = %s
                  AND valid_to IS NULL
                """,
                (tenant_id, legal_entity_id, accounting_book_reference),
            ).fetchone()
            if book_row is None:
                raise AccountingValidationError(
                    f"Accounting book {accounting_book_reference} is not recorded for this legal entity. "
                    "Create the accounting_book row, then retry the journal post."
                )
            book_id, reporting_currency_code, book_role_code = book_row
            if transaction_currency != reporting_currency_code:
                raise AccountingValidationError(
                    f"currency {transaction_currency} does not match book reporting "
                    f"currency {reporting_currency_code}. Supply the book reporting currency, "
                    "then retry the journal post."
                )
            period_state = self._load_book_period_state(
                connection, tenant_id, book_id, period_code
            )
            if period_state is None:
                raise AccountingValidationError(
                    f"Fiscal period {period_code} is not recorded for this tenant. "
                    "Create the fiscal_period row, then retry the journal post."
                )
            period_id, period_status_code, period_start, period_end = period_state
            if journal_date < period_start or journal_date > period_end:
                raise AccountingValidationError(
                    "journal_date must fall inside the supplied fiscal period. "
                    "Supply a journal_date in that period, then retry the journal post."
                )
            if period_status_code == "hard_closed":
                raise AccountingValidationError(
                    f"Fiscal period {period_code} is hard_closed. "
                    "Post the adjusting journal into an open or soft-closed period, "
                    "then retry; no journal was written."
                )
            policy_row = connection.execute(
                """
                SELECT accounting_policy_version, posting_rule_version
                FROM accounting_core.account_role_mapping
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND valid_to IS NULL
                ORDER BY account_role_code
                LIMIT 1
                """,
                (tenant_id, book_id),
            ).fetchone()
            if policy_row is None:
                raise AccountingValidationError(
                    "No account_role_mapping is effective for this book. "
                    "Create the account_role_mapping rows, then retry the journal post."
                )
            policy = AccountingPolicy(
                tenant_reference=self._tenant_reference,
                legal_entity_reference=legal_entity_reference,
                accounting_book_reference=accounting_book_reference,
                intended_book_role_code=book_role_code,
                transaction_currency=transaction_currency,
                functional_currency=functional_currency,
                open_period_start=period_start,
                open_period_end=period_end,
                chart_account_mapping={},
                accounting_policy_version=policy_row[0],
                posting_rule_version=policy_row[1],
            )
            proposal = _AdjustingProposal(
                source_payload_hash=source_payload_hash,
                transaction_currency=transaction_currency,
                transaction_date=journal_date,
                accounting_date=journal_date,
                source_event_references=(
                    f"urn:cwl:accounting:adjusting_journal:{proposal_id}",
                ),
            )
            journal_reference = f"urn:cwl:accounting:general_journal:{proposal_id}"
            receipt = PostingReceipt(
                receipt_reference=f"urn:cwl:accounting:posting_receipt:{proposal_id}",
                journal_reference=journal_reference,
                posting_status_code="posted",
                source_proposal_id=proposal_id,
                source_payload_hash=source_payload_hash,
                tenant_reference=self._tenant_reference,
                legal_entity_reference=legal_entity_reference,
                accounting_book_reference=accounting_book_reference,
                accounting_policy_version=policy.accounting_policy_version,
                posting_rule_version=policy.posting_rule_version,
                line_count=len(lines),
            )
            proposal_record_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, %s, %s, %s, %s, 'posted', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (
                    tenant_id,
                    proposal_uuid,
                    1,
                    idempotency_key,
                    source_payload_hash,
                ),
            ).fetchone()[0]
            journal_id = self._insert_journal(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
                journal_reference=journal_reference,
                proposal=proposal,
                policy=policy,
                proposal_record_id=proposal_record_id,
                lines=lines,
            )
            self._insert_receipt(
                connection, tenant_id, proposal_record_id, journal_id, receipt
            )
            self._insert_outbox(
                connection,
                tenant_id,
                "posting_receipt",
                journal_reference,
                receipt.receipt_reference,
                receipt,
            )

    def resolve_accounting_policy(self, proposal: JournalProposal) -> AccountingPolicy:
        """Load the effective catalog policy for *proposal* without posting."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            return self._resolve_accounting_policy(connection, tenant_id, proposal)

    def load_published_receipt(self, proposal: JournalProposal) -> dict[str, object]:
        """Return the schema-shaped posting receipt for a persisted *proposal*."""
        return self.load_published_receipt_by_key(proposal.idempotency_key)

    def load_published_receipt_by_key(self, idempotency_key: str) -> dict[str, object]:
        """Return the schema-shaped posting receipt for one Billing idempotency key."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            return self._load_published_receipt(connection, tenant_id, idempotency_key)

    def load_posted_journal(
        self, idempotency_key: str = "", journal_reference: str = ""
    ) -> dict[str, object]:
        """Return one persisted journal and its lines for a tenant key or reference."""
        if not idempotency_key and not journal_reference:
            raise AccountingValidationError(
                "idempotency_key or journal_reference is required. "
                "Supply the Billing key or the posted journal reference, then retry the journal read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            by_key = None
            by_reference = None
            if idempotency_key:
                by_key = self._load_journal_row(
                    connection, tenant_id, idempotency_key=idempotency_key
                )
                if by_key is None:
                    raise AccountingValidationError(
                        "posted journal is missing for this idempotency key. "
                        "Accept the proposal, then retry the journal read."
                    )
            if journal_reference:
                by_reference = self._load_journal_row(
                    connection, tenant_id, journal_reference=journal_reference
                )
                if by_reference is None:
                    raise AccountingValidationError(
                        "posted journal is missing for this journal_reference. "
                        "Accept the proposal, then retry the journal read."
                    )
            if (
                by_key is not None
                and by_reference is not None
                and by_key[0] != by_reference[0]
            ):
                raise AccountingValidationError(
                    "journal_reference and idempotency_key do not match the same posted journal. "
                    "Supply one identity, then retry the journal read."
                )
            row = by_key if by_key is not None else by_reference
            lines = self._load_lines(connection, tenant_id, row[0])
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": row[8],
                "accounting_book_reference": row[9],
                "journal_reference": row[1],
                "idempotency_key": row[10],
                "journal_status_code": row[2],
                "accounting_date": row[3].isoformat(),
                "transaction_currency": row[4],
                "functional_currency": row[5],
                "accounting_policy_version": row[6],
                "posting_rule_version": row[7],
                "source_payload_hash": row[11],
                "source_proposal_id": str(row[12]),
                "reversal_of_journal_reference": row[13],
                "reversal_reason_code": row[14],
                "lines": [
                    {
                        "line_number": line.line_number,
                        "chart_account_code": line.chart_account_code,
                        "account_role_code": line.account_role_code,
                        "debit_amount": _exact_amount_text(line.debit_amount),
                        "credit_amount": _exact_amount_text(line.credit_amount),
                    }
                    for line in lines
                ],
            }

    def load_period_journals(
        self,
        legal_entity_reference: str,
        book_reference: str,
        period_code: str,
        *,
        page_limit: int = 50,
        cursor_after: tuple[date, str] | None = None,
        journal_source_code: str = "",
    ) -> dict[str, object]:
        """Return one page of existing journals for a tenant entity, book, and period, optionally by source."""
        if not legal_entity_reference or not book_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those journal-list fields, then retry the journal list."
            )
        if journal_source_code and journal_source_code not in {
            "billing",
            "adjusting",
            "period_closing",
            "reversal",
        }:
            raise AccountingValidationError(
                "journal_source_code must be billing, adjusting, period_closing, or reversal. "
                "Supply a known journal source, then retry the journal list."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the journal list"
            )
            book_id, _currency = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                book_reference,
                "the journal list",
            )
            period_id, _status, _period_end = self._require_fiscal_period(
                connection, tenant_id, period_code, "the journal list"
            )
            if cursor_after is None:
                skip_cursor, cursor_date, cursor_reference = True, _SQL_SKIP_DATE, ""
            else:
                skip_cursor, cursor_date, cursor_reference = False, cursor_after[0], cursor_after[1]
            rows = connection.execute(
                """
                SELECT general_journal.journal_reference,
                       journal_proposal_record.idempotency_key,
                       general_journal.journal_status_code,
                       general_journal.accounting_date,
                       (
                           SELECT COUNT(*)
                           FROM accounting_core.journal_entry_line
                           WHERE tenant_account_id = general_journal.tenant_account_id
                             AND general_journal_id = general_journal.general_journal_id
                       ),
                       original_journal.journal_reference
                FROM accounting_core.general_journal
                JOIN accounting_integration.journal_proposal_record
                  ON journal_proposal_record.tenant_account_id = general_journal.tenant_account_id
                 AND journal_proposal_record.proposal_record_id
                   = general_journal.source_proposal_record_id
                LEFT JOIN accounting_core.journal_reversal
                  ON journal_reversal.tenant_account_id = general_journal.tenant_account_id
                 AND journal_reversal.reversal_journal_id = general_journal.general_journal_id
                LEFT JOIN accounting_core.general_journal AS original_journal
                  ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND original_journal.general_journal_id = journal_reversal.original_journal_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.legal_entity_id = %s
                  AND general_journal.accounting_book_id = %s
                  AND general_journal.fiscal_period_id = %s
                  AND (
                        %s
                        OR (general_journal.accounting_date, general_journal.journal_reference)
                           > (%s, %s)
                      )
                  AND (
                        %s
                        OR (%s AND journal_reversal.reversal_journal_id IS NOT NULL)
                        OR (%s AND general_journal.journal_reference LIKE %s)
                        OR (
                              %s
                              AND journal_reversal.reversal_journal_id IS NULL
                              AND EXISTS (
                                    SELECT 1
                                    FROM accounting_core.journal_entry_line
                                    WHERE journal_entry_line.tenant_account_id
                                          = general_journal.tenant_account_id
                                      AND journal_entry_line.general_journal_id
                                          = general_journal.general_journal_id
                                      AND journal_entry_line.account_role_code = 'adjusting'
                              )
                        )
                        OR (
                              %s
                              AND journal_reversal.reversal_journal_id IS NULL
                              AND general_journal.journal_reference NOT LIKE %s
                              AND NOT EXISTS (
                                    SELECT 1
                                    FROM accounting_core.journal_entry_line
                                    WHERE journal_entry_line.tenant_account_id
                                          = general_journal.tenant_account_id
                                      AND journal_entry_line.general_journal_id
                                          = general_journal.general_journal_id
                                      AND journal_entry_line.account_role_code = 'adjusting'
                              )
                        )
                      )
                ORDER BY general_journal.accounting_date, general_journal.journal_reference
                LIMIT %s
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    skip_cursor,
                    cursor_date,
                    cursor_reference,
                    journal_source_code == "",
                    journal_source_code == "reversal",
                    journal_source_code == "period_closing",
                    _CLOSING_JOURNAL_PATTERN,
                    journal_source_code == "adjusting",
                    journal_source_code == "billing",
                    _CLOSING_JOURNAL_PATTERN,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            journals = [
                {
                    "journal_reference": row[0],
                    "idempotency_key": row[1],
                    "journal_status_code": row[2],
                    "accounting_date": row[3].isoformat(),
                    "line_count": int(row[4]),
                    "reversal_of_journal_reference": row[5],
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{last[3].isoformat()}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "accounting_book_reference": book_reference,
                "book_reference": book_reference,
                "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
                "period_code": period_code,
                "journals": journals,
                "next_cursor": next_cursor,
            }
            if journal_source_code:
                document["journal_source_code"] = journal_source_code
            return document

    def load_journal_reversals(
        self,
        legal_entity_reference: str,
        original_journal_reference: str = "",
        period_code: str = "",
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, str] | None = None,
    ) -> dict[str, object]:
        """Return one page of existing journal reversals for a tenant legal entity."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the journal-reversal list"
            )
            period_id_value: object = _SQL_SKIP_UUID
            skip_period = True
            if period_code:
                period_id_value = self._require_fiscal_period(
                    connection, tenant_id, period_code, "the journal-reversal list"
                )[0]
                skip_period = False
            if cursor_after is None:
                skip_cursor, cursor_posted_at, cursor_reference = (
                    True,
                    _SQL_SKIP_DATETIME,
                    "",
                )
            else:
                skip_cursor, cursor_posted_at, cursor_reference = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT reversal_journal.journal_reference,
                       original_journal.journal_reference,
                       reversal_journal.accounting_date,
                       reversal_journal.posted_at,
                       journal_reversal.reversal_reason_code
                FROM accounting_core.journal_reversal
                JOIN accounting_core.general_journal AS reversal_journal
                  ON reversal_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND reversal_journal.general_journal_id = journal_reversal.reversal_journal_id
                JOIN accounting_core.general_journal AS original_journal
                  ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND original_journal.general_journal_id = journal_reversal.original_journal_id
                WHERE journal_reversal.tenant_account_id = %s
                  AND reversal_journal.legal_entity_id = %s
                  AND (%s OR original_journal.journal_reference = %s)
                  AND (%s OR reversal_journal.fiscal_period_id = %s)
                  AND (
                        %s
                        OR (reversal_journal.posted_at, reversal_journal.journal_reference)
                           > (%s, %s)
                      )
                ORDER BY reversal_journal.posted_at, reversal_journal.journal_reference
                LIMIT %s
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    not original_journal_reference,
                    original_journal_reference,
                    skip_period,
                    period_id_value,
                    skip_cursor,
                    cursor_posted_at,
                    cursor_reference,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            journal_reversals = [
                {
                    "reversal_journal_reference": row[0],
                    "original_journal_reference": row[1],
                    "reversal_date": row[2].isoformat(),
                    "posted_at": _format_timestamp(row[3]),
                    "reversal_reason_code": row[4],
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[3])}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "journal_reversals": journal_reversals,
                "next_cursor": next_cursor,
            }
            if original_journal_reference:
                document["original_journal_reference"] = original_journal_reference
            if period_code:
                document["fiscal_period_reference"] = (
                    f"urn:cwl:accounting:fiscal_period:{period_code}"
                )
            return document

    def load_period_closes(
        self,
        legal_entity_reference: str,
        period_code: str = "",
        period_status_code: str = "",
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, UUID] | None = None,
    ) -> dict[str, object]:
        """Return one page of durable hard-close receipts for a tenant legal entity."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the period-close list"
            )
            period_id_value: object = _SQL_SKIP_UUID
            skip_period = True
            if period_code:
                period_id_value = self._require_fiscal_period(
                    connection, tenant_id, period_code, "the period-close list"
                )[0]
                skip_period = False
            if cursor_after is None:
                skip_cursor, cursor_generated_at, cursor_snapshot_id = (
                    True,
                    _SQL_SKIP_DATETIME,
                    _SQL_SKIP_UUID,
                )
            else:
                skip_cursor, cursor_generated_at, cursor_snapshot_id = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT trial_balance_snapshot.trial_balance_snapshot_id,
                       trial_balance_snapshot.snapshot_generated_at,
                       trial_balance_snapshot.source_journal_count,
                       trial_balance_snapshot.source_payload_hash,
                       fiscal_period.period_code,
                       accounting_book_period_control.period_status_code,
                       accounting_book.book_name,
                       legal_entity_record.legal_entity_code
                FROM accounting_reporting.trial_balance_snapshot
                JOIN accounting_core.fiscal_period
                  ON fiscal_period.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND fiscal_period.fiscal_period_id = trial_balance_snapshot.fiscal_period_id
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND accounting_book.accounting_book_id = trial_balance_snapshot.accounting_book_id
                JOIN accounting_core.accounting_book_period_control
                  ON accounting_book_period_control.tenant_account_id
                     = trial_balance_snapshot.tenant_account_id
                 AND accounting_book_period_control.accounting_book_id
                     = trial_balance_snapshot.accounting_book_id
                 AND accounting_book_period_control.fiscal_period_id
                     = trial_balance_snapshot.fiscal_period_id
                JOIN accounting_core.legal_entity_record
                  ON legal_entity_record.tenant_account_id = trial_balance_snapshot.tenant_account_id
                 AND legal_entity_record.legal_entity_id = trial_balance_snapshot.legal_entity_id
                WHERE trial_balance_snapshot.tenant_account_id = %s
                  AND trial_balance_snapshot.legal_entity_id = %s
                  AND (%s OR trial_balance_snapshot.fiscal_period_id = %s)
                  AND (%s OR accounting_book_period_control.period_status_code = %s)
                  AND (
                        %s
                        OR (
                              trial_balance_snapshot.snapshot_generated_at,
                              trial_balance_snapshot.trial_balance_snapshot_id
                           ) > (%s, %s)
                      )
                ORDER BY trial_balance_snapshot.snapshot_generated_at,
                         trial_balance_snapshot.trial_balance_snapshot_id
                LIMIT %s
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    skip_period,
                    period_id_value,
                    not period_status_code,
                    period_status_code,
                    skip_cursor,
                    cursor_generated_at,
                    cursor_snapshot_id,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            period_closes = [
                {
                    "tenant_reference": self._tenant_reference,
                    "legal_entity_reference": row[7],
                    "accounting_book_reference": row[6],
                    "period_code": row[4],
                    "period_status_code": row[5],
                    "snapshot_record_id": str(row[0]),
                    "snapshot_generated_at": _format_timestamp(row[1]),
                    "source_journal_count": int(row[2]),
                    "source_payload_hash": row[3],
                    "replayed": False,
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[1])}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "period_closes": period_closes,
                "next_cursor": next_cursor,
            }
            if period_code:
                document["fiscal_period_reference"] = (
                    f"urn:cwl:accounting:fiscal_period:{period_code}"
                )
            if period_status_code:
                document["period_status_code"] = period_status_code
            return document

    def load_unpublished_outbox_events(
        self,
        event_type_code: str,
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, UUID] | None = None,
    ) -> dict[str, object]:
        """Return one page of unpublished outbox rows for one tenant event type."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            if cursor_after is None:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    True,
                    _SQL_SKIP_DATETIME,
                    _SQL_SKIP_UUID,
                )
            else:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT outbox_event.outbox_event_id,
                       outbox_event.event_type_code,
                       outbox_event.aggregate_reference,
                       outbox_event.payload_reference,
                       outbox_event.payload_hash,
                       outbox_event.created_at
                FROM accounting_integration.outbox_event
                WHERE outbox_event.tenant_account_id = %s
                  AND outbox_event.event_type_code = %s
                  AND outbox_event.published_at IS NULL
                  AND (
                        %s
                        OR (outbox_event.created_at, outbox_event.outbox_event_id)
                           > (%s, %s)
                      )
                ORDER BY outbox_event.created_at, outbox_event.outbox_event_id
                LIMIT %s
                """,
                (
                    tenant_id,
                    event_type_code,
                    skip_cursor,
                    cursor_created_at,
                    cursor_event_id,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            events = [
                {
                    "outbox_event_id": str(row[0]),
                    "event_type_code": row[1],
                    "aggregate_reference": row[2],
                    "payload_reference": row[3],
                    "payload_hash": row[4],
                    "created_at": _format_timestamp(row[5]),
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[5])}|{last[0]}"
            return {
                "tenant_reference": self._tenant_reference,
                "event_type_code": event_type_code,
                "outbox_events": events,
                "next_cursor": next_cursor,
            }

    def load_audit_events(
        self,
        event_type_code: str = "",
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, UUID] | None = None,
    ) -> dict[str, object]:
        """Return one page of published and unpublished outbox rows for one tenant."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            if cursor_after is None:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    True,
                    _SQL_SKIP_DATETIME,
                    _SQL_SKIP_UUID,
                )
            else:
                skip_cursor, cursor_created_at, cursor_event_id = (
                    False,
                    cursor_after[0],
                    cursor_after[1],
                )
            rows = connection.execute(
                """
                SELECT outbox_event.outbox_event_id,
                       outbox_event.event_type_code,
                       outbox_event.aggregate_reference,
                       outbox_event.payload_reference,
                       outbox_event.payload_hash,
                       outbox_event.created_at,
                       outbox_event.published_at
                FROM accounting_integration.outbox_event
                WHERE outbox_event.tenant_account_id = %s
                  AND (%s OR outbox_event.event_type_code = %s)
                  AND (
                        %s
                        OR (outbox_event.created_at, outbox_event.outbox_event_id)
                           > (%s, %s)
                      )
                ORDER BY outbox_event.created_at, outbox_event.outbox_event_id
                LIMIT %s
                """,
                (
                    tenant_id,
                    not event_type_code,
                    event_type_code,
                    skip_cursor,
                    cursor_created_at,
                    cursor_event_id,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            events = [
                {
                    "outbox_event_id": str(row[0]),
                    "event_type_code": row[1],
                    "aggregate_reference": row[2],
                    "payload_reference": row[3],
                    "payload_hash": row[4],
                    "created_at": _format_timestamp(row[5]),
                    "published_at": (
                        None if row[6] is None else _format_timestamp(row[6])
                    ),
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[5])}|{last[0]}"
            document: dict[str, object] = {
                "tenant_reference": self._tenant_reference,
                "audit_events": events,
                "next_cursor": next_cursor,
            }
            if event_type_code:
                document["event_type_code"] = event_type_code
            return document

    def publish_outbox_event(self, outbox_event_id: str) -> dict[str, object]:
        """Set published_at on one tenant outbox row, or replay an already-published row."""
        if not outbox_event_id:
            raise AccountingValidationError(
                "outbox_event_id is required. "
                "Supply the outbox event id, then retry the outbox publish."
            )
        try:
            event_id = UUID(outbox_event_id)
        except ValueError as error:
            raise AccountingValidationError(
                "outbox_event_id must be a UUID. "
                "Supply the outbox event id, then retry the outbox publish."
            ) from error
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            updated = connection.execute(
                """
                UPDATE accounting_integration.outbox_event
                SET published_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND outbox_event_id = %s
                  AND published_at IS NULL
                RETURNING outbox_event_id, event_type_code, aggregate_reference,
                          payload_reference, payload_hash, created_at, published_at
                """,
                (tenant_id, event_id),
            ).fetchone()
            row = updated
            if row is None:
                row = connection.execute(
                    """
                    SELECT outbox_event_id, event_type_code, aggregate_reference,
                           payload_reference, payload_hash, created_at, published_at
                    FROM accounting_integration.outbox_event
                    WHERE tenant_account_id = %s AND outbox_event_id = %s
                    """,
                    (tenant_id, event_id),
                ).fetchone()
            if row is None:
                raise AccountingValidationError(
                    "outbox event is missing for this outbox_event_id. "
                    "Accept the proposal, then retry the outbox publish."
                )
            return {
                "outbox_event_id": str(row[0]),
                "event_type_code": row[1],
                "aggregate_reference": row[2],
                "payload_reference": row[3],
                "payload_hash": row[4],
                "created_at": _format_timestamp(row[5]),
                "published_at": _format_timestamp(row[6]),
            }

    def _persist_proposal(
        self, proposal: JournalProposal, policy: AccountingPolicy | None
    ) -> PostingReceipt:
        """Resolve optional catalog policy and persist *proposal* in one transaction."""
        proposal_uuid = _require_proposal_uuid(proposal.proposal_id)
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(
                connection, f"proposal:{proposal.idempotency_key}"
            )
            prior = connection.execute(
                """
                SELECT source_payload_hash
                FROM accounting_integration.journal_proposal_record
                WHERE tenant_account_id = %s AND idempotency_key = %s
                """,
                (tenant_id, proposal.idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior[0] != proposal.source_payload_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different payload"
                    )
                return self._receipt_for_idempotency_key(connection, tenant_id, proposal)
            if any(line.account_role_code == "retained_earnings" for line in proposal.lines):
                raise AccountingValidationError(
                    "retained_earnings is reserved for AIS period-close. "
                    "Post revenue and expense through Billing, then hard-close; "
                    "no journal was written."
                )
            if policy is None:
                policy = self._resolve_accounting_policy(connection, tenant_id, proposal)
            PostingLedger._validate_policy_scope(proposal, policy)
            resolved_lines = tuple(
                PostingLedger._resolve_line(line, policy) for line in proposal.lines
            )
            legal_entity_id = self._require_legal_entity(
                connection, tenant_id, proposal.legal_entity_reference
            )
            book_id = self._require_book(
                connection,
                tenant_id,
                legal_entity_id,
                policy.intended_book_role_code,
                policy.accounting_book_reference,
            )
            period_id = self._require_open_book_period(
                connection, tenant_id, book_id, proposal.accounting_date
            )
            journal_reference = f"urn:cwl:accounting:general_journal:{proposal.proposal_id}"
            receipt = PostingReceipt(
                receipt_reference=f"urn:cwl:accounting:posting_receipt:{proposal.proposal_id}",
                journal_reference=journal_reference,
                posting_status_code="posted",
                source_proposal_id=proposal.proposal_id,
                source_payload_hash=proposal.source_payload_hash,
                tenant_reference=proposal.tenant_reference,
                legal_entity_reference=proposal.legal_entity_reference,
                accounting_book_reference=policy.accounting_book_reference,
                accounting_policy_version=policy.accounting_policy_version,
                posting_rule_version=policy.posting_rule_version,
                line_count=len(resolved_lines),
            )
            proposal_record_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, %s, %s, %s, %s, 'posted', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (
                    tenant_id,
                    proposal_uuid,
                    proposal.proposal_contract_version,
                    proposal.idempotency_key,
                    proposal.source_payload_hash,
                ),
            ).fetchone()[0]
            journal_id = self._insert_journal(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=legal_entity_id,
                book_id=book_id,
                period_id=period_id,
                journal_reference=journal_reference,
                proposal=proposal,
                policy=policy,
                proposal_record_id=proposal_record_id,
                lines=resolved_lines,
            )
            self._insert_receipt(
                connection, tenant_id, proposal_record_id, journal_id, receipt
            )
            self._insert_outbox(
                connection,
                tenant_id,
                "posting_receipt",
                journal_reference,
                receipt.receipt_reference,
                receipt,
            )
            return receipt

    def close_fiscal_period(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        snapshot_currency_code: str,
        period_status_code: str = "hard_closed",
        idempotency_key: str = "",
    ) -> PeriodCloseReceipt:
        """Soft-close or hard-close one fiscal period; only hard-close snapshots the book."""
        _require_reference(legal_entity_reference, "legal entity reference")
        _require_reference(accounting_book_reference, "accounting book reference")
        if not period_code.strip():
            raise AccountingValidationError(
                "period_code is required. Supply the fiscal period code, then retry the close."
            )
        close_idempotency_key = idempotency_key.strip() or (
            f"{self._tenant_reference}:period_close:{accounting_book_reference}:{period_code}"
        )
        try:
            _require_currency(snapshot_currency_code)
        except AccountingValidationError as error:
            raise AccountingValidationError(
                "snapshot_currency_code must be a three-letter ISO currency. "
                "Supply the book reporting currency, then retry the close."
            ) from error
        if period_status_code not in {"soft_closed", "hard_closed"}:
            raise AccountingValidationError(
                "period_status_code must be soft_closed or hard_closed. "
                "Supply one of those codes, then retry the close."
            )
        with self._session() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self._active_connection = connection
            try:
                tenant_id = self._require_tenant(connection)
                self._acquire_command_lock(
                    connection, f"period:{accounting_book_reference}:{period_code}"
                )
                legal_entity_id = self._require_legal_entity(
                    connection,
                    tenant_id,
                    legal_entity_reference,
                    next_action="the close",
                )
                book_id, reporting_currency_code = self._require_book_for_close(
                    connection, tenant_id, legal_entity_id, accounting_book_reference
                )
                if snapshot_currency_code != reporting_currency_code:
                    raise AccountingValidationError(
                        f"snapshot currency {snapshot_currency_code} does not match book reporting "
                        f"currency {reporting_currency_code}. Supply the book reporting currency, "
                        "then retry the close."
                    )
                period_id, current_status, period_end_date = self._lock_book_period(
                    connection, tenant_id, book_id, period_code
                )
                if current_status == "hard_closed":
                    if period_status_code == "soft_closed":
                        raise AccountingValidationError(
                            f"Fiscal period {period_code} is hard_closed. "
                            "Hard-closed periods cannot be soft-closed. "
                            "Open a later period or leave this period hard_closed; "
                            "no close row was written."
                        )
                    return self._replay_close_receipt(
                        connection,
                        tenant_id=tenant_id,
                        legal_entity_id=legal_entity_id,
                        book_id=book_id,
                        period_id=period_id,
                        period_code=period_code,
                        current_status=current_status,
                        legal_entity_reference=legal_entity_reference,
                        accounting_book_reference=accounting_book_reference,
                        idempotency_key=close_idempotency_key,
                    )
                if current_status == period_status_code:
                    return self._replay_soft_close_receipt(
                        connection,
                        tenant_id=tenant_id,
                        legal_entity_id=legal_entity_id,
                        book_id=book_id,
                        period_id=period_id,
                        period_code=period_code,
                        period_end_date=period_end_date,
                        snapshot_currency_code=snapshot_currency_code,
                        legal_entity_reference=legal_entity_reference,
                        accounting_book_reference=accounting_book_reference,
                        idempotency_key=close_idempotency_key,
                    )
                if period_status_code == "soft_closed":
                    return self._persist_soft_close(
                        connection,
                        tenant_id=tenant_id,
                        legal_entity_id=legal_entity_id,
                        book_id=book_id,
                        period_id=period_id,
                        period_code=period_code,
                        period_end_date=period_end_date,
                        snapshot_currency_code=snapshot_currency_code,
                        legal_entity_reference=legal_entity_reference,
                        accounting_book_reference=accounting_book_reference,
                        idempotency_key=close_idempotency_key,
                    )
                package = self._assemble_period_close_package(
                    legal_entity_reference,
                    accounting_book_reference,
                    period_code,
                )
                self._require_closeable_package(package)
                return self._persist_period_close(
                    connection,
                    tenant_id=tenant_id,
                    legal_entity_id=legal_entity_id,
                    book_id=book_id,
                    period_id=period_id,
                    period_code=period_code,
                    period_end_date=period_end_date,
                    period_status_code=period_status_code,
                    snapshot_currency_code=snapshot_currency_code,
                    legal_entity_reference=legal_entity_reference,
                    accounting_book_reference=accounting_book_reference,
                    idempotency_key=close_idempotency_key,
                )
            finally:
                self._active_connection = None

    def open_fiscal_period(
        self,
        legal_entity_reference: str,
        period_code: str,
        period_start_date: date | None = None,
        period_end_date: date | None = None,
        *,
        idempotency_key: str,
        source_payload_hash: str,
    ) -> dict[str, object]:
        """Insert or replay one fiscal-period-open command from durable evidence."""
        if not legal_entity_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference and fiscal_period_reference are required. "
                "Supply those period-open fields, then retry the period open."
            )
        command_key = idempotency_key.strip()
        if not command_key or command_key != idempotency_key:
            raise AccountingValidationError(
                "period-open idempotency_key must be a canonical non-empty string. "
                "Supply the original command key, then retry the period open."
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", source_payload_hash) is None:
            raise AccountingValidationError(
                "period-open source_payload_hash must be a canonical sha256 digest. "
                "Supply the immutable command hash, then retry the period open."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(connection, f"period-open:{command_key}")
            self._acquire_command_lock(connection, f"period:{period_code}")
            legal_entity_id, _functional_currency = self._load_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the period open",
            )
            prior = connection.execute(
                """
                SELECT period_open_command.legal_entity_id,
                       fiscal_period.period_code,
                       period_open_command.requested_period_start_date,
                       period_open_command.requested_period_end_date,
                       fiscal_period.period_start_date,
                       fiscal_period.period_end_date,
                       period_open_command.source_payload_hash
                FROM accounting_integration.fiscal_period_open_command AS period_open_command
                JOIN accounting_core.fiscal_period AS fiscal_period
                  ON fiscal_period.tenant_account_id = period_open_command.tenant_account_id
                 AND fiscal_period.fiscal_period_id = period_open_command.fiscal_period_id
                WHERE period_open_command.tenant_account_id = %s
                  AND period_open_command.period_open_idempotency_key = %s
                """,
                (tenant_id, command_key),
            ).fetchone()
            if prior is not None:
                (
                    prior_legal_entity_id,
                    prior_period_code,
                    prior_requested_start,
                    prior_requested_end,
                    stored_start_date,
                    stored_end_date,
                    prior_source_hash,
                ) = prior
                if (
                    prior_legal_entity_id != legal_entity_id
                    or prior_period_code != period_code
                    or prior_requested_start != period_start_date
                    or prior_requested_end != period_end_date
                    or prior_source_hash != source_payload_hash
                ):
                    raise IdempotencyConflictError(
                        "period-open idempotency key was already used with a different payload"
                    )
                return self._period_open_document(
                    legal_entity_reference,
                    period_code,
                    stored_start_date,
                    stored_end_date,
                    replayed=True,
                )

            existing = self._load_period_state(connection, tenant_id, period_code)
            replayed = existing is not None
            if existing is not None:
                period_id, current_status, stored_start_date, stored_end_date = existing
                if current_status != "open":
                    raise AccountingValidationError(
                        f"Fiscal period {period_code} is {current_status}. "
                        "Closed periods cannot be reopened. Open a later period, "
                        "then retry the period open."
                    )
                if (
                    period_start_date is not None
                    and period_start_date != stored_start_date
                ) or (
                    period_end_date is not None and period_end_date != stored_end_date
                ):
                    raise AccountingValidationError(
                        "period-open dates do not match the already-open fiscal period. "
                        "Supply its existing dates or omit both dates, then retry."
                    )
            else:
                if period_start_date is None or period_end_date is None:
                    raise AccountingValidationError(
                        "period_start_date and period_end_date are required. "
                        "Supply those fiscal_period dates, then retry the period open."
                    )
                if period_end_date < period_start_date:
                    raise AccountingValidationError(
                        "period_end_date must be on or after period_start_date. "
                        "Supply a valid date range, then retry the period open."
                    )
                calendar_id = self._require_tenant_calendar(connection, tenant_id)
                period_id = connection.execute(
                    """
                    INSERT INTO accounting_core.fiscal_period (
                        tenant_account_id, fiscal_calendar_id, period_code,
                        period_start_date, period_end_date, period_status_code
                    )
                    VALUES (%s, %s, %s, %s, %s, 'open')
                    RETURNING fiscal_period_id
                    """,
                    (
                        tenant_id,
                        calendar_id,
                        period_code,
                        period_start_date,
                        period_end_date,
                    ),
                ).fetchone()[0]
                stored_start_date = period_start_date
                stored_end_date = period_end_date

            connection.execute(
                """
                INSERT INTO accounting_integration.fiscal_period_open_command (
                    tenant_account_id,
                    legal_entity_id,
                    fiscal_period_id,
                    period_open_idempotency_key,
                    source_payload_hash,
                    requested_period_start_date,
                    requested_period_end_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    period_id,
                    command_key,
                    source_payload_hash,
                    period_start_date,
                    period_end_date,
                ),
            )
            return self._period_open_document(
                legal_entity_reference,
                period_code,
                stored_start_date,
                stored_end_date,
                replayed=replayed,
            )

    def load_fiscal_period(
        self, legal_entity_reference: str, period_code: str
    ) -> dict[str, object]:
        """Return persisted fiscal-period status and dates for one tenant entity."""
        if not legal_entity_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference and fiscal_period_reference are required. "
                "Supply those period fields, then retry the period read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the period read"
            )
            existing = self._load_period_state(connection, tenant_id, period_code)
            if existing is None:
                raise AccountingValidationError(
                    f"Fiscal period {period_code} is not recorded for this tenant. "
                    "Create the fiscal_period row, then retry the period read."
                )
            _period_id, current_status, start_date, end_date = existing
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
                "period_code": period_code,
                "period_status_code": current_status,
                "period_start_date": start_date.isoformat(),
                "period_end_date": end_date.isoformat(),
            }

    def load_fiscal_periods(
        self,
        legal_entity_reference: str,
        *,
        page_limit: int = 50,
        cursor_after: tuple[date, str] | None = None,
    ) -> dict[str, object]:
        """Return one page of existing fiscal periods for a tenant legal entity."""
        if not legal_entity_reference:
            raise AccountingValidationError(
                "legal_entity_reference is required. "
                "Supply that period-list field, then retry the period list."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the period list"
            )
            calendar_row = connection.execute(
                """
                SELECT fiscal_calendar_id
                FROM accounting_core.fiscal_calendar
                WHERE tenant_account_id = %s
                ORDER BY calendar_code
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()
            periods: list[dict[str, object]] = []
            next_cursor = None
            if calendar_row is not None:
                if cursor_after is None:
                    skip_cursor, cursor_start_date, cursor_period_code = (
                        True,
                        _SQL_SKIP_DATE,
                        "",
                    )
                else:
                    skip_cursor, cursor_start_date, cursor_period_code = (
                        False,
                        cursor_after[0],
                        cursor_after[1],
                    )
                rows = connection.execute(
                    """
                    SELECT fiscal_period.period_code,
                           fiscal_period.period_start_date,
                           fiscal_period.period_end_date,
                           fiscal_period.period_status_code
                    FROM accounting_core.fiscal_period
                    WHERE fiscal_period.tenant_account_id = %s
                      AND fiscal_period.fiscal_calendar_id = %s
                      AND (
                            %s
                            OR (fiscal_period.period_start_date, fiscal_period.period_code)
                               > (%s, %s)
                          )
                    ORDER BY fiscal_period.period_start_date, fiscal_period.period_code
                    LIMIT %s
                    """,
                    (
                        tenant_id,
                        calendar_row[0],
                        skip_cursor,
                        cursor_start_date,
                        cursor_period_code,
                        page_limit + 1,
                    ),
                ).fetchall()
                has_more = len(rows) > page_limit
                page_rows = rows[:page_limit]
                periods = [
                    {
                        "fiscal_period_reference": (
                            f"urn:cwl:accounting:fiscal_period:{row[0]}"
                        ),
                        "period_code": row[0],
                        "period_start_date": row[1].isoformat(),
                        "period_end_date": row[2].isoformat(),
                        "period_status_code": row[3],
                    }
                    for row in page_rows
                ]
                if has_more:
                    last = page_rows[-1]
                    next_cursor = f"{last[1].isoformat()}|{last[0]}"
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "fiscal_periods": periods,
                "next_cursor": next_cursor,
            }

    def load_account_rollforward(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        chart_account_code: str,
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        """Return opening + period = closing sides for one chart account and scope."""
        if statement_scope_code not in {"", "period", "year_to_date"}:
            raise AccountingValidationError(
                "statement_scope_code must be period or year_to_date. "
                "Supply a known statement scope, then retry the account-rollforward read."
            )
        if not chart_account_code:
            raise AccountingValidationError(
                "chart_account_code is required. "
                "Supply that account-rollforward field, then retry the account-rollforward read."
            )
        account_classes = self._load_chart_account_classes(
            legal_entity_reference, accounting_book_reference
        )
        if chart_account_code not in account_classes:
            raise AccountingValidationError(
                f"Chart account {chart_account_code} is not recorded for this book. "
                "Create the chart_account row, then retry the account-rollforward read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the account-rollforward read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the account-rollforward read",
            )[0]
            self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the account-rollforward read",
            )
            period_ids = self._statement_period_ids(
                connection,
                tenant_id,
                period_code,
                statement_scope_code,
            )
            scope_start = connection.execute(
                """
                SELECT MIN(period_start_date)
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND fiscal_period_id = ANY(%s)
                """,
                (tenant_id, period_ids),
            ).fetchone()[0]
            opening_debit_amount, opening_credit_amount = self._opening_account_sides(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                chart_account_code,
                scope_start,
            )
            period_debit_amount, period_credit_amount = self._period_account_sides(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                chart_account_code,
                period_ids,
            )
        closing_debit_amount = opening_debit_amount + period_debit_amount
        closing_credit_amount = opening_credit_amount + period_credit_amount
        document = {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "accounting_book_reference": accounting_book_reference,
            "book_reference": accounting_book_reference,
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
            "chart_account_code": chart_account_code,
            "account_class_code": account_classes[chart_account_code],
            "opening_debit_amount": _exact_amount_text(opening_debit_amount),
            "opening_credit_amount": _exact_amount_text(opening_credit_amount),
            "period_debit_amount": _exact_amount_text(period_debit_amount),
            "period_credit_amount": _exact_amount_text(period_credit_amount),
            "closing_debit_amount": _exact_amount_text(closing_debit_amount),
            "closing_credit_amount": _exact_amount_text(closing_credit_amount),
        }
        if statement_scope_code == "year_to_date":
            document["statement_scope_code"] = "year_to_date"
        return document

    def load_unapplied_cash_rollforward(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
    ) -> dict[str, object]:
        """Return leftover-cash opening, park / apply / refund, and closing for 210200."""
        if not legal_entity_reference or not accounting_book_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those unapplied-cash-rollforward fields, then retry the unapplied-cash-rollforward read."
            )
        account_classes = self._load_chart_account_classes(
            legal_entity_reference, accounting_book_reference
        )
        if "210200" not in account_classes:
            raise AccountingValidationError(
                "Chart account 210200 is not recorded for this book. "
                "Create the chart_account row, then retry the unapplied-cash-rollforward read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the unapplied-cash-rollforward read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the unapplied-cash-rollforward read",
            )[0]
            period_id, _period_status, period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the unapplied-cash-rollforward read",
            )
            period_start_date = connection.execute(
                """
                SELECT period_start_date
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND fiscal_period_id = %s
                """,
                (tenant_id, period_id),
            ).fetchone()[0]
            opening_debit_amount, opening_credit_amount = self._opening_account_sides(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                "210200",
                period_start_date,
            )
            line_rows = connection.execute(
                """
                SELECT COALESCE(journal_proposal_record.idempotency_key, ''),
                       general_journal.journal_reference,
                       journal_entry_line.account_role_code,
                       chart_account.chart_account_code,
                       journal_entry_line.debit_amount,
                       journal_entry_line.credit_amount
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                LEFT JOIN accounting_integration.journal_proposal_record
                  ON journal_proposal_record.tenant_account_id = general_journal.tenant_account_id
                 AND journal_proposal_record.proposal_record_id
                   = general_journal.source_proposal_record_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.legal_entity_id = %s
                  AND general_journal.accounting_book_id = %s
                  AND general_journal.accounting_date >= %s
                  AND general_journal.accounting_date <= %s
                  AND general_journal.journal_reference NOT LIKE %s
                ORDER BY general_journal.journal_reference, journal_entry_line.line_number
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_start_date,
                    period_end_date,
                    _CLOSING_JOURNAL_PATTERN,
                ),
            ).fetchall()
        journals: dict[str, dict[str, object]] = {}
        for (
            idempotency_key,
            journal_reference,
            account_role_code,
            chart_account_code,
            debit_amount,
            credit_amount,
        ) in line_rows:
            bucket = journals.setdefault(
                str(journal_reference),
                {
                    "idempotency_key": str(idempotency_key),
                    "debit_roles": set(),
                    "credit_roles": set(),
                    "unapplied_debit_amount": Decimal("0"),
                    "unapplied_credit_amount": Decimal("0"),
                },
            )
            line_debit_amount = Decimal(str(debit_amount))
            line_credit_amount = Decimal(str(credit_amount))
            debit_roles = bucket["debit_roles"]
            credit_roles = bucket["credit_roles"]
            assert isinstance(debit_roles, set)
            assert isinstance(credit_roles, set)
            if line_debit_amount > 0:
                debit_roles.add(str(account_role_code))
            if line_credit_amount > 0:
                credit_roles.add(str(account_role_code))
            if str(chart_account_code) == "210200":
                bucket["unapplied_debit_amount"] = (
                    Decimal(str(bucket["unapplied_debit_amount"])) + line_debit_amount
                )
                bucket["unapplied_credit_amount"] = (
                    Decimal(str(bucket["unapplied_credit_amount"])) + line_credit_amount
                )
        parked_amount = Decimal("0")
        applied_amount = Decimal("0")
        refunded_amount = Decimal("0")
        other_movement_amount = Decimal("0")
        for bucket in journals.values():
            unapplied_debit_amount = Decimal(str(bucket["unapplied_debit_amount"]))
            unapplied_credit_amount = Decimal(str(bucket["unapplied_credit_amount"]))
            if unapplied_debit_amount == 0 and unapplied_credit_amount == 0:
                continue
            debit_roles = bucket["debit_roles"]
            credit_roles = bucket["credit_roles"]
            assert isinstance(debit_roles, set)
            assert isinstance(credit_roles, set)
            movement_kind = _unapplied_cash_movement_kind(
                str(bucket["idempotency_key"]),
                debit_roles,
                credit_roles,
            )
            if movement_kind == "parked":
                parked_amount += unapplied_credit_amount
            elif movement_kind == "applied":
                applied_amount += unapplied_debit_amount
            elif movement_kind == "refunded":
                refunded_amount += unapplied_debit_amount
            else:
                other_movement_amount += unapplied_credit_amount - unapplied_debit_amount
        opening_amount = opening_credit_amount - opening_debit_amount
        closing_amount = (
            opening_amount + parked_amount - applied_amount - refunded_amount + other_movement_amount
        )
        document: dict[str, object] = {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "accounting_book_reference": accounting_book_reference,
            "book_reference": accounting_book_reference,
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
            "as_of_date": period_end_date.isoformat(),
            "chart_account_code": "210200",
            "account_role_code": "unapplied_cash",
            "parked_amount": _unsigned_aging_amount_text(parked_amount),
            "applied_amount": _unsigned_aging_amount_text(applied_amount),
            "refunded_amount": _unsigned_aging_amount_text(refunded_amount),
            "opening_amount": _unsigned_aging_amount_text(opening_amount),
            "closing_amount": _unsigned_aging_amount_text(closing_amount),
        }
        if other_movement_amount != 0:
            document["other_movement_amount"] = _unsigned_aging_amount_text(
                other_movement_amount
            )
        return document

    def load_vat_period_register(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
    ) -> dict[str, object]:
        """Return issued, voided, and closing tax-payable amounts for catalog 210100."""
        if not legal_entity_reference or not accounting_book_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those vat-period-register fields, then retry the vat-period-register read."
            )
        account_classes = self._load_chart_account_classes(
            legal_entity_reference, accounting_book_reference
        )
        if "210100" not in account_classes:
            raise AccountingValidationError(
                "Chart account 210100 is not recorded for this book. "
                "Create the chart_account row, then retry the vat-period-register read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the vat-period-register read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the vat-period-register read",
            )[0]
            _period_id, _period_status, period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the vat-period-register read",
            )
            line_rows = connection.execute(
                """
                SELECT COALESCE(journal_proposal_record.idempotency_key, ''),
                       general_journal.journal_reference,
                       journal_entry_line.account_role_code,
                       chart_account.chart_account_code,
                       journal_entry_line.debit_amount,
                       journal_entry_line.credit_amount
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                LEFT JOIN accounting_integration.journal_proposal_record
                  ON journal_proposal_record.tenant_account_id = general_journal.tenant_account_id
                 AND journal_proposal_record.proposal_record_id
                   = general_journal.source_proposal_record_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.legal_entity_id = %s
                  AND general_journal.accounting_book_id = %s
                  AND general_journal.accounting_date <= %s
                  AND general_journal.journal_reference NOT LIKE %s
                ORDER BY general_journal.journal_reference, journal_entry_line.line_number
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_end_date,
                    _CLOSING_JOURNAL_PATTERN,
                ),
            ).fetchall()
        journals: dict[str, dict[str, object]] = {}
        for (
            idempotency_key,
            journal_reference,
            account_role_code,
            chart_account_code,
            debit_amount,
            credit_amount,
        ) in line_rows:
            bucket = journals.setdefault(
                str(journal_reference),
                {
                    "idempotency_key": str(idempotency_key),
                    "debit_roles": set(),
                    "credit_roles": set(),
                    "tax_debit_amount": Decimal("0"),
                    "tax_credit_amount": Decimal("0"),
                },
            )
            line_debit_amount = Decimal(str(debit_amount))
            line_credit_amount = Decimal(str(credit_amount))
            debit_roles = bucket["debit_roles"]
            credit_roles = bucket["credit_roles"]
            assert isinstance(debit_roles, set)
            assert isinstance(credit_roles, set)
            if line_debit_amount > 0:
                debit_roles.add(str(account_role_code))
            if line_credit_amount > 0:
                credit_roles.add(str(account_role_code))
            if str(chart_account_code) == "210100":
                bucket["tax_debit_amount"] = (
                    Decimal(str(bucket["tax_debit_amount"])) + line_debit_amount
                )
                bucket["tax_credit_amount"] = (
                    Decimal(str(bucket["tax_credit_amount"])) + line_credit_amount
                )
        issued_amount = Decimal("0")
        voided_amount = Decimal("0")
        other_movement_amount = Decimal("0")
        for bucket in journals.values():
            tax_debit_amount = Decimal(str(bucket["tax_debit_amount"]))
            tax_credit_amount = Decimal(str(bucket["tax_credit_amount"]))
            if tax_debit_amount == 0 and tax_credit_amount == 0:
                continue
            debit_roles = bucket["debit_roles"]
            credit_roles = bucket["credit_roles"]
            assert isinstance(debit_roles, set)
            assert isinstance(credit_roles, set)
            movement_kind = _vat_period_movement_kind(
                str(bucket["idempotency_key"]),
                debit_roles,
                credit_roles,
            )
            if movement_kind == "issued":
                issued_amount += tax_credit_amount
            elif movement_kind == "voided":
                voided_amount += tax_debit_amount
            else:
                other_movement_amount += tax_credit_amount - tax_debit_amount
        closing_amount = issued_amount - voided_amount + other_movement_amount
        document: dict[str, object] = {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "accounting_book_reference": accounting_book_reference,
            "book_reference": accounting_book_reference,
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
            "as_of_date": period_end_date.isoformat(),
            "chart_account_code": "210100",
            "account_role_code": "tax_payable",
            "issued_amount": _unsigned_aging_amount_text(issued_amount),
            "voided_amount": _unsigned_aging_amount_text(voided_amount),
            "closing_amount": _unsigned_aging_amount_text(closing_amount),
        }
        if other_movement_amount != 0:
            document["other_movement_amount"] = _unsigned_aging_amount_text(
                other_movement_amount
            )
        return document

    def persist_home_tax_submission(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        submission_idempotency_key: str,
        source_payload_hash: str,
        source_payload_reference: str,
        register_document: dict[str, object],
        rejection_reason_code: str,
    ) -> dict[str, object]:
        """Persist or replay one rejected HomeTax receipt with immutable command provenance."""
        if not submission_idempotency_key:
            raise AccountingValidationError(
                "submission_idempotency_key is required. "
                "Supply the original HomeTax command key, then retry the home-tax-submission."
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", source_payload_hash) is None:
            raise AccountingValidationError(
                "source_payload_hash must be a sha256 digest. "
                "Supply immutable HomeTax source evidence, then retry the home-tax-submission."
            )
        normalized_source_reference = source_payload_reference.strip()
        if not normalized_source_reference:
            raise AccountingValidationError(
                "source_payload_reference is required. "
                "Supply the immutable HomeTax source locator, then retry the home-tax-submission."
            )
        register_payload_hash = "sha256:" + hashlib.sha256(
            json.dumps(
                register_document, separators=(",", ":"), sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        raw_as_of_date = str(register_document.get("as_of_date") or "")
        as_of_date = date.fromisoformat(raw_as_of_date) if raw_as_of_date else None
        closing_amount = Decimal(str(register_document.get("closing_amount") or "0"))
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the home-tax-submission",
            )
            self._acquire_command_lock(
                connection, f"home-tax:{submission_idempotency_key}"
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the home-tax-submission",
            )[0]
            period_id, _period_status, period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the home-tax-submission",
            )
            if as_of_date is None:
                as_of_date = period_end_date
            row = connection.execute(
                """
                INSERT INTO accounting_integration.home_tax_submission (
                    tenant_account_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    submission_idempotency_key,
                    source_payload_hash,
                    source_payload_reference,
                    submission_status_code,
                    rejection_reason_code,
                    as_of_date,
                    closing_amount,
                    register_payload_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'rejected', %s, %s, %s, %s)
                ON CONFLICT (tenant_account_id, submission_idempotency_key) DO NOTHING
                RETURNING home_tax_submission_id,
                          submission_status_code,
                          rejection_reason_code,
                          as_of_date,
                          closing_amount,
                          register_payload_hash,
                          source_payload_hash,
                          source_payload_reference,
                          legal_entity_id,
                          accounting_book_id,
                          fiscal_period_id
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_id,
                    submission_idempotency_key,
                    source_payload_hash,
                    normalized_source_reference,
                    rejection_reason_code,
                    as_of_date,
                    closing_amount,
                    register_payload_hash,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """
                    SELECT home_tax_submission_id,
                           submission_status_code,
                           rejection_reason_code,
                           as_of_date,
                           closing_amount,
                           register_payload_hash,
                           source_payload_hash,
                           source_payload_reference,
                           legal_entity_id,
                           accounting_book_id,
                           fiscal_period_id
                    FROM accounting_integration.home_tax_submission
                    WHERE tenant_account_id = %s
                      AND submission_idempotency_key = %s
                    """,
                    (tenant_id, submission_idempotency_key),
                ).fetchone()
                if row is None:
                    raise AccountingValidationError(
                        "HomeTax command replay could not find its existing receipt. "
                        "Retry the command with the same idempotency key."
                    )
                if (
                    row[5] != register_payload_hash
                    or row[6] != source_payload_hash
                    or row[7] != normalized_source_reference
                    or row[8] != legal_entity_id
                    or row[9] != book_id
                    or row[10] != period_id
                ):
                    raise IdempotencyConflictError(
                        "HomeTax idempotency key was already used with different evidence or scope. "
                        "Use a new command key for the changed submission."
                    )
        receipt_register = _home_tax_register_view(register_document)
        if not receipt_register.get("as_of_date"):
            receipt_register["as_of_date"] = row[3].isoformat()
        return _home_tax_submission_document(
            home_tax_submission_id=str(row[0]),
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            book_reference=accounting_book_reference,
            period_code=period_code,
            vat_period_register=receipt_register,
            rejection_reason_code=str(row[2]),
            submission_status_code=str(row[1]),
        )

    def load_home_tax_submissions(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
    ) -> dict[str, object]:
        """Return persisted HomeTax receipts for one tenant entity, book, and period."""
        if not legal_entity_reference or not accounting_book_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                "Supply those home-tax-submission fields, then retry the home-tax-submission read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the home-tax-submission read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the home-tax-submission read",
            )[0]
            period_id, _period_status, _period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the home-tax-submission read",
            )
            rows = connection.execute(
                """
                SELECT home_tax_submission_id,
                       submission_status_code,
                       rejection_reason_code,
                       as_of_date,
                       closing_amount
                FROM accounting_integration.home_tax_submission
                WHERE tenant_account_id = %s
                  AND legal_entity_id = %s
                  AND accounting_book_id = %s
                  AND fiscal_period_id = %s
                ORDER BY created_at, home_tax_submission_id
                """,
                (tenant_id, legal_entity_id, book_id, period_id),
            ).fetchall()
        return {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "book_reference": accounting_book_reference,
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
            "home_tax_submissions": [
                _home_tax_submission_document(
                    home_tax_submission_id=str(row[0]),
                    tenant_reference=self._tenant_reference,
                    legal_entity_reference=legal_entity_reference,
                    book_reference=accounting_book_reference,
                    period_code=period_code,
                    vat_period_register={
                        "as_of_date": row[3].isoformat(),
                        "closing_amount": _unsigned_aging_amount_text(Decimal(row[4])),
                    },
                    rejection_reason_code=str(row[2]),
                    submission_status_code=str(row[1]),
                )
                for row in rows
            ],
        }

    def _opening_account_sides(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        chart_account_code: str,
        scope_start: date,
    ) -> tuple[Decimal, Decimal]:
        prior_snapshot = connection.execute(
            """
            SELECT trial_balance_snapshot.trial_balance_snapshot_id
            FROM accounting_core.fiscal_period
            JOIN accounting_reporting.trial_balance_snapshot
              ON trial_balance_snapshot.tenant_account_id = fiscal_period.tenant_account_id
             AND trial_balance_snapshot.fiscal_period_id = fiscal_period.fiscal_period_id
             AND trial_balance_snapshot.legal_entity_id = %s
             AND trial_balance_snapshot.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.period_end_date < %s
              AND fiscal_period.period_status_code = 'hard_closed'
            ORDER BY fiscal_period.period_end_date DESC, fiscal_period.period_code DESC
            LIMIT 1
            """,
            (legal_entity_id, book_id, tenant_id, scope_start),
        ).fetchone()
        if prior_snapshot is not None:
            row = connection.execute(
                """
                SELECT COALESCE(trial_balance_line.debit_total_amount, 0),
                       COALESCE(trial_balance_line.credit_total_amount, 0)
                FROM accounting_reporting.trial_balance_line
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = trial_balance_line.tenant_account_id
                 AND chart_account.chart_account_id = trial_balance_line.chart_account_id
                WHERE trial_balance_line.tenant_account_id = %s
                  AND trial_balance_line.trial_balance_snapshot_id = %s
                  AND chart_account.chart_account_code = %s
                """,
                (tenant_id, prior_snapshot[0], chart_account_code),
            ).fetchone()
            if row is None:
                return Decimal("0"), Decimal("0")
            return Decimal(row[0]), Decimal(row[1])
        row = connection.execute(
            """
            SELECT COALESCE(SUM(journal_entry_line.debit_amount), 0),
                   COALESCE(SUM(journal_entry_line.credit_amount), 0)
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND chart_account.chart_account_code = %s
              AND general_journal.accounting_date <= %s
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                chart_account_code,
                scope_start - timedelta(days=1),
            ),
        ).fetchone()
        return Decimal(row[0]), Decimal(row[1])

    def _period_account_sides(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        chart_account_code: str,
        period_ids: list[UUID],
    ) -> tuple[Decimal, Decimal]:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(journal_entry_line.debit_amount), 0),
                   COALESCE(SUM(journal_entry_line.credit_amount), 0)
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND chart_account.chart_account_code = %s
              AND general_journal.fiscal_period_id = ANY(%s)
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                chart_account_code,
                period_ids,
            ),
        ).fetchone()
        return Decimal(row[0]), Decimal(row[1])

    def load_account_balances(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        chart_account_code: str = "",
        *,
        page_limit: int = 50,
        cursor: str = "",
    ) -> dict[str, object]:
        """Return as-of chart-account balances from the close snapshot or live journals."""
        trial_balance = self.load_period_trial_balance(
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
        )
        account_classes = self._load_chart_account_classes(
            legal_entity_reference, accounting_book_reference
        )
        requested_code = chart_account_code.strip()
        if requested_code and requested_code not in account_classes:
            raise AccountingValidationError(
                f"Chart account {requested_code} is not recorded for this book. "
                "Create the chart_account row, then retry the account-balance read."
            )
        source_lines = [
            {
                "chart_account_code": str(raw_line["chart_account_code"]),
                "debit_amount": str(raw_line["debit_amount"]),
                "credit_amount": str(raw_line["credit_amount"]),
            }
            for raw_line in trial_balance["lines"]
        ]
        if requested_code:
            source_lines = [
                raw_line
                for raw_line in source_lines
                if raw_line["chart_account_code"] == requested_code
            ]
            if not source_lines:
                source_lines = [
                    {
                        "chart_account_code": requested_code,
                        "debit_amount": "0",
                        "credit_amount": "0",
                    }
                ]
        if cursor:
            source_lines = [
                raw_line
                for raw_line in source_lines
                if raw_line["chart_account_code"] > cursor
            ]
        has_more = len(source_lines) > page_limit
        page_lines = source_lines[:page_limit]
        account_balances = [
            {
                "chart_account_code": raw_line["chart_account_code"],
                "account_class_code": account_classes[str(raw_line["chart_account_code"])],
                "debit_amount": _exact_amount_text(Decimal(str(raw_line["debit_amount"]))),
                "credit_amount": _exact_amount_text(Decimal(str(raw_line["credit_amount"]))),
            }
            for raw_line in page_lines
        ]
        next_cursor = None
        if has_more:
            next_cursor = str(page_lines[-1]["chart_account_code"])
        return {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "accounting_book_reference": accounting_book_reference,
            "book_reference": accounting_book_reference,
            "fiscal_period_reference": str(trial_balance["fiscal_period_reference"]),
            "account_balances": account_balances,
            "next_cursor": next_cursor,
        }

    def load_receivable_aging(
        self,
        legal_entity_reference: str,
        book_reference: str,
        period_code: str,
        chart_account_code: str = "",
    ) -> dict[str, object]:
        """Return entity-level FIFO receivable aging as of the fiscal period end date."""
        return self._load_account_aging(
            legal_entity_reference,
            book_reference,
            period_code,
            chart_account_code,
            catalog_role_code="accounts_receivable",
            increase_is_debit=True,
            read_name="receivable-aging",
        )

    def load_payable_aging(
        self,
        legal_entity_reference: str,
        book_reference: str,
        period_code: str,
        chart_account_code: str = "",
    ) -> dict[str, object]:
        """Return entity-level FIFO payable aging as of the fiscal period end date."""
        return self._load_account_aging(
            legal_entity_reference,
            book_reference,
            period_code,
            chart_account_code,
            catalog_role_code="tax_payable",
            increase_is_debit=False,
            read_name="payable-aging",
        )

    def _load_account_aging(
        self,
        legal_entity_reference: str,
        book_reference: str,
        period_code: str,
        chart_account_code: str,
        *,
        catalog_role_code: str,
        increase_is_debit: bool,
        read_name: str,
    ) -> dict[str, object]:
        if not legal_entity_reference or not book_reference or not period_code:
            raise AccountingValidationError(
                "legal_entity_reference, book_reference, and fiscal_period_reference are required. "
                f"Supply those {read_name} fields, then retry the {read_name} read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action=f"the {read_name} read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                book_reference,
                next_action=f"the {read_name} read",
            )[0]
            _period_id, _status, period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action=f"the {read_name} read",
            )
            account_rows = connection.execute(
                """
                SELECT chart_account.chart_account_code,
                       account_role_mapping.account_role_code,
                       chart_account.account_class_code
                FROM accounting_core.chart_account
                LEFT JOIN accounting_core.account_role_mapping
                  ON account_role_mapping.tenant_account_id = chart_account.tenant_account_id
                 AND account_role_mapping.chart_account_id = chart_account.chart_account_id
                 AND account_role_mapping.valid_to IS NULL
                WHERE chart_account.tenant_account_id = %s
                  AND chart_account.accounting_book_id = %s
                  AND chart_account.valid_to IS NULL
                """,
                (tenant_id, book_id),
            ).fetchall()
            account_classes = {
                str(account_code): str(account_class_code)
                for account_code, _role_code, account_class_code in account_rows
            }
            catalog_account_code = next(
                (
                    str(account_code)
                    for account_code, role_code, _class in account_rows
                    if role_code == catalog_role_code
                ),
                "",
            )
            resolved_account_code = chart_account_code.strip() or catalog_account_code
            if resolved_account_code not in account_classes:
                raise AccountingValidationError(
                    f"Chart account {resolved_account_code} is not recorded for this book. "
                    f"Create the chart_account row, then retry the {read_name} read."
                )
            if resolved_account_code != catalog_account_code:
                raise AccountingValidationError(
                    f"chart_account_code must be the catalog {catalog_role_code} account. "
                    f"Supply that {read_name} account, then retry the {read_name} read."
                )
            line_rows = connection.execute(
                """
                SELECT general_journal.accounting_date,
                       general_journal.journal_reference,
                       journal_entry_line.line_number,
                       journal_entry_line.debit_amount,
                       journal_entry_line.credit_amount
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.legal_entity_id = %s
                  AND general_journal.accounting_book_id = %s
                  AND chart_account.chart_account_code = %s
                  AND general_journal.accounting_date <= %s
                  AND general_journal.journal_reference NOT LIKE %s
                ORDER BY general_journal.accounting_date,
                         CASE
                           WHEN %s AND journal_entry_line.debit_amount > 0 THEN 0
                           WHEN NOT %s AND journal_entry_line.credit_amount > 0 THEN 0
                           ELSE 1
                         END,
                         general_journal.journal_reference,
                         journal_entry_line.line_number
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    resolved_account_code,
                    period_end_date,
                    _CLOSING_JOURNAL_PATTERN,
                    increase_is_debit,
                    increase_is_debit,
                ),
            ).fetchall()
        open_items = _fifo_aging_open_items(line_rows, increase_is_debit=increase_is_debit)
        bucket_amounts = {
            "current": Decimal("0"),
            "days_31_60": Decimal("0"),
            "days_61_90": Decimal("0"),
            "days_over_90": Decimal("0"),
        }
        for open_item in open_items:
            outstanding_days = (period_end_date - open_item[0]).days
            bucket_amounts[_receivable_aging_bucket(outstanding_days)] += open_item[1]
        total_outstanding_amount = (
            bucket_amounts["current"]
            + bucket_amounts["days_31_60"]
            + bucket_amounts["days_61_90"]
            + bucket_amounts["days_over_90"]
        )
        document: dict[str, object] = {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "accounting_book_reference": book_reference,
            "book_reference": book_reference,
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
            "chart_account_code": resolved_account_code,
            "account_class_code": account_classes[resolved_account_code],
            "as_of_date": period_end_date.isoformat(),
            "current_amount": _unsigned_aging_amount_text(bucket_amounts["current"]),
            "days_31_60_amount": _unsigned_aging_amount_text(bucket_amounts["days_31_60"]),
            "days_61_90_amount": _unsigned_aging_amount_text(bucket_amounts["days_61_90"]),
            "days_over_90_amount": _unsigned_aging_amount_text(bucket_amounts["days_over_90"]),
            "total_outstanding_amount": _unsigned_aging_amount_text(total_outstanding_amount),
        }
        if increase_is_debit:
            unapplied_credit_amount = Decimal("0")
            for _date, _reference, _line_number, debit_amount, credit_amount in line_rows:
                unapplied_credit_amount += Decimal(str(credit_amount)) - Decimal(
                    str(debit_amount)
                )
            if unapplied_credit_amount > 0:
                document["unapplied_credit_amount"] = _unsigned_aging_amount_text(
                    unapplied_credit_amount
                )
        return document

    def _load_chart_account_classes(
        self, legal_entity_reference: str, accounting_book_reference: str
    ) -> dict[str, str]:
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the account-balance read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the account-balance read",
            )[0]
            rows = connection.execute(
                """
                SELECT chart_account.chart_account_code,
                       chart_account.account_class_code
                FROM accounting_core.chart_account
                WHERE chart_account.tenant_account_id = %s
                  AND chart_account.accounting_book_id = %s
                  AND chart_account.valid_to IS NULL
                """,
                (tenant_id, book_id),
            ).fetchall()
        return {
            str(account_code): str(account_class_code)
            for account_code, account_class_code in rows
        }

    def load_account_ledger(
        self,
        legal_entity_reference: str,
        chart_account_code: str,
        fiscal_period_reference: str = "",
        *,
        page_limit: int = 50,
        cursor_after: tuple[datetime, str, int] | None = None,
    ) -> dict[str, object]:
        """Return posted journal lines for one tenant entity and chart account."""
        if not legal_entity_reference:
            raise AccountingValidationError(
                "legal_entity_reference is required. "
                "Supply that ledger field, then retry the account-ledger read."
            )
        if not chart_account_code:
            raise AccountingValidationError(
                "chart_account_code is required. "
                "Supply that ledger field, then retry the account-ledger read."
            )
        period_code = ""
        if fiscal_period_reference:
            period_code = fiscal_period_reference
            if period_code.startswith("urn:cwl:accounting:fiscal_period:"):
                period_code = period_code[len("urn:cwl:accounting:fiscal_period:") :]
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._require_legal_entity(
                connection, tenant_id, legal_entity_reference, "the account-ledger read"
            )
            chart_row = connection.execute(
                """
                SELECT chart_account_id
                FROM accounting_core.chart_account
                WHERE tenant_account_id = %s
                  AND chart_account_code = %s
                  AND valid_to IS NULL
                LIMIT 1
                """,
                (tenant_id, chart_account_code),
            ).fetchone()
            if chart_row is None:
                raise AccountingValidationError(
                    f"Chart account {chart_account_code} is not recorded for this tenant. "
                    "Create the chart_account row, then retry the account-ledger read."
                )
            period_id = None
            period_reference: str | None = None
            if period_code:
                period_id, _status, _end = self._require_fiscal_period(
                    connection, tenant_id, period_code, "the account-ledger read"
                )
                period_reference = f"urn:cwl:accounting:fiscal_period:{period_code}"
            cursor_posted_at = None
            cursor_journal_reference = None
            cursor_line_number = None
            if cursor_after is not None:
                cursor_posted_at, cursor_journal_reference, cursor_line_number = cursor_after
            totals = connection.execute(
                """
                SELECT COALESCE(SUM(journal_entry_line.debit_amount), 0),
                       COALESCE(SUM(journal_entry_line.credit_amount), 0)
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                JOIN accounting_core.legal_entity_record
                  ON legal_entity_record.tenant_account_id = general_journal.tenant_account_id
                 AND legal_entity_record.legal_entity_id = general_journal.legal_entity_id
                WHERE journal_entry_line.tenant_account_id = %s
                  AND legal_entity_record.legal_entity_code = %s
                  AND chart_account.chart_account_code = %s
                  AND (%s::uuid IS NULL OR general_journal.fiscal_period_id = %s)
                """,
                (
                    tenant_id,
                    legal_entity_reference,
                    chart_account_code,
                    period_id,
                    period_id,
                ),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT general_journal.journal_reference,
                       general_journal.posted_at,
                       journal_entry_line.line_number,
                       chart_account.chart_account_code,
                       journal_entry_line.account_role_code,
                       journal_entry_line.debit_amount,
                       journal_entry_line.credit_amount
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                JOIN accounting_core.legal_entity_record
                  ON legal_entity_record.tenant_account_id = general_journal.tenant_account_id
                 AND legal_entity_record.legal_entity_id = general_journal.legal_entity_id
                WHERE journal_entry_line.tenant_account_id = %s
                  AND legal_entity_record.legal_entity_code = %s
                  AND chart_account.chart_account_code = %s
                  AND (%s::uuid IS NULL OR general_journal.fiscal_period_id = %s)
                  AND (
                        %s::timestamptz IS NULL
                        OR (
                            general_journal.posted_at,
                            general_journal.journal_reference,
                            journal_entry_line.line_number
                        ) > (%s, %s, %s)
                      )
                ORDER BY general_journal.posted_at,
                         general_journal.journal_reference,
                         journal_entry_line.line_number
                LIMIT %s
                """,
                (
                    tenant_id,
                    legal_entity_reference,
                    chart_account_code,
                    period_id,
                    period_id,
                    cursor_posted_at,
                    cursor_posted_at,
                    cursor_journal_reference,
                    cursor_line_number,
                    page_limit + 1,
                ),
            ).fetchall()
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            ledger_lines = [
                {
                    "line_number": row[2],
                    "chart_account_code": row[3],
                    "account_role_code": row[4],
                    "debit_amount": _exact_amount_text(Decimal(row[5])),
                    "credit_amount": _exact_amount_text(Decimal(row[6])),
                    "journal_reference": row[0],
                    "posted_at": _format_timestamp(row[1]),
                }
                for row in page_rows
            ]
            next_cursor = None
            if has_more:
                last = page_rows[-1]
                next_cursor = f"{_format_timestamp(last[1])}|{last[0]}|{last[2]}"
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "chart_account_code": chart_account_code,
                "fiscal_period_reference": period_reference,
                "ledger_lines": ledger_lines,
                "period_debit_total": _exact_amount_text(Decimal(totals[0])),
                "period_credit_total": _exact_amount_text(Decimal(totals[1])),
                "next_cursor": next_cursor,
            }

    def reverse(
        self,
        journal_reference: str,
        reversal_date: date,
        reversal_reason_code: str,
        policy: AccountingPolicy,
        *,
        reversal_idempotency_key: str | None = None,
    ) -> PostingReceipt:
        """Append the exact opposite of one original journal and preserve lineage."""
        _require_code(reversal_reason_code, "reversal reason code")
        command_key = (
            f"reversal:{journal_reference}"
            if reversal_idempotency_key is None
            else reversal_idempotency_key.strip()
        )
        if not command_key:
            raise AccountingValidationError(
                "reversal idempotency key must not be empty. "
                "Supply the reversal command identity, then retry reversal."
            )
        command_hash = _reversal_command_hash(
            tenant_reference=self._tenant_reference,
            reversal_idempotency_key=command_key,
            original_journal_reference=journal_reference,
            reversal_date=reversal_date,
            reversal_reason_code=reversal_reason_code,
        )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            self._acquire_command_lock(
                connection, f"reversal:{journal_reference}:{command_key}"
            )
            existing = connection.execute(
                """
                SELECT reversal_journal.journal_reference,
                       reversal_record.idempotency_key,
                       reversal_record.source_payload_hash,
                       original_journal.journal_reference,
                       journal_reversal.reversal_reason_code,
                       reversal_journal.accounting_date
                FROM accounting_core.journal_reversal
                JOIN accounting_core.general_journal AS original_journal
                  ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND original_journal.general_journal_id = journal_reversal.original_journal_id
                JOIN accounting_core.general_journal AS reversal_journal
                  ON reversal_journal.tenant_account_id = journal_reversal.tenant_account_id
                 AND reversal_journal.general_journal_id = journal_reversal.reversal_journal_id
                JOIN accounting_integration.journal_proposal_record AS reversal_record
                  ON reversal_record.tenant_account_id = reversal_journal.tenant_account_id
                 AND reversal_record.proposal_record_id = reversal_journal.source_proposal_record_id
                WHERE journal_reversal.tenant_account_id = %s
                  AND original_journal.journal_reference = %s
                """,
                (tenant_id, journal_reference),
            ).fetchone()
            if existing is not None:
                if str(existing[1]) != command_key:
                    raise AccountingValidationError(
                        "journal is already reversed. Use the existing reversal receipt, then retry."
                    )
                if (
                    str(existing[2]) != command_hash
                    or str(existing[3]) != journal_reference
                    or str(existing[4]) != reversal_reason_code
                    or existing[5] != reversal_date
                ):
                    raise IdempotencyConflictError(
                        "reversal idempotency key was already used with different command evidence. "
                        "Use a new reversal command identity, then retry."
                    )
                return self._receipt_for_journal(connection, tenant_id, existing[0])
            prior_command = connection.execute(
                """
                SELECT source_payload_hash
                FROM accounting_integration.journal_proposal_record
                WHERE tenant_account_id = %s AND idempotency_key = %s
                """,
                (tenant_id, command_key),
            ).fetchone()
            if prior_command is not None:
                raise IdempotencyConflictError(
                    "reversal idempotency key was already used by another accounting command. Supply a new reversal command identity, then retry."
                )
            original = connection.execute(
                """
                SELECT general_journal_id, legal_entity_id, accounting_book_id,
                       transaction_currency_code, functional_currency_code,
                       source_proposal_record_id, transaction_date, accounting_date
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (tenant_id, journal_reference),
            ).fetchone()
            if original is None:
                raise AccountingValidationError(
                    "journal does not exist. Supply a posted journal reference, then retry reversal."
                )
            already_reversal = connection.execute(
                """
                SELECT 1
                FROM accounting_core.journal_reversal
                WHERE tenant_account_id = %s AND reversal_journal_id = %s
                """,
                (tenant_id, original[0]),
            ).fetchone()
            if already_reversal is not None:
                raise AccountingValidationError(
                    "a reversal journal cannot itself be reversed. Reverse the original journal, or post a replacement."
                )
            if reversal_date < original[7]:
                raise AccountingValidationError(
                    "reversal date cannot precede original journal accounting date. Supply a reversal_date on or after the original accounting date, then retry reversal."
                )
            if not policy.permits(reversal_date):
                raise AccountingValidationError("reversal date belongs to a closed fiscal period. Reverse into an open or soft-closed period, then retry reversal.")
            if (
                self._tenant_reference != policy.tenant_reference
                or self._legal_entity_code(connection, tenant_id, original[1])
                != policy.legal_entity_reference
                or self._book_name(connection, tenant_id, original[2])
                != policy.accounting_book_reference
            ):
                raise AccountingValidationError(
                    "reversal policy scope does not match original journal. Supply the reversal policy for the original journal's legal entity and book, then retry reversal."
                )
            period_id = self._require_adjusting_period(connection, tenant_id, reversal_date)
            original_lines = self._load_lines(connection, tenant_id, original[0])
            reversal_lines = tuple(
                PostedJournalLine(
                    line_number=line.line_number,
                    chart_account_code=line.chart_account_code,
                    account_role_code=line.account_role_code,
                    debit_amount=line.credit_amount,
                    credit_amount=line.debit_amount,
                )
                for line in original_lines
            )
            reversal_reference = f"{journal_reference}:reversal"
            occupant = connection.execute(
                """
                SELECT 1
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (tenant_id, reversal_reference),
            ).fetchone()
            if occupant is not None:
                raise AccountingValidationError(
                    "posted journal is immutable. Reverse the existing journal, "
                    "then post a replacement."
                )
            _original_source_hash, source_proposal_id = self._proposal_identity(
                connection, tenant_id, original[5]
            )
            receipt = PostingReceipt(
                receipt_reference=f"{reversal_reference}:receipt",
                journal_reference=reversal_reference,
                posting_status_code="posted",
                source_proposal_id=source_proposal_id,
                source_payload_hash=command_hash,
                tenant_reference=policy.tenant_reference,
                legal_entity_reference=policy.legal_entity_reference,
                accounting_book_reference=policy.accounting_book_reference,
                accounting_policy_version=policy.accounting_policy_version,
                posting_rule_version=policy.posting_rule_version,
                line_count=len(reversal_lines),
                reversal_of_journal_reference=journal_reference,
            )
            reversal_proposal_id = connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    tenant_account_id, external_proposal_id, proposal_contract_version,
                    idempotency_key, source_payload_hash, proposal_status_code, processed_at
                )
                VALUES (%s, uuidv7(), 1, %s, %s, 'posted', clock_timestamp())
                RETURNING proposal_record_id
                """,
                (tenant_id, command_key, command_hash),
            ).fetchone()[0]
            reversal_journal_id = self._insert_journal(
                connection,
                tenant_id=tenant_id,
                legal_entity_id=original[1],
                book_id=original[2],
                period_id=period_id,
                journal_reference=reversal_reference,
                proposal=_ReversalProposal(
                    source_payload_hash=command_hash,
                    transaction_currency=original[3],
                    transaction_date=original[6],
                    accounting_date=reversal_date,
                    source_event_references=(),
                ),
                policy=policy,
                proposal_record_id=reversal_proposal_id,
                lines=reversal_lines,
            )
            connection.execute(
                """
                INSERT INTO accounting_core.journal_reversal (
                    tenant_account_id, original_journal_id, reversal_journal_id,
                    reversal_reason_code
                )
                VALUES (%s, %s, %s, %s)
                """,
                (tenant_id, original[0], reversal_journal_id, reversal_reason_code),
            )
            self._insert_receipt(
                connection, tenant_id, reversal_proposal_id, reversal_journal_id, receipt
            )
            self._insert_outbox(
                connection,
                tenant_id,
                "journal_reversal",
                reversal_reference,
                receipt.receipt_reference,
                receipt,
            )
            return receipt

    def load_reversal_policy(
        self, journal_reference: str, reversal_date: date
    ) -> AccountingPolicy:
        """Build catalog policy for reversing *journal_reference* on *reversal_date*."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            row = connection.execute(
                """
                SELECT legal_entity_record.legal_entity_code,
                       accounting_book.book_name,
                       accounting_book.book_role_code,
                       general_journal.transaction_currency_code,
                       general_journal.functional_currency_code,
                       general_journal.accounting_policy_version,
                       general_journal.posting_rule_version,
                       general_journal.general_journal_id
                FROM accounting_core.general_journal
                JOIN accounting_core.legal_entity_record
                  ON legal_entity_record.tenant_account_id = general_journal.tenant_account_id
                 AND legal_entity_record.legal_entity_id = general_journal.legal_entity_id
                JOIN accounting_core.accounting_book
                  ON accounting_book.tenant_account_id = general_journal.tenant_account_id
                 AND accounting_book.accounting_book_id = general_journal.accounting_book_id
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.journal_reference = %s
                """,
                (tenant_id, journal_reference),
            ).fetchone()
            if row is None:
                raise AccountingValidationError(
                    "journal does not exist. Supply a posted journal reference, then retry reversal."
                )
            _period_id, period_start, period_end = self._require_adjusting_period_bounds(
                connection, tenant_id, reversal_date
            )
            lines = self._load_lines(connection, tenant_id, row[7])
            return AccountingPolicy(
                tenant_reference=self._tenant_reference,
                legal_entity_reference=row[0],
                accounting_book_reference=row[1],
                intended_book_role_code=row[2],
                transaction_currency=row[3],
                functional_currency=row[4],
                open_period_start=period_start,
                open_period_end=period_end,
                chart_account_mapping={
                    line.account_role_code: line.chart_account_code for line in lines
                },
                accounting_policy_version=row[5],
                posting_rule_version=row[6],
            )

    def load_account_role_mappings(
        self, legal_entity_reference: str, accounting_book_reference: str
    ) -> dict[str, object]:
        """Return effective account-role mappings for one legal entity and book."""
        if not legal_entity_reference or not accounting_book_reference:
            raise AccountingValidationError(
                "legal_entity_reference and book_reference are required. "
                "Supply those catalog fields, then retry the mapping read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._load_legal_entity(
                connection, tenant_id, legal_entity_reference, "the mapping read"
            )[0]
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                "the mapping read",
            )[0]
            rows = connection.execute(
                """
                SELECT account_role_mapping.account_role_code,
                       chart_account.chart_account_code,
                       account_role_mapping.accounting_policy_version,
                       account_role_mapping.posting_rule_version
                FROM accounting_core.account_role_mapping
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = account_role_mapping.tenant_account_id
                 AND chart_account.chart_account_id = account_role_mapping.chart_account_id
                WHERE account_role_mapping.tenant_account_id = %s
                  AND account_role_mapping.accounting_book_id = %s
                  AND account_role_mapping.valid_to IS NULL
                ORDER BY account_role_mapping.account_role_code
                """,
                (tenant_id, book_id),
            ).fetchall()
            if not rows:
                raise AccountingValidationError(
                    "No account_role_mapping is recorded for this book. "
                    "Create the account_role_mapping rows, then retry the mapping read."
                )
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "accounting_book_reference": accounting_book_reference,
                "book_reference": accounting_book_reference,
                "mappings": [
                    {
                        "account_role_code": role_code,
                        "chart_account_code": account_code,
                        "accounting_policy_version": policy_version,
                        "posting_rule_version": rule_version,
                    }
                    for role_code, account_code, policy_version, rule_version in rows
                ],
            }

    def load_legal_entities(self) -> dict[str, object]:
        """Return existing legal_entity_record rows for the bound tenant."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            rows = connection.execute(
                """
                SELECT legal_entity_record.legal_entity_code,
                       legal_entity_record.entity_name
                FROM accounting_core.legal_entity_record
                WHERE legal_entity_record.tenant_account_id = %s
                  AND legal_entity_record.valid_to IS NULL
                ORDER BY legal_entity_record.legal_entity_code
                """,
                (tenant_id,),
            ).fetchall()
        return {
            "tenant_reference": self._tenant_reference,
            "legal_entities": [
                {
                    "legal_entity_reference": legal_entity_code,
                    "entity_name": entity_name,
                }
                for legal_entity_code, entity_name in rows
            ],
        }

    def load_accounting_books(self, legal_entity_reference: str) -> dict[str, object]:
        """Return existing accounting_book rows for one legal entity."""
        if not legal_entity_reference:
            raise AccountingValidationError(
                "legal_entity_reference is required. "
                "Supply that catalog field, then retry the accounting-book list."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._load_legal_entity(
                connection, tenant_id, legal_entity_reference, "the accounting-book list"
            )[0]
            rows = connection.execute(
                """
                SELECT accounting_book.book_name,
                       accounting_book.book_role_code
                FROM accounting_core.accounting_book
                WHERE accounting_book.tenant_account_id = %s
                  AND accounting_book.legal_entity_id = %s
                  AND accounting_book.valid_to IS NULL
                ORDER BY accounting_book.book_name
                """,
                (tenant_id, legal_entity_id),
            ).fetchall()
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "accounting_books": [
                    {
                        "accounting_book_reference": book_name,
                        "book_reference": book_name,
                        "intended_book_role_code": book_role_code,
                        "book_name": book_name,
                    }
                    for book_name, book_role_code in rows
                ],
            }

    def load_chart_accounts(
        self, legal_entity_reference: str, accounting_book_reference: str
    ) -> dict[str, object]:
        """Return existing chart_account rows for one legal entity and book."""
        if not legal_entity_reference or not accounting_book_reference:
            raise AccountingValidationError(
                "legal_entity_reference and book_reference are required. "
                "Supply those catalog fields, then retry the chart-account read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._load_legal_entity(
                connection, tenant_id, legal_entity_reference, "the chart-account read"
            )[0]
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                "the chart-account read",
            )[0]
            rows = connection.execute(
                """
                SELECT chart_account.chart_account_code,
                       chart_account.account_name,
                       chart_account.normal_balance_code,
                       chart_account.account_class_code
                FROM accounting_core.chart_account
                WHERE chart_account.tenant_account_id = %s
                  AND chart_account.accounting_book_id = %s
                  AND chart_account.valid_to IS NULL
                ORDER BY chart_account.chart_account_code
                """,
                (tenant_id, book_id),
            ).fetchall()
            return {
                "tenant_reference": self._tenant_reference,
                "legal_entity_reference": legal_entity_reference,
                "accounting_book_reference": accounting_book_reference,
                "book_reference": accounting_book_reference,
                "chart_accounts": [
                    {
                        "chart_account_code": account_code,
                        "account_name": account_name,
                        "normal_balance_code": normal_balance_code,
                        "account_class_code": account_class_code,
                    }
                    for (
                        account_code,
                        account_name,
                        normal_balance_code,
                        account_class_code,
                    ) in rows
                ],
            }

    def trial_balance(
        self,
        tenant_reference: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
        through_date: date,
    ) -> dict[str, AccountBalance]:
        """Aggregate posted lines in one tenant/entity/book scope through a date."""
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            if tenant_reference != self._tenant_reference:
                return {}
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s AND legal_entity_code = %s
                """,
                (tenant_id, legal_entity_reference),
            ).fetchone()
            book_id = connection.execute(
                """
                SELECT accounting_book_id
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s AND book_name = %s
                """,
                (tenant_id, accounting_book_reference),
            ).fetchone()
            if legal_entity_id is None or book_id is None:
                return {}
            rows = self._aggregate_trial_balance(
                connection, tenant_id, legal_entity_id[0], book_id[0], through_date
            )
        return {
            account_code: AccountBalance(account_code, debit_total, credit_total)
            for _account_id, account_code, debit_total, credit_total in rows
        }

    def load_period_trial_balance(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        balance_basis_code: str = "",
    ) -> dict[str, object]:
        """Return snapshot or live trial-balance totals, optionally on an unadjusted, adjusted, or post-close basis."""
        _require_reference(legal_entity_reference, "legal entity reference")
        _require_reference(accounting_book_reference, "accounting book reference")
        if not period_code.strip():
            raise AccountingValidationError(
                "period_code is required. Supply the fiscal period code, then retry the trial-balance read."
            )
        if balance_basis_code and balance_basis_code not in {
            "unadjusted",
            "adjusted",
            "post_close",
        }:
            raise AccountingValidationError(
                "balance_basis_code must be unadjusted, adjusted, or post_close. "
                "Supply a known trial-balance basis, then retry the trial-balance read."
            )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the trial-balance read",
            )
            book_id, _reporting_currency = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the trial-balance read",
            )
            period_id, period_status_code, period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the trial-balance read",
            )
            snapshot_record_id = None
            if balance_basis_code == "post_close":
                snapshot = self._latest_close_snapshot(
                    connection, tenant_id, legal_entity_id, book_id, period_id
                )
                if snapshot is None:
                    raise AccountingValidationError(
                        "balance_basis_code=post_close requires a stored trial_balance_snapshot. "
                        "Hard-close the period, then retry the trial-balance read."
                    )
                snapshot_record_id = str(snapshot[0])
                line_rows = self._load_snapshot_balance_lines(
                    connection, tenant_id, snapshot[0]
                )
                balance_source_code = "snapshot"
            elif balance_basis_code == "unadjusted":
                line_rows = tuple(
                    (account_code, debit_total, credit_total)
                    for _account_id, account_code, debit_total, credit_total in self._aggregate_worksheet_trial_balance(
                        connection,
                        tenant_id,
                        legal_entity_id,
                        book_id,
                        period_end_date,
                        exclude_adjusting=True,
                    )
                )
                balance_source_code = "live"
            elif balance_basis_code == "adjusted":
                line_rows = tuple(
                    (account_code, debit_total, credit_total)
                    for _account_id, account_code, debit_total, credit_total in self._aggregate_worksheet_trial_balance(
                        connection,
                        tenant_id,
                        legal_entity_id,
                        book_id,
                        period_end_date,
                        exclude_adjusting=False,
                    )
                )
                balance_source_code = "live"
            elif period_status_code == "hard_closed":
                snapshot = self._latest_close_snapshot(
                    connection, tenant_id, legal_entity_id, book_id, period_id
                )
                if snapshot is None:
                    raise AccountingValidationError(
                        f"Fiscal period {period_code} is {period_status_code} without a "
                        "trial-balance snapshot. Restore the trial_balance_snapshot for this "
                        "book from the journal population, then retry the trial-balance read."
                    )
                snapshot_record_id = str(snapshot[0])
                line_rows = self._load_snapshot_balance_lines(
                    connection, tenant_id, snapshot[0]
                )
                balance_source_code = "snapshot"
            else:
                line_rows = tuple(
                    (account_code, debit_total, credit_total)
                    for _account_id, account_code, debit_total, credit_total in self._aggregate_trial_balance(
                        connection, tenant_id, legal_entity_id, book_id, period_end_date
                    )
                )
                balance_source_code = "live"
        document: dict[str, object] = {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "accounting_book_reference": accounting_book_reference,
            "book_reference": accounting_book_reference,
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
            "period_code": period_code,
            "period_status_code": period_status_code,
            "balance_source_code": balance_source_code,
            "lines": [
                {
                    "chart_account_code": account_code,
                    "debit_amount": _exact_amount_text(debit_total),
                    "credit_amount": _exact_amount_text(credit_total),
                    "net_balance_amount": _exact_amount_text(debit_total - credit_total),
                }
                for account_code, debit_total, credit_total in line_rows
            ],
        }
        if snapshot_record_id is not None:
            document["snapshot_record_id"] = snapshot_record_id
        if balance_basis_code:
            document["balance_basis_code"] = balance_basis_code
        return document

    def load_financial_statement(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        statement_type_code: str,
        comparison_period_code: str = "",
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        """Project income-statement, balance-sheet, changes-in-equity, or cash-flow lines from posted books."""
        if statement_scope_code not in {"", "period", "year_to_date"}:
            raise AccountingValidationError(
                "statement_scope_code must be period or year_to_date. "
                "Supply a known statement scope, then retry the financial-statement read."
            )
        if statement_type_code == "income_statement":
            allowed_classes = frozenset({"revenue", "expense"})
        elif statement_type_code == "balance_sheet":
            allowed_classes = frozenset({"asset", "liability", "equity"})
        elif statement_type_code == "changes_in_equity":
            allowed_classes = frozenset({"equity"})
        elif statement_type_code == "cash_flow":
            allowed_classes = frozenset()
        else:
            raise AccountingValidationError(
                "statement_type_code must be income_statement, balance_sheet, changes_in_equity, or cash_flow. "
                "Supply a known statement type, then retry the financial-statement read."
            )
        trial_balance = self.load_period_trial_balance(
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
        )
        account_facts = self._load_statement_account_facts(
            legal_entity_reference, accounting_book_reference
        )
        income_scope_code = (
            "period" if statement_type_code == "balance_sheet" else statement_scope_code
        )
        if statement_type_code == "changes_in_equity":
            source_lines = self._load_changes_in_equity_lines(
                legal_entity_reference=legal_entity_reference,
                accounting_book_reference=accounting_book_reference,
                period_code=period_code,
                statement_scope_code=statement_scope_code,
            )
        elif statement_type_code == "cash_flow":
            source_lines = self._load_cash_flow_lines(
                legal_entity_reference=legal_entity_reference,
                accounting_book_reference=accounting_book_reference,
                period_code=period_code,
                statement_scope_code=statement_scope_code,
            )
        elif statement_type_code == "income_statement":
            source_lines = self._load_operational_income_lines(
                legal_entity_reference=legal_entity_reference,
                accounting_book_reference=accounting_book_reference,
                period_code=period_code,
                statement_scope_code=income_scope_code,
            )
        else:
            source_lines = []
            for raw_line in trial_balance["lines"]:
                account_code = str(raw_line["chart_account_code"])
                account_fact = account_facts.get(account_code)
                if account_fact is None:
                    raise AccountingValidationError(
                        f"account_role_mapping is missing for chart account {account_code}. "
                        "Create the account_role_mapping row, then retry the financial-statement read."
                    )
                account_role_code, account_class_code = account_fact
                if account_class_code not in allowed_classes:
                    continue
                source_lines.append(
                    {
                        "chart_account_code": account_code,
                        "account_role_code": account_role_code,
                        "account_class_code": account_class_code,
                        "debit_amount": Decimal(str(raw_line["debit_amount"])),
                        "credit_amount": Decimal(str(raw_line["credit_amount"])),
                    }
                )
        statement_lines: list[dict[str, str]] = []
        total_debit_amount = Decimal("0")
        total_credit_amount = Decimal("0")
        for raw_line in source_lines:
            debit_amount = Decimal(str(raw_line["debit_amount"]))
            credit_amount = Decimal(str(raw_line["credit_amount"]))
            statement_lines.append(
                {
                    "chart_account_code": str(raw_line["chart_account_code"]),
                    "account_role_code": str(raw_line["account_role_code"]),
                    "account_class_code": str(raw_line["account_class_code"]),
                    "debit_amount": _exact_amount_text(debit_amount),
                    "credit_amount": _exact_amount_text(credit_amount),
                }
            )
            total_debit_amount += debit_amount
            total_credit_amount += credit_amount
        if statement_type_code == "income_statement":
            net_income_amount = sum(
                (
                    Decimal(str(raw_line["credit_amount"]))
                    - Decimal(str(raw_line["debit_amount"]))
                    for raw_line in source_lines
                ),
                Decimal("0"),
            )
        elif statement_type_code in {"changes_in_equity", "cash_flow"}:
            net_income_amount = next(
                Decimal(str(raw_line["credit_amount"]))
                - Decimal(str(raw_line["debit_amount"]))
                for raw_line in source_lines
                if raw_line["account_role_code"] == "period_net_income"
            )
        elif str(trial_balance["period_status_code"]) == "hard_closed":
            net_income_amount = Decimal("0")
        else:
            net_income_amount = sum(
                (
                    Decimal(str(raw_line["credit_amount"]))
                    - Decimal(str(raw_line["debit_amount"]))
                    for raw_line in self._load_operational_income_lines(
                        legal_entity_reference=legal_entity_reference,
                        accounting_book_reference=accounting_book_reference,
                        period_code=period_code,
                        statement_scope_code=income_scope_code,
                    )
                ),
                Decimal("0"),
            )
        document = {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "accounting_book_reference": accounting_book_reference,
            "book_reference": accounting_book_reference,
            "fiscal_period_reference": str(trial_balance["fiscal_period_reference"]),
            "statement_type_code": statement_type_code,
            "statement_lines": statement_lines,
            "total_debit_amount": _exact_amount_text(total_debit_amount),
            "total_credit_amount": _exact_amount_text(total_credit_amount),
            "net_income_amount": _exact_amount_text(net_income_amount),
        }
        if statement_scope_code == "year_to_date":
            document["statement_scope_code"] = "year_to_date"
        if comparison_period_code.strip():
            compared = self.load_financial_statement(
                legal_entity_reference,
                accounting_book_reference,
                comparison_period_code.strip(),
                statement_type_code,
                statement_scope_code=statement_scope_code,
            )
            document["comparison_fiscal_period_reference"] = compared[
                "fiscal_period_reference"
            ]
            document["comparison_statement_lines"] = compared["statement_lines"]
            document["comparison_total_debit_amount"] = compared["total_debit_amount"]
            document["comparison_total_credit_amount"] = compared["total_credit_amount"]
            document["comparison_net_income_amount"] = compared["net_income_amount"]
        return document

    def load_financial_statement_package(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        comparison_period_code: str = "",
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        """Return all four financial statements from one REPEATABLE READ snapshot."""
        with self._consistent_read_session():
            return self._assemble_financial_statement_package(
                legal_entity_reference,
                accounting_book_reference,
                period_code,
                comparison_period_code=comparison_period_code,
                statement_scope_code=statement_scope_code,
            )

    def _assemble_financial_statement_package(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        comparison_period_code: str = "",
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        income_statement = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "income_statement",
            comparison_period_code,
            statement_scope_code,
        )
        balance_sheet = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "balance_sheet",
            comparison_period_code,
            statement_scope_code,
        )
        changes_in_equity = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "changes_in_equity",
            comparison_period_code,
            statement_scope_code,
        )
        cash_flow = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "cash_flow",
            comparison_period_code,
            statement_scope_code,
        )
        document: dict[str, object] = {
            "tenant_reference": income_statement["tenant_reference"],
            "legal_entity_reference": income_statement["legal_entity_reference"],
            "accounting_book_reference": income_statement["accounting_book_reference"],
            "book_reference": income_statement["book_reference"],
            "fiscal_period_reference": income_statement["fiscal_period_reference"],
            "income_statement": income_statement,
            "balance_sheet": balance_sheet,
            "changes_in_equity": changes_in_equity,
            "cash_flow": cash_flow,
        }
        if statement_scope_code == "year_to_date":
            document["statement_scope_code"] = "year_to_date"
        return document

    def load_period_close_package(
        self,
        legal_entity_reference: str,
        book_reference: str,
        period_code: str,
        comparison_period_code: str = "",
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        """Return the close-binder worksheets from one REPEATABLE READ ledger snapshot."""
        with self._consistent_read_session():
            return self._assemble_period_close_package(
                legal_entity_reference,
                book_reference,
                period_code,
                comparison_period_code=comparison_period_code,
                statement_scope_code=statement_scope_code,
            )

    def _assemble_period_close_package(
        self,
        legal_entity_reference: str,
        book_reference: str,
        period_code: str,
        comparison_period_code: str = "",
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        fiscal_period = self.load_fiscal_period(legal_entity_reference, period_code)
        trial_balance = self.load_period_trial_balance(
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=book_reference,
            period_code=period_code,
        )
        income_statement = self.load_financial_statement(
            legal_entity_reference,
            book_reference,
            period_code,
            "income_statement",
            comparison_period_code,
            statement_scope_code,
        )
        balance_sheet = self.load_financial_statement(
            legal_entity_reference,
            book_reference,
            period_code,
            "balance_sheet",
            comparison_period_code,
            statement_scope_code,
        )
        changes_in_equity = self.load_financial_statement(
            legal_entity_reference,
            book_reference,
            period_code,
            "changes_in_equity",
            comparison_period_code,
            statement_scope_code,
        )
        cash_flow = self.load_financial_statement(
            legal_entity_reference,
            book_reference,
            period_code,
            "cash_flow",
            comparison_period_code,
            statement_scope_code,
        )
        financial_statement_package: dict[str, object] = {
            "tenant_reference": income_statement["tenant_reference"],
            "legal_entity_reference": income_statement["legal_entity_reference"],
            "accounting_book_reference": income_statement["accounting_book_reference"],
            "book_reference": income_statement["book_reference"],
            "fiscal_period_reference": income_statement["fiscal_period_reference"],
            "income_statement": income_statement,
            "balance_sheet": balance_sheet,
            "changes_in_equity": changes_in_equity,
            "cash_flow": cash_flow,
        }
        if statement_scope_code == "year_to_date":
            financial_statement_package["statement_scope_code"] = "year_to_date"
        receivable_aging = self.load_receivable_aging(
            legal_entity_reference,
            book_reference,
            period_code,
        )
        payable_aging = self.load_payable_aging(
            legal_entity_reference,
            book_reference,
            period_code,
        )
        unapplied_cash_rollforward = self.load_unapplied_cash_rollforward(
            legal_entity_reference,
            book_reference,
            period_code,
        )
        vat_period_register = self.load_vat_period_register(
            legal_entity_reference,
            book_reference,
            period_code,
        )
        close_page = self.load_period_closes(legal_entity_reference, period_code)
        stored_closes = close_page["period_closes"]
        period_close = stored_closes[-1] if stored_closes else None
        return {
            "tenant_reference": trial_balance["tenant_reference"],
            "legal_entity_reference": trial_balance["legal_entity_reference"],
            "accounting_book_reference": trial_balance["accounting_book_reference"],
            "book_reference": trial_balance["book_reference"],
            "fiscal_period_reference": trial_balance["fiscal_period_reference"],
            "fiscal_period": fiscal_period,
            "trial_balance": trial_balance,
            "financial_statement_package": financial_statement_package,
            "receivable_aging": receivable_aging,
            "payable_aging": payable_aging,
            "unapplied_cash_rollforward": unapplied_cash_rollforward,
            "vat_period_register": vat_period_register,
            "period_close": period_close,
        }

    def _require_closeable_package(self, package: Mapping[str, object]) -> None:
        trial_balance = package["trial_balance"]
        lines = trial_balance["lines"]
        debit_total = sum(
            (Decimal(str(line["debit_amount"])) for line in lines),
            Decimal("0"),
        )
        credit_total = sum(
            (Decimal(str(line["credit_amount"])) for line in lines),
            Decimal("0"),
        )
        if debit_total != credit_total:
            raise AccountingValidationError(
                "trial balance does not balance. "
                "Correct the posted journals so debit totals equal credit totals, "
                "then retry the close."
            )

    def _load_statement_account_facts(
        self, legal_entity_reference: str, accounting_book_reference: str
    ) -> dict[str, tuple[str, str]]:
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the financial-statement read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the financial-statement read",
            )[0]
            rows = connection.execute(
                """
                SELECT chart_account.chart_account_code,
                       account_role_mapping.account_role_code,
                       chart_account.account_class_code
                FROM accounting_core.chart_account
                JOIN accounting_core.account_role_mapping
                  ON account_role_mapping.tenant_account_id = chart_account.tenant_account_id
                 AND account_role_mapping.chart_account_id = chart_account.chart_account_id
                 AND account_role_mapping.valid_to IS NULL
                WHERE chart_account.tenant_account_id = %s
                  AND chart_account.accounting_book_id = %s
                  AND chart_account.valid_to IS NULL
                """,
                (tenant_id, book_id),
            ).fetchall()
        return {
            str(account_code): (str(account_role_code), str(account_class_code))
            for account_code, account_role_code, account_class_code in rows
        }

    def _load_changes_in_equity_lines(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        statement_scope_code: str,
    ) -> list[dict[str, object]]:
        income_lines = self._load_operational_income_lines(
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            statement_scope_code=statement_scope_code,
        )
        period_net_income = sum(
            (
                Decimal(str(line["credit_amount"])) - Decimal(str(line["debit_amount"]))
                for line in income_lines
            ),
            Decimal("0"),
        )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the financial-statement read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the financial-statement read",
            )[0]
            period_ids = self._statement_period_ids(
                connection,
                tenant_id,
                period_code,
                statement_scope_code,
            )
            scope_start = connection.execute(
                """
                SELECT MIN(period_start_date)
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND fiscal_period_id = ANY(%s)
                """,
                (tenant_id, period_ids),
            ).fetchone()[0]
            opening_equity = self._opening_equity_amount(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                scope_start,
            )
            other_equity_movements = self._other_equity_movement_amount(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                period_ids,
            )
        closing_equity = opening_equity + period_net_income + other_equity_movements
        return [
            self._equity_movement_line("opening_equity", opening_equity),
            self._equity_movement_line("period_net_income", period_net_income),
            self._equity_movement_line("other_equity_movements", other_equity_movements),
            self._equity_movement_line("closing_equity", closing_equity),
        ]

    def _opening_equity_amount(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        scope_start: date,
    ) -> Decimal:
        prior_snapshot = connection.execute(
            """
            SELECT trial_balance_snapshot.trial_balance_snapshot_id
            FROM accounting_core.fiscal_period
            JOIN accounting_reporting.trial_balance_snapshot
              ON trial_balance_snapshot.tenant_account_id = fiscal_period.tenant_account_id
             AND trial_balance_snapshot.fiscal_period_id = fiscal_period.fiscal_period_id
             AND trial_balance_snapshot.legal_entity_id = %s
             AND trial_balance_snapshot.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.period_end_date < %s
              AND fiscal_period.period_status_code = 'hard_closed'
            ORDER BY fiscal_period.period_end_date DESC, fiscal_period.period_code DESC
            LIMIT 1
            """,
            (legal_entity_id, book_id, tenant_id, scope_start),
        ).fetchone()
        if prior_snapshot is not None:
            amount = connection.execute(
                """
                SELECT COALESCE(
                    SUM(
                        trial_balance_line.credit_total_amount
                        - trial_balance_line.debit_total_amount
                    ),
                    0
                )
                FROM accounting_reporting.trial_balance_line
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = trial_balance_line.tenant_account_id
                 AND chart_account.chart_account_id = trial_balance_line.chart_account_id
                WHERE trial_balance_line.tenant_account_id = %s
                  AND trial_balance_line.trial_balance_snapshot_id = %s
                  AND chart_account.account_class_code = 'equity'
                """,
                (tenant_id, prior_snapshot[0]),
            ).fetchone()[0]
            return Decimal(amount)
        amount = connection.execute(
            """
            SELECT COALESCE(
                SUM(journal_entry_line.credit_amount - journal_entry_line.debit_amount),
                0
            )
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND general_journal.accounting_date <= %s
              AND chart_account.account_class_code = 'equity'
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                scope_start - timedelta(days=1),
            ),
        ).fetchone()[0]
        return Decimal(amount)

    def _other_equity_movement_amount(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_ids: list[UUID],
    ) -> Decimal:
        amount = connection.execute(
            """
            SELECT COALESCE(
                SUM(journal_entry_line.credit_amount - journal_entry_line.debit_amount),
                0
            )
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND general_journal.fiscal_period_id = ANY(%s)
              AND chart_account.account_class_code = 'equity'
              AND general_journal.journal_reference NOT LIKE %s
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                period_ids,
                _CLOSING_JOURNAL_PATTERN,
            ),
        ).fetchone()[0]
        return Decimal(amount)

    def _equity_movement_line(
        self,
        account_role_code: str,
        amount: Decimal,
        account_class_code: str = "equity",
    ) -> dict[str, object]:
        debit_amount = Decimal("0") if amount >= 0 else -amount
        credit_amount = amount if amount >= 0 else Decimal("0")
        return {
            "chart_account_code": "",
            "account_role_code": account_role_code,
            "account_class_code": account_class_code,
            "debit_amount": debit_amount,
            "credit_amount": credit_amount,
        }

    def _load_cash_flow_lines(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        statement_scope_code: str,
    ) -> list[dict[str, object]]:
        income_lines = self._load_operational_income_lines(
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            statement_scope_code=statement_scope_code,
        )
        period_net_income = sum(
            (
                Decimal(str(line["credit_amount"])) - Decimal(str(line["debit_amount"]))
                for line in income_lines
            ),
            Decimal("0"),
        )
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the financial-statement read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the financial-statement read",
            )[0]
            period_ids = self._statement_period_ids(
                connection,
                tenant_id,
                period_code,
                statement_scope_code,
            )
            scope_start = connection.execute(
                """
                SELECT MIN(period_start_date)
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND fiscal_period_id = ANY(%s)
                """,
                (tenant_id, period_ids),
            ).fetchone()[0]
            opening_cash = self._opening_cash_amount(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                scope_start,
            )
            operating_working_capital = self._operating_working_capital_amount(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                period_ids,
            )
            cash_from_financing = self._other_equity_movement_amount(
                connection,
                tenant_id,
                legal_entity_id,
                book_id,
                period_ids,
            )
        cash_from_investing = Decimal("0")
        cash_from_operations = period_net_income + operating_working_capital
        net_cash_change = cash_from_operations + cash_from_investing + cash_from_financing
        closing_cash = opening_cash + net_cash_change
        return [
            self._equity_movement_line("period_net_income", period_net_income, ""),
            self._equity_movement_line(
                "operating_working_capital", operating_working_capital, ""
            ),
            self._equity_movement_line("cash_from_operations", cash_from_operations, ""),
            self._equity_movement_line("cash_from_investing", cash_from_investing, ""),
            self._equity_movement_line("cash_from_financing", cash_from_financing, ""),
            self._equity_movement_line("net_cash_change", net_cash_change, ""),
            self._equity_movement_line("opening_cash", opening_cash, ""),
            self._equity_movement_line("closing_cash", closing_cash, ""),
        ]

    def _opening_cash_amount(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        scope_start: date,
    ) -> Decimal:
        prior_snapshot = connection.execute(
            """
            SELECT trial_balance_snapshot.trial_balance_snapshot_id
            FROM accounting_core.fiscal_period
            JOIN accounting_reporting.trial_balance_snapshot
              ON trial_balance_snapshot.tenant_account_id = fiscal_period.tenant_account_id
             AND trial_balance_snapshot.fiscal_period_id = fiscal_period.fiscal_period_id
             AND trial_balance_snapshot.legal_entity_id = %s
             AND trial_balance_snapshot.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.period_end_date < %s
              AND fiscal_period.period_status_code = 'hard_closed'
            ORDER BY fiscal_period.period_end_date DESC, fiscal_period.period_code DESC
            LIMIT 1
            """,
            (legal_entity_id, book_id, tenant_id, scope_start),
        ).fetchone()
        if prior_snapshot is not None:
            amount = connection.execute(
                """
                SELECT COALESCE(
                    SUM(
                        trial_balance_line.debit_total_amount
                        - trial_balance_line.credit_total_amount
                    ),
                    0
                )
                FROM accounting_reporting.trial_balance_line
                JOIN accounting_core.account_role_mapping
                  ON account_role_mapping.tenant_account_id = trial_balance_line.tenant_account_id
                 AND account_role_mapping.chart_account_id = trial_balance_line.chart_account_id
                 AND account_role_mapping.accounting_book_id = %s
                 AND account_role_mapping.account_role_code = 'cash_receipt'
                 AND account_role_mapping.valid_to IS NULL
                WHERE trial_balance_line.tenant_account_id = %s
                  AND trial_balance_line.trial_balance_snapshot_id = %s
                """,
                (book_id, tenant_id, prior_snapshot[0]),
            ).fetchone()[0]
            return Decimal(amount)
        amount = connection.execute(
            """
            SELECT COALESCE(
                SUM(journal_entry_line.debit_amount - journal_entry_line.credit_amount),
                0
            )
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.account_role_mapping
              ON account_role_mapping.tenant_account_id = journal_entry_line.tenant_account_id
             AND account_role_mapping.chart_account_id = journal_entry_line.chart_account_id
             AND account_role_mapping.accounting_book_id = %s
             AND account_role_mapping.account_role_code = 'cash_receipt'
             AND account_role_mapping.valid_to IS NULL
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND general_journal.accounting_date <= %s
            """,
            (
                book_id,
                tenant_id,
                legal_entity_id,
                book_id,
                scope_start - timedelta(days=1),
            ),
        ).fetchone()[0]
        return Decimal(amount)

    def _operating_working_capital_amount(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_ids: list[UUID],
    ) -> Decimal:
        amount = connection.execute(
            """
            SELECT COALESCE(
                SUM(journal_entry_line.credit_amount - journal_entry_line.debit_amount),
                0
            )
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND general_journal.fiscal_period_id = ANY(%s)
              AND chart_account.account_class_code IN ('asset', 'liability')
              AND chart_account.chart_account_id NOT IN (
                    SELECT account_role_mapping.chart_account_id
                    FROM accounting_core.account_role_mapping
                    WHERE account_role_mapping.tenant_account_id = %s
                      AND account_role_mapping.accounting_book_id = %s
                      AND account_role_mapping.account_role_code = 'cash_receipt'
                      AND account_role_mapping.valid_to IS NULL
              )
              AND general_journal.journal_reference NOT LIKE %s
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                period_ids,
                tenant_id,
                book_id,
                _CLOSING_JOURNAL_PATTERN,
            ),
        ).fetchone()[0]
        return Decimal(amount)

    def _load_operational_income_lines(
        self,
        *,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        statement_scope_code: str = "",
    ) -> list[dict[str, object]]:
        with self._session() as connection:
            tenant_id = self._require_tenant(connection)
            legal_entity_id = self._require_legal_entity(
                connection,
                tenant_id,
                legal_entity_reference,
                next_action="the financial-statement read",
            )
            book_id = self._require_book_for_close(
                connection,
                tenant_id,
                legal_entity_id,
                accounting_book_reference,
                next_action="the financial-statement read",
            )[0]
            period_ids = self._statement_period_ids(
                connection,
                tenant_id,
                period_code,
                statement_scope_code,
            )
            rows = connection.execute(
                """
                SELECT chart_account.chart_account_code,
                       account_role_mapping.account_role_code,
                       chart_account.account_class_code,
                       SUM(journal_entry_line.debit_amount),
                       SUM(journal_entry_line.credit_amount)
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
                 AND general_journal.general_journal_id = journal_entry_line.general_journal_id
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                LEFT JOIN accounting_core.account_role_mapping
                  ON account_role_mapping.tenant_account_id = chart_account.tenant_account_id
                 AND account_role_mapping.chart_account_id = chart_account.chart_account_id
                 AND account_role_mapping.valid_to IS NULL
                WHERE general_journal.tenant_account_id = %s
                  AND general_journal.legal_entity_id = %s
                  AND general_journal.accounting_book_id = %s
                  AND general_journal.fiscal_period_id = ANY(%s)
                  AND chart_account.account_class_code IN ('revenue', 'expense')
                  AND general_journal.journal_reference NOT LIKE %s
                GROUP BY chart_account.chart_account_code,
                         account_role_mapping.account_role_code,
                         chart_account.account_class_code
                ORDER BY chart_account.chart_account_code
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    book_id,
                    period_ids,
                    _CLOSING_JOURNAL_PATTERN,
                ),
            ).fetchall()
        lines: list[dict[str, object]] = []
        for account_code, account_role_code, account_class_code, debit_total, credit_total in rows:
            if account_role_code is None:
                raise AccountingValidationError(
                    f"account_role_mapping is missing for chart account {account_code}. "
                    "Create the account_role_mapping row, then retry the financial-statement read."
                )
            lines.append(
                {
                    "chart_account_code": str(account_code),
                    "account_role_code": str(account_role_code),
                    "account_class_code": str(account_class_code),
                    "debit_amount": Decimal(debit_total),
                    "credit_amount": Decimal(credit_total),
                }
            )
        return lines

    def _statement_period_ids(
        self,
        connection: object,
        tenant_id: UUID,
        period_code: str,
        statement_scope_code: str,
    ) -> list[UUID]:
        period_id, calendar_id, requested_code, period_start_date = connection.execute(
            """
            SELECT fiscal_period_id, fiscal_calendar_id, period_code, period_start_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            """,
            (tenant_id, period_code),
        ).fetchone()
        if statement_scope_code in {"", "period"}:
            return [period_id]
        fiscal_year = _fiscal_year_identity(str(requested_code), period_start_date)
        peers = connection.execute(
            """
            SELECT fiscal_period_id, period_code, period_start_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s
              AND fiscal_calendar_id = %s
              AND period_start_date <= %s
            ORDER BY period_start_date, period_code
            """,
            (tenant_id, calendar_id, period_start_date),
        ).fetchall()
        return [
            peer_id
            for peer_id, peer_code, peer_start in peers
            if _fiscal_year_identity(str(peer_code), peer_start) == fiscal_year
        ]

    @contextmanager
    def _consistent_read_session(self) -> Iterator[object]:
        with self._session() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self._active_connection = connection
            try:
                yield connection
            finally:
                self._active_connection = None

    @contextmanager
    def _session(self, *, readiness: bool = False) -> Iterator[object]:
        if self._active_connection is not None:
            yield self._active_connection
            return
        psycopg = _import_psycopg()
        try:
            database_url = self._database_url
            if readiness:
                connection_options = psycopg.conninfo.conninfo_to_dict(database_url)
                host_list = (
                    connection_options.get("host")
                    or connection_options.get("hostaddr")
                    or ""
                )
                if "," in host_list:
                    raise AccountingValidationError(
                        "readiness requires a single PostgreSQL host."
                    )
                configured_timeout = connection_options.get("connect_timeout")
                timeout_seconds = (
                    int(configured_timeout) if configured_timeout is not None else None
                )
                if (
                    timeout_seconds is None
                    or timeout_seconds <= 0
                    or timeout_seconds > _READINESS_CONNECT_TIMEOUT_SECONDS
                ):
                    connection_options["connect_timeout"] = str(
                        _READINESS_CONNECT_TIMEOUT_SECONDS
                    )
                startup_options = connection_options.get("options") or ""
                configured_statement_timeout = (
                    _readiness_statement_timeout_milliseconds(startup_options)
                )
                if (
                    configured_statement_timeout is None
                    or configured_statement_timeout <= 0
                    or configured_statement_timeout
                    > _READINESS_STATEMENT_TIMEOUT_MILLISECONDS
                ):
                    connection_options["options"] = (
                        f"{startup_options} -c statement_timeout="
                        f"{_READINESS_STATEMENT_TIMEOUT_MILLISECONDS}ms"
                    ).strip()
                database_url = psycopg.conninfo.make_conninfo(**connection_options)
            connection = psycopg.connect(database_url)
        except AccountingValidationError:
            raise
        except Exception as error:
            raise AccountingValidationError(
                "PostgreSQL is not reachable. Start PostgreSQL 18, set ACCOUNTING_DATABASE_URL "
                "to that server, then retry posting."
            ) from error
        try:
            connection.execute("SET lock_timeout = '5s'")
            connection.execute("SET idle_in_transaction_session_timeout = '60s'")
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def check_readiness(self) -> None:
        """Verify PostgreSQL 18, tenant binding, and the complete schema contract."""
        try:
            readiness_deadline = (
                time.monotonic()
                + _READINESS_STATEMENT_TIMEOUT_MILLISECONDS / 1000
            )
            with self._session(readiness=True) as connection:
                self._require_tenant(
                    connection,
                    allow_privileged=False,
                    statement_deadline=readiness_deadline,
                )
                _set_readiness_statement_timeout(connection, readiness_deadline)
                version_ok, tables_ok, functions_ok, columns_ok, constraints_ok, control_triggers_ok, indexes_ok = connection.execute(
                    """
                    SELECT
                        current_setting('server_version_num')::integer
                            BETWEEN 180000 AND 189999,
                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(%s::text[]) AS required(object_name)
                            WHERE to_regclass(required.object_name) IS NULL
                        ),
                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(%s::text[]) AS required(function_name)
                            WHERE to_regprocedure(required.function_name) IS NULL
                        ),
                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(%s::text[], %s::text[], %s::text[])
                                AS required(schema_name, table_name, column_name)
                            LEFT JOIN pg_catalog.pg_namespace
                              ON pg_namespace.nspname = required.schema_name
                            LEFT JOIN pg_catalog.pg_class
                              ON pg_class.relnamespace = pg_namespace.oid
                             AND pg_class.relname = required.table_name
                            LEFT JOIN pg_catalog.pg_attribute
                              ON pg_attribute.attrelid = pg_class.oid
                             AND pg_attribute.attname = required.column_name
                             AND pg_attribute.attnum > 0
                             AND NOT pg_attribute.attisdropped
                            WHERE pg_attribute.attrelid IS NULL
                        ),
                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(
                                %s::text[], %s::text[], %s::text[],
                                %s::text[], %s::text[]
                            ) AS required(
                                schema_name, table_name, constraint_name,
                                constraint_type, constraint_fingerprint
                            )
                            LEFT JOIN pg_catalog.pg_namespace
                              ON pg_namespace.nspname = required.schema_name
                            LEFT JOIN pg_catalog.pg_class
                              ON pg_class.relnamespace = pg_namespace.oid
                             AND pg_class.relname = required.table_name
                            LEFT JOIN pg_catalog.pg_constraint
                              ON pg_constraint.conrelid = pg_class.oid
                             AND pg_constraint.conname = required.constraint_name
                            WHERE pg_constraint.oid IS NULL
                               OR pg_constraint.contype::text <> required.constraint_type
                               OR NOT pg_constraint.convalidated
                               OR COALESCE(
                                    (
                                        pg_catalog.to_jsonb(pg_constraint)
                                            ->> 'conenforced'
                                    )::boolean,
                                    true
                                  ) IS NOT TRUE
                               OR pg_constraint.condeferrable
                               OR pg_constraint.condeferred
                               OR pg_catalog.md5(
                                    pg_catalog.pg_get_constraintdef(
                                        pg_constraint.oid, true
                                    )
                                  ) <> required.constraint_fingerprint
                        )
                        AND NOT EXISTS (
                            SELECT 1
                            FROM unnest(
                                %s::text[], %s::text[], %s::text[],
                                %s::text[], %s::text[], %s::smallint[],
                                %s::text[]
                            ) AS required(
                                schema_name, table_name, trigger_name,
                                function_schema, function_name, trigger_type,
                                function_fingerprint
                            )
                            LEFT JOIN pg_catalog.pg_namespace AS trigger_namespace
                              ON trigger_namespace.nspname = required.schema_name
                            LEFT JOIN pg_catalog.pg_class AS relation
                              ON relation.relnamespace = trigger_namespace.oid
                             AND relation.relname = required.table_name
                            LEFT JOIN pg_catalog.pg_trigger AS trigger
                              ON trigger.tgrelid = relation.oid
                             AND trigger.tgname = required.trigger_name
                            LEFT JOIN pg_catalog.pg_namespace AS function_namespace
                              ON function_namespace.nspname = required.function_schema
                            LEFT JOIN pg_catalog.pg_proc AS function
                              ON function.oid = trigger.tgfoid
                             AND function.pronamespace = function_namespace.oid
                             AND function.proname = required.function_name
                             AND pg_catalog.pg_get_function_identity_arguments(function.oid) = ''
                            WHERE trigger.oid IS NULL
                               OR function.oid IS NULL
                               OR trigger.tgenabled <> 'O'
                               OR trigger.tgisinternal
                               OR trigger.tgtype <> required.trigger_type
                               OR trigger.tgconstraint = 0
                               OR NOT trigger.tgdeferrable
                               OR NOT trigger.tginitdeferred
                               OR trigger.tgqual IS NOT NULL
                               OR pg_catalog.cardinality(trigger.tgattr) <> 0
                               OR pg_catalog.md5(pg_catalog.pg_get_functiondef(function.oid))
                                  <> required.function_fingerprint
                        ),
                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(
                                %s::text[], %s::text[], %s::text[],
                                %s::text[], %s::text[], %s::smallint[],
                                %s::text[], %s::text[]
                            ) AS required(
                                schema_name, table_name, trigger_name,
                                function_schema, function_name, trigger_type,
                                trigger_columns, function_fingerprint
                            )
                            LEFT JOIN pg_catalog.pg_namespace AS trigger_namespace
                              ON trigger_namespace.nspname = required.schema_name
                            LEFT JOIN pg_catalog.pg_class AS relation
                              ON relation.relnamespace = trigger_namespace.oid
                             AND relation.relname = required.table_name
                            LEFT JOIN pg_catalog.pg_trigger AS trigger
                              ON trigger.tgrelid = relation.oid
                             AND trigger.tgname = required.trigger_name
                            LEFT JOIN pg_catalog.pg_namespace AS function_namespace
                              ON function_namespace.nspname = required.function_schema
                            LEFT JOIN pg_catalog.pg_proc AS function
                              ON function.oid = trigger.tgfoid
                             AND function.pronamespace = function_namespace.oid
                             AND function.proname = required.function_name
                             AND pg_catalog.pg_get_function_identity_arguments(function.oid) = ''
                            WHERE trigger.oid IS NULL
                               OR function.oid IS NULL
                               OR trigger.tgenabled <> 'O'
                               OR trigger.tgisinternal
                               OR trigger.tgtype <> required.trigger_type
                               OR trigger.tgconstraint <> 0
                               OR trigger.tgdeferrable
                               OR trigger.tginitdeferred
                               OR trigger.tgqual IS NOT NULL
                               OR COALESCE(
                                    pg_catalog.array_to_string(
                                        ARRAY(
                                            SELECT attribute.attname::text
                                            FROM unnest(trigger.tgattr::smallint[])
                                                 WITH ORDINALITY
                                                 AS trigger_column(attnum, position)
                                            JOIN pg_catalog.pg_attribute AS attribute
                                              ON attribute.attrelid = relation.oid
                                             AND attribute.attnum = trigger_column.attnum
                                            ORDER BY trigger_column.position
                                        ),
                                        ','
                                    ),
                                    ''
                                  ) <> required.trigger_columns
                               OR pg_catalog.md5(pg_catalog.pg_get_functiondef(function.oid))
                                  <> required.function_fingerprint
                        ),
                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(
                                %s::text[], %s::text[], %s::text[],
                                %s::text[], %s::boolean[], %s::text[],
                                %s::text[]
                            ) AS required(
                                schema_name, index_name, table_schema,
                                table_name, index_unique, index_predicate,
                                index_fingerprint
                            )
                            LEFT JOIN pg_catalog.pg_namespace AS index_namespace
                              ON index_namespace.nspname = required.schema_name
                            LEFT JOIN pg_catalog.pg_class AS index_relation
                              ON index_relation.relnamespace = index_namespace.oid
                             AND index_relation.relname = required.index_name
                            LEFT JOIN pg_catalog.pg_index AS index_definition
                              ON index_definition.indexrelid = index_relation.oid
                            LEFT JOIN pg_catalog.pg_namespace AS table_namespace
                              ON table_namespace.nspname = required.table_schema
                            LEFT JOIN pg_catalog.pg_class AS table_relation
                              ON table_relation.relnamespace = table_namespace.oid
                             AND table_relation.relname = required.table_name
                            WHERE index_relation.oid IS NULL
                               OR index_definition.indexrelid IS NULL
                               OR index_definition.indrelid <> table_relation.oid
                               OR NOT index_definition.indisvalid
                               OR NOT index_definition.indisready
                               OR NOT index_definition.indislive
                               OR index_definition.indisunique <> required.index_unique
                               OR index_definition.indisprimary
                               OR index_definition.indisexclusion
                               OR index_definition.indisreplident
                               OR index_definition.indnullsnotdistinct
                               OR COALESCE(
                                    pg_catalog.pg_get_expr(
                                        index_definition.indpred,
                                        index_definition.indrelid,
                                        true
                                    ),
                                    ''
                                  ) <> required.index_predicate
                               OR pg_catalog.md5(
                                    pg_catalog.pg_get_indexdef(
                                        index_definition.indexrelid
                                    )
                                  ) <> required.index_fingerprint
                        )
                    """,
                    (
                        list(_READINESS_TABLES),
                        list(_READINESS_FUNCTIONS),
                        [item[0] for item in _READINESS_COLUMNS],
                        [item[1] for item in _READINESS_COLUMNS],
                        [item[2] for item in _READINESS_COLUMNS],
                        [item[0] for item in _READINESS_CONSTRAINTS],
                        [item[1] for item in _READINESS_CONSTRAINTS],
                        [item[2] for item in _READINESS_CONSTRAINTS],
                        [item[3] for item in _READINESS_CONSTRAINTS],
                        [item[4] for item in _READINESS_CONSTRAINTS],
                        [item[0] for item in _READINESS_BALANCE_TRIGGERS],
                        [item[1] for item in _READINESS_BALANCE_TRIGGERS],
                        [item[2] for item in _READINESS_BALANCE_TRIGGERS],
                        [item[3] for item in _READINESS_BALANCE_TRIGGERS],
                        [item[4] for item in _READINESS_BALANCE_TRIGGERS],
                        [item[5] for item in _READINESS_BALANCE_TRIGGERS],
                        [item[6] for item in _READINESS_BALANCE_TRIGGERS],
                        [item[0] for item in _READINESS_CONTROL_TRIGGERS],
                        [item[1] for item in _READINESS_CONTROL_TRIGGERS],
                        [item[2] for item in _READINESS_CONTROL_TRIGGERS],
                        [item[3] for item in _READINESS_CONTROL_TRIGGERS],
                        [item[4] for item in _READINESS_CONTROL_TRIGGERS],
                        [item[5] for item in _READINESS_CONTROL_TRIGGERS],
                        [item[6] for item in _READINESS_CONTROL_TRIGGERS],
                        [
                            _READINESS_CONTROL_FUNCTION_FINGERPRINTS[(item[3], item[4])]
                            for item in _READINESS_CONTROL_TRIGGERS
                        ],
                        [item[0] for item in _READINESS_INDEX_DEFINITIONS],
                        [item[1] for item in _READINESS_INDEX_DEFINITIONS],
                        [item[2] for item in _READINESS_INDEX_DEFINITIONS],
                        [item[3] for item in _READINESS_INDEX_DEFINITIONS],
                        [item[4] for item in _READINESS_INDEX_DEFINITIONS],
                        [item[5] for item in _READINESS_INDEX_DEFINITIONS],
                        [item[6] for item in _READINESS_INDEX_DEFINITIONS],
                    ),
                ).fetchone()
                _set_readiness_statement_timeout(connection, readiness_deadline)
                rls_ok = connection.execute(
                    """
                    SELECT NOT EXISTS (
                        SELECT 1
                        FROM unnest(%s::text[], %s::text[])
                            AS required(schema_name, table_name)
                        LEFT JOIN pg_catalog.pg_namespace AS namespace
                          ON namespace.nspname = required.schema_name
                        LEFT JOIN pg_catalog.pg_class AS relation
                          ON relation.relnamespace = namespace.oid
                         AND relation.relname = required.table_name
                        WHERE relation.oid IS NULL
                           OR NOT relation.relrowsecurity
                           OR NOT relation.relforcerowsecurity
                    )
                    """,
                    (
                        [schema_name for schema_name, _table_name in _READINESS_RLS_TABLES],
                        [table_name for _schema_name, table_name in _READINESS_RLS_TABLES],
                    ),
                ).fetchone()[0]
                _set_readiness_statement_timeout(connection, readiness_deadline)
                policies_ok = connection.execute(
                    """
                    SELECT (
                        SELECT count(*)
                        FROM pg_catalog.pg_policies AS actual
                        WHERE (actual.schemaname, actual.tablename) IN (
                            SELECT required.schema_name, required.table_name
                            FROM unnest(%s::text[], %s::text[])
                                AS required(schema_name, table_name)
                        )
                    ) = %s
                    AND NOT EXISTS (
                        SELECT 1
                        FROM unnest(%s::text[], %s::text[], %s::text[])
                            AS required(schema_name, table_name, policy_name)
                        LEFT JOIN pg_catalog.pg_policies AS actual
                          ON actual.schemaname = required.schema_name
                         AND actual.tablename = required.table_name
                         AND actual.policyname = required.policy_name
                        WHERE actual.policyname IS NULL
                           OR actual.permissive <> 'PERMISSIVE'
                           OR pg_catalog.array_to_string(actual.roles, ',') <> 'public'
                           OR actual.cmd <> 'ALL'
                           OR COALESCE(actual.qual, '')
                              <> '(tenant_account_id = accounting_core.current_tenant_account_id())'
                           OR COALESCE(actual.with_check, '')
                              <> '(tenant_account_id = accounting_core.current_tenant_account_id())'
                    )
                    """,
                    (
                        [schema_name for schema_name, _table_name, _policy_name in _READINESS_RLS_POLICIES],
                        [table_name for _schema_name, table_name, _policy_name in _READINESS_RLS_POLICIES],
                        len(_READINESS_RLS_POLICIES),
                        [schema_name for schema_name, _table_name, _policy_name in _READINESS_RLS_POLICIES],
                        [table_name for _schema_name, table_name, _policy_name in _READINESS_RLS_POLICIES],
                        [policy_name for _schema_name, _table_name, policy_name in _READINESS_RLS_POLICIES],
                    ),
                ).fetchone()[0]
                _set_readiness_statement_timeout(connection, readiness_deadline)
                tenant_function_ok = connection.execute(
                    """
                    SELECT COALESCE(
                        (
                            SELECT pg_catalog.md5(pg_catalog.pg_get_functiondef(function.oid))
                            FROM pg_catalog.pg_proc AS function
                            JOIN pg_catalog.pg_namespace AS namespace
                              ON namespace.oid = function.pronamespace
                            WHERE namespace.nspname = 'accounting_core'
                              AND function.proname = 'current_tenant_account_id'
                              AND pg_catalog.pg_get_function_identity_arguments(function.oid) = ''
                        ),
                        ''
                    ) = %s
                    """,
                    (_READINESS_TENANT_FUNCTION_FINGERPRINT,),
                ).fetchone()[0]
                _set_readiness_statement_timeout(connection, readiness_deadline)
                column_rows = connection.execute(
                    """
                    SELECT namespace.nspname,
                           relation.relname,
                           attribute.attname,
                           pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                           attribute.attnotnull,
                           COALESCE(
                               pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid),
                               ''
                           ),
                           attribute.attidentity::text,
                           attribute.attgenerated::text,
                           COALESCE(
                               collation_namespace.nspname || '.' || collation_row.collname,
                               ''
                           )
                    FROM pg_catalog.pg_attribute AS attribute
                    JOIN pg_catalog.pg_class AS relation
                      ON relation.oid = attribute.attrelid
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    LEFT JOIN pg_catalog.pg_attrdef AS default_value
                      ON default_value.adrelid = relation.oid
                     AND default_value.adnum = attribute.attnum
                    LEFT JOIN pg_catalog.pg_collation AS collation_row
                      ON collation_row.oid = attribute.attcollation
                     AND attribute.attcollation <> 0
                    LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
                      ON collation_namespace.oid = collation_row.collnamespace
                    WHERE namespace.nspname IN (
                        'accounting_core', 'accounting_integration', 'accounting_reporting'
                    )
                      AND relation.relkind IN ('r', 'p')
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                    ORDER BY namespace.nspname, relation.relname, attribute.attnum
                    """
                ).fetchall()
                column_groups: dict[tuple[str, str], list[list[object]]] = {}
                for row in column_rows:
                    column_groups.setdefault((row[0], row[1]), []).append(list(row[2:]))
                required_column_fingerprints = {
                    (schema_name, table_name): (column_count, fingerprint)
                    for schema_name, table_name, column_count, fingerprint
                    in _READINESS_COLUMN_FINGERPRINTS
                }
                actual_column_fingerprints = {
                    table_key: (
                        required_column_fingerprints[table_key][0],
                        hashlib.sha256(
                            json.dumps(
                                metadata[: required_column_fingerprints[table_key][0]],
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                    )
                    for table_key, metadata in column_groups.items()
                    if table_key in required_column_fingerprints
                }
                columns_ok &= actual_column_fingerprints == required_column_fingerprints
                if not version_ok:
                    raise AccountingValidationError("PostgreSQL 18 is required.")
                if not all(
                    (
                        tables_ok,
                        functions_ok,
                        rls_ok,
                        policies_ok,
                        tenant_function_ok,
                        columns_ok,
                        constraints_ok,
                        control_triggers_ok,
                        indexes_ok,
                    )
                ):
                    raise AccountingValidationError(
                        "accounting database schema is incomplete."
                    )
        except AccountingValidationError:
            raise
        except Exception as error:
            raise AccountingValidationError(
                "accounting service readiness could not be verified."
            ) from error

    def _acquire_command_lock(self, connection: object, command_scope: str) -> None:
        """Serialize one tenant command scope until the current transaction ends."""
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (self._tenant_reference, command_scope),
        )

    def _require_tenant(
        self,
        connection: object,
        *,
        allow_privileged: bool = True,
        statement_deadline: float | None = None,
    ) -> UUID:
        if statement_deadline is not None:
            _set_readiness_statement_timeout(connection, statement_deadline)
        row = connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.tenant_account
            WHERE tenant_account_code = %s
            """,
            (self._tenant_reference,),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Tenant {self._tenant_reference} is not recorded. Create the tenant_account row, then retry posting."
            )
        requested_tenant_id = row[0]
        if statement_deadline is not None:
            _set_readiness_statement_timeout(connection, statement_deadline)
        bound_tenant_id = connection.execute(
            "SELECT accounting_core.current_tenant_account_id()"
        ).fetchone()[0]
        if bound_tenant_id is not None:
            if bound_tenant_id != requested_tenant_id:
                raise AccountingValidationError(
                    "the database session is not provisioned for this tenant. "
                    "Ask the platform operator to verify tenant provisioning, "
                    "then retry the request."
                )
            return requested_tenant_id
        if not allow_privileged:
            raise AccountingValidationError(
                "this request cannot be authorized for the requested tenant. "
                "Ask the platform operator to verify tenant provisioning, then retry."
            )
        if statement_deadline is not None:
            _set_readiness_statement_timeout(connection, statement_deadline)
        rolsuper, rolbypassrls = connection.execute(
            """
            SELECT rolsuper, rolbypassrls
            FROM pg_catalog.pg_roles
            WHERE rolname = session_user
            """
        ).fetchone()
        if rolsuper or rolbypassrls:
            return requested_tenant_id
        raise AccountingValidationError(
            "this request cannot be authorized for the requested tenant. "
            "Ask the platform operator to verify tenant provisioning, then retry."
        )

    def _require_legal_entity(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_reference: str,
        next_action: str = "posting",
    ) -> UUID:
        return self._load_legal_entity(connection, tenant_id, legal_entity_reference, next_action)[0]

    def _load_legal_entity(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_reference: str,
        next_action: str = "posting",
    ) -> tuple[UUID, str]:
        row = connection.execute(
            """
            SELECT legal_entity_id, functional_currency_code
            FROM accounting_core.legal_entity_record
            WHERE tenant_account_id = %s AND legal_entity_code = %s AND valid_to IS NULL
            """,
            (tenant_id, legal_entity_reference),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Legal entity {legal_entity_reference} is not recorded for this tenant. "
                f"Create the legal_entity_record row, then retry {next_action}."
            )
        return row[0], row[1]

    def _require_book(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_role_code: str,
        accounting_book_reference: str,
    ) -> UUID:
        row = connection.execute(
            """
            SELECT accounting_book_id
            FROM accounting_core.accounting_book
            WHERE tenant_account_id = %s
              AND legal_entity_id = %s
              AND book_role_code = %s
              AND valid_to IS NULL
            """,
            (tenant_id, legal_entity_id, book_role_code),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Accounting book {accounting_book_reference} is not recorded for this legal entity. "
                "Create the accounting_book row, then retry posting."
            )
        return row[0]

    def _require_open_book_period(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        accounting_date: date,
    ) -> UUID:
        """Require an open fiscal period for the selected accounting book."""
        return self._require_open_book_period_bounds(
            connection, tenant_id, book_id, accounting_date
        )[0]

    def _require_open_book_period_bounds(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        accounting_date: date,
    ) -> tuple[UUID, date, date]:
        """Return period identity and bounds when this accounting book is open."""
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   fiscal_period.period_code,
                   COALESCE(
                       accounting_book_period_control.period_status_code,
                       fiscal_period.period_status_code
                   ),
                   fiscal_period.period_start_date,
                   fiscal_period.period_end_date
            FROM accounting_core.fiscal_period
            LEFT JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = fiscal_period.tenant_account_id
             AND accounting_book_period_control.fiscal_period_id
                 = fiscal_period.fiscal_period_id
             AND accounting_book_period_control.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.period_start_date <= %s
              AND fiscal_period.period_end_date >= %s
            """,
            (book_id, tenant_id, accounting_date, accounting_date),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "
                "Create an open fiscal period on the tenant calendar, then retry posting."
            )
        period_id, period_code = row[0], row[1]
        self._acquire_command_lock(connection, f"period:{book_id}:{period_code}")
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   fiscal_period.period_code,
                   COALESCE(
                       accounting_book_period_control.period_status_code,
                       fiscal_period.period_status_code
                   ),
                   fiscal_period.period_start_date,
                   fiscal_period.period_end_date
            FROM accounting_core.fiscal_period
            LEFT JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = fiscal_period.tenant_account_id
             AND accounting_book_period_control.fiscal_period_id
                 = fiscal_period.fiscal_period_id
             AND accounting_book_period_control.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.fiscal_period_id = %s
            """,
            (book_id, tenant_id, period_id),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "
                "Create an open fiscal period on the tenant calendar, then retry posting."
            )
        if row[2] != "open":
            locked_marker = " (period_closed)" if row[2] == "hard_closed" else ""
            raise AccountingValidationError(
                f"Fiscal period {row[1]} is {row[2]}{locked_marker}. "
                "Open that period or post into an open period for this accounting book; "
                "no journal was written."
            )
        return row[0], row[3], row[4]

    def _require_adjusting_period(
        self, connection: object, tenant_id: UUID, accounting_date: date
    ) -> UUID:
        return self._require_adjusting_period_bounds(connection, tenant_id, accounting_date)[0]

    def _require_adjusting_period_bounds(
        self, connection: object, tenant_id: UUID, accounting_date: date
    ) -> tuple[UUID, date, date]:
        return self._require_period_bounds(
            connection,
            tenant_id,
            accounting_date,
            allowed_status_codes=frozenset({"open", "soft_closed"}),
            next_action="Reverse into an open or soft-closed period",
        )

    def _require_period_bounds(
        self,
        connection: object,
        tenant_id: UUID,
        accounting_date: date,
        *,
        allowed_status_codes: frozenset[str],
        next_action: str,
    ) -> tuple[UUID, date, date]:
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_code, period_status_code,
                   period_start_date, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s
              AND period_start_date <= %s
              AND period_end_date >= %s
            """,
            (tenant_id, accounting_date, accounting_date),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "
                "Create an open fiscal period on the tenant calendar, then retry posting."
            )
        period_id, period_code = row[0], row[1]
        self._acquire_command_lock(connection, f"period:{period_code}")
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_code, period_status_code,
                   period_start_date, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s
              AND fiscal_period_id = %s
            """,
            (tenant_id, period_id),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "
                "Create an open fiscal period on the tenant calendar, then retry posting."
            )
        if row[2] not in allowed_status_codes:
            locked_marker = " (period_closed)" if row[2] == "hard_closed" else ""
            raise AccountingValidationError(
                f"Fiscal period {row[1]} is {row[2]}{locked_marker}. {next_action}; "
                "no journal was written."
            )
        return row[0], row[3], row[4]

    def _resolve_accounting_policy(
        self, connection: object, tenant_id: UUID, proposal: JournalProposal
    ) -> AccountingPolicy:
        if proposal.tenant_reference != self._tenant_reference:
            raise AccountingValidationError(
                "proposal tenant scope does not match this deployment. "
                "Send the proposal to that tenant's accounting endpoint, then retry posting."
            )
        legal_entity_id, functional_currency = self._load_legal_entity(
            connection, tenant_id, proposal.legal_entity_reference
        )
        book_id, book_name = self._require_book_for_role(
            connection,
            tenant_id,
            legal_entity_id,
            proposal.intended_book_role_code,
        )
        _period_id, period_start, period_end = self._require_open_book_period_bounds(
            connection, tenant_id, book_id, proposal.accounting_date
        )
        mapping, policy_version, rule_version = self._load_role_mapping(
            connection, tenant_id, book_id, proposal
        )
        return AccountingPolicy(
            tenant_reference=proposal.tenant_reference,
            legal_entity_reference=proposal.legal_entity_reference,
            accounting_book_reference=book_name,
            intended_book_role_code=proposal.intended_book_role_code,
            transaction_currency=proposal.transaction_currency,
            functional_currency=functional_currency,
            open_period_start=period_start,
            open_period_end=period_end,
            chart_account_mapping=mapping,
            accounting_policy_version=policy_version,
            posting_rule_version=rule_version,
        )

    def _require_book_for_role(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_role_code: str,
    ) -> tuple[UUID, str]:
        row = connection.execute(
            """
            SELECT accounting_book_id, book_name
            FROM accounting_core.accounting_book
            WHERE tenant_account_id = %s
              AND legal_entity_id = %s
              AND book_role_code = %s
              AND valid_to IS NULL
            """,
            (tenant_id, legal_entity_id, book_role_code),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Accounting book for role {book_role_code} is not recorded for this legal entity. "
                "Create the accounting_book row, then retry posting."
            )
        return row[0], row[1]

    def _load_role_mapping(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        proposal: JournalProposal,
    ) -> tuple[dict[str, str], str, str]:
        role_codes = tuple(dict.fromkeys(line.account_role_code for line in proposal.lines))
        as_of = datetime.combine(
            proposal.accounting_date, datetime.min.time(), tzinfo=timezone.utc
        )
        rows = connection.execute(
            """
            SELECT account_role_mapping.account_role_code,
                   chart_account.chart_account_code,
                   account_role_mapping.accounting_policy_version,
                   account_role_mapping.posting_rule_version
            FROM accounting_core.account_role_mapping
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = account_role_mapping.tenant_account_id
             AND chart_account.chart_account_id = account_role_mapping.chart_account_id
            WHERE account_role_mapping.tenant_account_id = %s
              AND account_role_mapping.accounting_book_id = %s
              AND account_role_mapping.account_role_code = ANY(%s)
              AND account_role_mapping.valid_from <= %s
              AND (
                    account_role_mapping.valid_to IS NULL
                    OR account_role_mapping.valid_to > %s
                  )
            """,
            (tenant_id, book_id, list(role_codes), as_of, as_of),
        ).fetchall()
        if not rows:
            raise AccountingValidationError(
                "No account_role_mapping is effective for this book and accounting date. "
                "Create the account_role_mapping rows, then retry posting."
            )
        seen_roles: dict[str, tuple[str, str, str]] = {}
        for role_code, account_code, policy_version, rule_version in rows:
            if role_code in seen_roles:
                raise AccountingValidationError(
                    f"More than one effective account_role_mapping applies for role {role_code}. "
                    "Close the superseded mapping, then retry posting."
                )
            seen_roles[role_code] = (account_code, policy_version, rule_version)
        missing_roles = [role_code for role_code in role_codes if role_code not in seen_roles]
        if missing_roles:
            raise AccountingValidationError(
                f"Account role {missing_roles[0]} is not mapped on this book. "
                "Create the account_role_mapping row, then retry posting."
            )
        versions = {(policy_version, rule_version) for _code, policy_version, rule_version in seen_roles.values()}
        if len(versions) != 1:
            raise AccountingValidationError(
                "Account role mappings use more than one policy version. "
                "Approve a single effective mapping set, then retry posting."
            )
        policy_version, rule_version = next(iter(versions))
        return (
            {role_code: account_code for role_code, (account_code, _, _) in seen_roles.items()},
            policy_version,
            rule_version,
        )

    def _require_book_for_close(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        accounting_book_reference: str,
        next_action: str = "the close",
    ) -> tuple[UUID, str]:
        row = connection.execute(
            """
            SELECT accounting_book_id, reporting_currency_code
            FROM accounting_core.accounting_book
            WHERE tenant_account_id = %s
              AND legal_entity_id = %s
              AND book_name = %s
              AND valid_to IS NULL
            """,
            (tenant_id, legal_entity_id, accounting_book_reference),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Accounting book {accounting_book_reference} is not recorded for this legal entity. "
                f"Create the accounting_book row, then retry {next_action}."
            )
        return row[0], row[1]

    def _require_fiscal_period(
        self,
        connection: object,
        tenant_id: UUID,
        period_code: str,
        next_action: str = "the close",
    ) -> tuple[UUID, str, date]:
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_status_code, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            """,
            (tenant_id, period_code),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is not recorded for this tenant. "
                f"Create the fiscal_period row, then retry {next_action}."
            )
        return row[0], row[1], row[2]

    def _lock_book_period(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        period_code: str,
    ) -> tuple[UUID, str, date]:
        """Materialize and lock close state independently for one accounting book."""
        period_row = connection.execute(
            """
            SELECT fiscal_period_id, period_status_code, period_closed_at
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            """,
            (tenant_id, period_code),
        ).fetchone()
        if period_row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is not recorded for this tenant. "
                "Create the fiscal_period row, then retry the close."
            )
        period_id = period_row[0]
        connection.execute(
            """
            INSERT INTO accounting_core.accounting_book_period_control (
                tenant_account_id, accounting_book_id, fiscal_period_id,
                period_status_code, period_closed_at
            )
            SELECT accounting_book.tenant_account_id,
                   accounting_book.accounting_book_id,
                   fiscal_period.fiscal_period_id,
                   fiscal_period.period_status_code,
                   fiscal_period.period_closed_at
            FROM accounting_core.accounting_book
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id = accounting_book.tenant_account_id
            WHERE accounting_book.tenant_account_id = %s
              AND accounting_book.valid_to IS NULL
              AND fiscal_period.fiscal_period_id = %s
            ON CONFLICT (tenant_account_id, accounting_book_id, fiscal_period_id)
            DO NOTHING
            """,
            (tenant_id, period_id),
        )
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   accounting_book_period_control.period_status_code,
                   fiscal_period.period_end_date
            FROM accounting_core.fiscal_period
            JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = fiscal_period.tenant_account_id
             AND accounting_book_period_control.fiscal_period_id
                 = fiscal_period.fiscal_period_id
             AND accounting_book_period_control.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.fiscal_period_id = %s
            FOR UPDATE OF accounting_book_period_control
            """,
            (book_id, tenant_id, period_id),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} has no control row for this accounting book. "
                "Repair the fiscal-period control data for this book, then retry the close."
            )
        return row[0], row[1], row[2]

    def _load_book_period_state(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        period_code: str,
    ) -> tuple[UUID, str, date, date] | None:
        """Return the selected book's period state, falling back to legacy calendar state."""
        row = connection.execute(
            """
            SELECT fiscal_period.fiscal_period_id,
                   COALESCE(
                       accounting_book_period_control.period_status_code,
                       fiscal_period.period_status_code
                   ),
                   fiscal_period.period_start_date,
                   fiscal_period.period_end_date
            FROM accounting_core.fiscal_period
            LEFT JOIN accounting_core.accounting_book_period_control
              ON accounting_book_period_control.tenant_account_id
                 = fiscal_period.tenant_account_id
             AND accounting_book_period_control.fiscal_period_id
                 = fiscal_period.fiscal_period_id
             AND accounting_book_period_control.accounting_book_id = %s
            WHERE fiscal_period.tenant_account_id = %s
              AND fiscal_period.period_code = %s
            """,
            (book_id, tenant_id, period_code),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], row[2], row[3]

    def _load_period_state(
        self, connection: object, tenant_id: UUID, period_code: str
    ) -> tuple[UUID, str, date, date] | None:
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_status_code, period_start_date, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            """,
            (tenant_id, period_code),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], row[2], row[3]

    def _require_tenant_calendar(self, connection: object, tenant_id: UUID) -> UUID:
        row = connection.execute(
            """
            SELECT fiscal_calendar_id
            FROM accounting_core.fiscal_calendar
            WHERE tenant_account_id = %s
            ORDER BY calendar_code
            LIMIT 1
            """,
            (tenant_id,),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                "No fiscal_calendar is recorded for this tenant. "
                "Create the fiscal_calendar row, then retry the period open."
            )
        return row[0]

    def _period_open_document(
        self,
        legal_entity_reference: str,
        period_code: str,
        period_start_date: date,
        period_end_date: date,
        *,
        replayed: bool,
    ) -> dict[str, object]:
        return {
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": legal_entity_reference,
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
            "period_code": period_code,
            "period_status_code": "open",
            "period_start_date": period_start_date.isoformat(),
            "period_end_date": period_end_date.isoformat(),
            "replayed": replayed,
        }

    def _aggregate_trial_balance(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        through_date: date,
    ) -> tuple[tuple[UUID, str, Decimal, Decimal], ...]:
        rows = connection.execute(
            """
            SELECT chart_account.chart_account_id,
                   chart_account.chart_account_code,
                   SUM(journal_entry_line.debit_amount),
                   SUM(journal_entry_line.credit_amount)
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND general_journal.accounting_date <= %s
            GROUP BY chart_account.chart_account_id, chart_account.chart_account_code
            ORDER BY chart_account.chart_account_code
            """,
            (tenant_id, legal_entity_id, book_id, through_date),
        ).fetchall()
        return tuple(
            (row[0], row[1], Decimal(row[2]), Decimal(row[3])) for row in rows
        )

    def _aggregate_worksheet_trial_balance(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        through_date: date,
        *,
        exclude_adjusting: bool,
    ) -> tuple[tuple[UUID, str, Decimal, Decimal], ...]:
        rows = connection.execute(
            """
            SELECT chart_account.chart_account_id,
                   chart_account.chart_account_code,
                   SUM(journal_entry_line.debit_amount),
                   SUM(journal_entry_line.credit_amount)
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND general_journal.accounting_date <= %s
              AND general_journal.journal_reference NOT LIKE %s
              AND (
                    %s
                    OR journal_entry_line.account_role_code IS DISTINCT FROM %s
                  )
            GROUP BY chart_account.chart_account_id, chart_account.chart_account_code
            ORDER BY chart_account.chart_account_code
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                through_date,
                _CLOSING_JOURNAL_PATTERN,
                not exclude_adjusting,
                "adjusting",
            ),
        ).fetchall()
        return tuple(
            (row[0], row[1], Decimal(row[2]), Decimal(row[3])) for row in rows
        )

    def _count_source_journals(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        through_date: date,
    ) -> int:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s
                  AND legal_entity_id = %s
                  AND accounting_book_id = %s
                  AND accounting_date <= %s
                """,
                (tenant_id, legal_entity_id, book_id, through_date),
            ).fetchone()[0]
        )

    def _latest_close_snapshot(
        self,
        connection: object,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_id: UUID,
    ) -> tuple[UUID, datetime, int, str, str] | None:
        row = connection.execute(
            """
            SELECT trial_balance_snapshot_id, snapshot_generated_at,
                   source_journal_count, source_payload_hash, close_idempotency_key
            FROM accounting_reporting.trial_balance_snapshot
            WHERE tenant_account_id = %s
              AND legal_entity_id = %s
              AND accounting_book_id = %s
              AND fiscal_period_id = %s
            ORDER BY snapshot_generated_at DESC
            LIMIT 1
            """,
            (tenant_id, legal_entity_id, book_id, period_id),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], int(row[2]), row[3], str(row[4])

    def _load_snapshot_balance_lines(
        self, connection: object, tenant_id: UUID, snapshot_id: UUID
    ) -> tuple[tuple[str, Decimal, Decimal], ...]:
        rows = connection.execute(
            """
            SELECT chart_account.chart_account_code,
                   trial_balance_line.debit_total_amount,
                   trial_balance_line.credit_total_amount
            FROM accounting_reporting.trial_balance_line
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = trial_balance_line.tenant_account_id
             AND chart_account.chart_account_id = trial_balance_line.chart_account_id
            WHERE trial_balance_line.tenant_account_id = %s
              AND trial_balance_line.trial_balance_snapshot_id = %s
            ORDER BY chart_account.chart_account_code
            """,
            (tenant_id, snapshot_id),
        ).fetchall()
        return tuple((row[0], Decimal(row[1]), Decimal(row[2])) for row in rows)

    def _replay_close_receipt(
        self,
        connection: object,
        *,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_id: UUID,
        period_code: str,
        current_status: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
        idempotency_key: str,
    ) -> PeriodCloseReceipt:
        snapshot = self._latest_close_snapshot(
            connection, tenant_id, legal_entity_id, book_id, period_id
        )
        if snapshot is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is {current_status} without a trial-balance snapshot. "
                "Restore the trial_balance_snapshot for this book from the journal population, "
                "then retry the close."
            )
        stored_close_key = snapshot[4]
        if stored_close_key != idempotency_key:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is hard_closed (period_closed). "
                "Replay the original period-close idempotency key; "
                "a second close of a locked period is rejected."
            )
        return self._close_receipt_from_snapshot(
            snapshot,
            period_code=period_code,
            period_status_code=current_status,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            replayed=True,
        )

    def _replay_soft_close_receipt(
        self,
        connection: object,
        *,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_id: UUID,
        period_code: str,
        period_end_date: date,
        snapshot_currency_code: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
        idempotency_key: str,
    ) -> PeriodCloseReceipt:
        (
            period_closed_at,
            stored_idempotency_key,
            source_journal_count,
            source_payload_hash,
            evidence_complete,
        ) = connection.execute(
            """
            SELECT COALESCE(period_closed_at, clock_timestamp()),
                   soft_close_idempotency_key,
                   soft_close_source_journal_count,
                   soft_close_source_payload_hash,
                   (
                       soft_close_idempotency_key IS NOT NULL
                       AND soft_close_source_journal_count IS NOT NULL
                       AND soft_close_source_payload_hash IS NOT NULL
                   )
            FROM accounting_core.accounting_book_period_control
            WHERE tenant_account_id = %s
              AND accounting_book_id = %s
              AND fiscal_period_id = %s
            """,
            (tenant_id, book_id, period_id),
        ).fetchone()
        if not evidence_complete:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is soft_closed without durable close-command evidence. "
                "Restore the original evidence through an audited migration, then retry; "
                "do not reconstruct it from later ledger state."
            )
        if stored_idempotency_key != idempotency_key:
            raise IdempotencyConflictError(
                "period-close idempotency key was already used by the soft-close command. Replay the original close idempotency key, then retry the close."
            )
        return PeriodCloseReceipt(
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            period_status_code="soft_closed",
            snapshot_record_id="",
            snapshot_generated_at=period_closed_at,
            source_journal_count=source_journal_count,
            source_payload_hash=source_payload_hash,
            replayed=True,
        )

    def _persist_soft_close(
        self,
        connection: object,
        *,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_id: UUID,
        period_code: str,
        period_end_date: date,
        snapshot_currency_code: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
        idempotency_key: str,
    ) -> PeriodCloseReceipt:
        _lines, source_journal_count, source_payload_hash = self._live_close_source(
            connection,
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            book_id=book_id,
            period_end_date=period_end_date,
            period_code=period_code,
            snapshot_currency_code=snapshot_currency_code,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
        )
        period_closed_at = self._set_book_period_closed(
            connection, tenant_id, book_id, period_id, "soft_closed"
        )
        connection.execute(
            """
            UPDATE accounting_core.accounting_book_period_control
            SET soft_close_idempotency_key = %s,
                soft_close_source_payload_hash = %s,
                soft_close_source_journal_count = %s
            WHERE tenant_account_id = %s
              AND accounting_book_id = %s
              AND fiscal_period_id = %s
            """,
            (
                idempotency_key,
                source_payload_hash,
                source_journal_count,
                tenant_id,
                book_id,
                period_id,
            ),
        )
        self._insert_period_close_event(
            connection,
            tenant_id,
            period_code,
            accounting_book_reference,
            None,
            source_payload_hash,
        )
        return PeriodCloseReceipt(
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            period_status_code="soft_closed",
            snapshot_record_id="",
            snapshot_generated_at=period_closed_at,
            source_journal_count=source_journal_count,
            source_payload_hash=source_payload_hash,
            replayed=False,
        )

    def _live_close_source(
        self,
        connection: object,
        *,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_end_date: date,
        period_code: str,
        snapshot_currency_code: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
    ) -> tuple[tuple[tuple[UUID, str, Decimal, Decimal], ...], int, str]:
        lines = self._aggregate_trial_balance(
            connection, tenant_id, legal_entity_id, book_id, period_end_date
        )
        source_journal_count = self._count_source_journals(
            connection, tenant_id, legal_entity_id, book_id, period_end_date
        )
        source_payload_hash = _canonical_snapshot_hash(
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            snapshot_currency_code=snapshot_currency_code,
            source_journal_count=source_journal_count,
            lines=lines,
        )
        return lines, source_journal_count, source_payload_hash

    def _persist_period_close(
        self,
        connection: object,
        *,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_id: UUID,
        period_code: str,
        period_end_date: date,
        period_status_code: str,
        snapshot_currency_code: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
        idempotency_key: str,
    ) -> PeriodCloseReceipt:
        self._post_closing_journal(
            connection,
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            book_id=book_id,
            period_id=period_id,
            period_code=period_code,
            period_end_date=period_end_date,
            snapshot_currency_code=snapshot_currency_code,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
        )
        lines, source_journal_count, source_payload_hash = self._live_close_source(
            connection,
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            book_id=book_id,
            period_end_date=period_end_date,
            period_code=period_code,
            snapshot_currency_code=snapshot_currency_code,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
        )
        snapshot_id, snapshot_generated_at = connection.execute(
            """
            INSERT INTO accounting_reporting.trial_balance_snapshot (
                tenant_account_id, legal_entity_id, accounting_book_id, fiscal_period_id,
                snapshot_currency_code, source_journal_count, source_payload_hash,
                close_idempotency_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING trial_balance_snapshot_id, snapshot_generated_at
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                period_id,
                snapshot_currency_code,
                source_journal_count,
                source_payload_hash,
                idempotency_key,
            ),
        ).fetchone()
        for account_id, _account_code, debit_total, credit_total in lines:
            connection.execute(
                """
                INSERT INTO accounting_reporting.trial_balance_line (
                    tenant_account_id, trial_balance_snapshot_id, chart_account_id,
                    debit_total_amount, credit_total_amount, net_balance_amount
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    snapshot_id,
                    account_id,
                    debit_total,
                    credit_total,
                    debit_total - credit_total,
                ),
            )
        self._set_book_period_closed(
            connection, tenant_id, book_id, period_id, period_status_code
        )
        self._insert_period_close_event(
            connection,
            tenant_id,
            period_code,
            accounting_book_reference,
            snapshot_id,
            source_payload_hash,
        )
        return PeriodCloseReceipt(
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            period_status_code=period_status_code,
            snapshot_record_id=str(snapshot_id),
            snapshot_generated_at=snapshot_generated_at,
            source_journal_count=source_journal_count,
            source_payload_hash=source_payload_hash,
            replayed=False,
        )

    def _post_closing_journal(
        self,
        connection: object,
        *,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_id: UUID,
        period_code: str,
        period_end_date: date,
        snapshot_currency_code: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
    ) -> None:
        closing_reference = (
            "urn:cwl:accounting:general_journal:period_closing:"
            f"{period_code}:{accounting_book_reference}"
        )
        income_rows = connection.execute(
            """
            SELECT chart_account.chart_account_code,
                   account_role_mapping.account_role_code,
                   SUM(journal_entry_line.debit_amount),
                   SUM(journal_entry_line.credit_amount)
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_entry_line.tenant_account_id
             AND general_journal.general_journal_id = journal_entry_line.general_journal_id
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            JOIN accounting_core.account_role_mapping
              ON account_role_mapping.tenant_account_id = chart_account.tenant_account_id
             AND account_role_mapping.chart_account_id = chart_account.chart_account_id
             AND account_role_mapping.valid_to IS NULL
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.legal_entity_id = %s
              AND general_journal.accounting_book_id = %s
              AND general_journal.accounting_date <= %s
              AND account_role_mapping.account_role_code IN (
                    'usage_revenue', 'write_off_expense'
                  )
            GROUP BY chart_account.chart_account_code, account_role_mapping.account_role_code
            ORDER BY chart_account.chart_account_code
            """,
            (tenant_id, legal_entity_id, book_id, period_end_date),
        ).fetchall()
        closing_lines: list[PostedJournalLine] = []
        retained_earnings_amount = Decimal("0")
        for account_code, role_code, debit_total, credit_total in income_rows:
            net_amount = Decimal(credit_total) - Decimal(debit_total)
            if net_amount == 0:
                continue
            line_number = len(closing_lines) + 1
            if net_amount > 0:
                closing_lines.append(
                    PostedJournalLine(
                        line_number=line_number,
                        chart_account_code=str(account_code),
                        account_role_code=str(role_code),
                        debit_amount=net_amount,
                        credit_amount=Decimal("0"),
                    )
                )
            else:
                closing_lines.append(
                    PostedJournalLine(
                        line_number=line_number,
                        chart_account_code=str(account_code),
                        account_role_code=str(role_code),
                        debit_amount=Decimal("0"),
                        credit_amount=-net_amount,
                    )
                )
            retained_earnings_amount += net_amount
        if not closing_lines:
            return
        policy_version, rule_version = self._require_retained_earnings_mapping(
            connection, tenant_id, book_id
        )
        if retained_earnings_amount > 0:
            closing_lines.append(
                PostedJournalLine(
                    line_number=len(closing_lines) + 1,
                    chart_account_code="310100",
                    account_role_code="retained_earnings",
                    debit_amount=Decimal("0"),
                    credit_amount=retained_earnings_amount,
                )
            )
        elif retained_earnings_amount < 0:
            closing_lines.append(
                PostedJournalLine(
                    line_number=len(closing_lines) + 1,
                    chart_account_code="310100",
                    account_role_code="retained_earnings",
                    debit_amount=-retained_earnings_amount,
                    credit_amount=Decimal("0"),
                )
            )
        source_payload_hash = _canonical_closing_hash(
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            lines=tuple(closing_lines),
        )
        proposal_record_id = connection.execute(
            """
            INSERT INTO accounting_integration.journal_proposal_record (
                tenant_account_id, external_proposal_id, proposal_contract_version,
                idempotency_key, source_payload_hash, proposal_status_code, processed_at
            )
            VALUES (%s, uuidv7(), 1, %s, %s, 'posted', clock_timestamp())
            RETURNING proposal_record_id
            """,
            (
                tenant_id,
                f"{self._tenant_reference}:period_closing:{period_code}:"
                f"{accounting_book_reference}",
                source_payload_hash,
            ),
        ).fetchone()[0]
        policy = AccountingPolicy(
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            intended_book_role_code=self._book_role_code(connection, tenant_id, book_id),
            transaction_currency=snapshot_currency_code,
            functional_currency=snapshot_currency_code,
            open_period_start=period_end_date,
            open_period_end=period_end_date,
            chart_account_mapping={"retained_earnings": "310100"},
            accounting_policy_version=policy_version,
            posting_rule_version=rule_version,
        )
        self._insert_journal(
            connection,
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            book_id=book_id,
            period_id=period_id,
            journal_reference=closing_reference,
            proposal=_ClosingProposal(
                source_payload_hash=source_payload_hash,
                transaction_currency=snapshot_currency_code,
                transaction_date=period_end_date,
                accounting_date=period_end_date,
                source_event_references=(),
            ),
            policy=policy,
            proposal_record_id=proposal_record_id,
            lines=tuple(closing_lines),
        )

    def _require_retained_earnings_mapping(
        self, connection: object, tenant_id: UUID, book_id: UUID
    ) -> tuple[str, str]:
        row = connection.execute(
            """
            SELECT account_role_mapping.accounting_policy_version,
                   account_role_mapping.posting_rule_version
            FROM accounting_core.account_role_mapping
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = account_role_mapping.tenant_account_id
             AND chart_account.chart_account_id = account_role_mapping.chart_account_id
            WHERE account_role_mapping.tenant_account_id = %s
              AND account_role_mapping.accounting_book_id = %s
              AND account_role_mapping.account_role_code = 'retained_earnings'
              AND chart_account.chart_account_code = '310100'
              AND account_role_mapping.valid_to IS NULL
              AND chart_account.valid_to IS NULL
            """,
            (tenant_id, book_id),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                "account_role_mapping is missing for retained_earnings → 310100. "
                "Create the retained_earnings mapping and chart_account 310100, "
                "then retry the close."
            )
        return str(row[0]), str(row[1])

    def _book_role_code(
        self, connection: object, tenant_id: UUID, book_id: UUID
    ) -> str:
        return str(
            connection.execute(
                """
                SELECT book_role_code
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s AND accounting_book_id = %s
                """,
                (tenant_id, book_id),
            ).fetchone()[0]
        )

    def _set_book_period_closed(
        self,
        connection: object,
        tenant_id: UUID,
        book_id: UUID,
        period_id: UUID,
        period_status_code: str,
    ) -> datetime:
        """Close one book and retain aggregate calendar status only for compatibility."""
        period_closed_at = connection.execute(
            """
            UPDATE accounting_core.accounting_book_period_control
            SET period_status_code = %s,
                period_closed_at = clock_timestamp()
            WHERE tenant_account_id = %s
              AND accounting_book_id = %s
              AND fiscal_period_id = %s
            RETURNING period_closed_at
            """,
            (period_status_code, tenant_id, book_id, period_id),
        ).fetchone()[0]
        aggregate_row = connection.execute(
            """
            SELECT CASE
                       WHEN bool_and(
                           accounting_book_period_control.period_status_code = 'hard_closed'
                       ) THEN 'hard_closed'
                       WHEN bool_and(
                           accounting_book_period_control.period_status_code <> 'open'
                       ) THEN 'soft_closed'
                       ELSE 'open'
                   END,
                   max(accounting_book_period_control.period_closed_at)
            FROM accounting_core.accounting_book_period_control
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id
                 = accounting_book_period_control.tenant_account_id
             AND accounting_book.accounting_book_id
                 = accounting_book_period_control.accounting_book_id
            WHERE accounting_book_period_control.tenant_account_id = %s
              AND accounting_book_period_control.fiscal_period_id = %s
              AND accounting_book.valid_to IS NULL
            """,
            (tenant_id, period_id),
        ).fetchone()
        aggregate_status = aggregate_row[0] or "open"
        aggregate_closed_at = None if aggregate_status == "open" else aggregate_row[1]
        connection.execute(
            """
            UPDATE accounting_core.fiscal_period
            SET period_status_code = %s,
                period_closed_at = %s
            WHERE tenant_account_id = %s AND fiscal_period_id = %s
            """,
            (aggregate_status, aggregate_closed_at, tenant_id, period_id),
        )
        return period_closed_at

    def _insert_period_close_event(
        self,
        connection: object,
        tenant_id: UUID,
        period_code: str,
        accounting_book_reference: str,
        snapshot_id: UUID | None,
        payload_hash: str,
    ) -> None:
        payload_reference = (
            f"urn:cwl:accounting:trial_balance_snapshot:{snapshot_id}"
            if snapshot_id is not None
            else f"urn:cwl:accounting:fiscal_period:{period_code}"
        )
        connection.execute(
            """
            INSERT INTO accounting_integration.outbox_event (
                tenant_account_id, event_type_code, aggregate_reference,
                payload_reference, payload_hash
            )
            VALUES (%s, 'period_close', %s, %s, %s)
            """,
            (
                tenant_id,
                f"{accounting_book_reference}:fiscal_period:{period_code}",
                payload_reference,
                payload_hash,
            ),
        )

    def _close_receipt_from_snapshot(
        self,
        snapshot: tuple[UUID, datetime, int, str, str],
        *,
        period_code: str,
        period_status_code: str,
        legal_entity_reference: str,
        accounting_book_reference: str,
        replayed: bool,
    ) -> PeriodCloseReceipt:
        snapshot_id, snapshot_generated_at, source_journal_count, source_payload_hash, _close_key = (
            snapshot
        )
        return PeriodCloseReceipt(
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            accounting_book_reference=accounting_book_reference,
            period_code=period_code,
            period_status_code=period_status_code,
            snapshot_record_id=str(snapshot_id),
            snapshot_generated_at=snapshot_generated_at,
            source_journal_count=source_journal_count,
            source_payload_hash=source_payload_hash,
            replayed=replayed,
        )

    def _insert_journal(
        self,
        connection: object,
        *,
        tenant_id: UUID,
        legal_entity_id: UUID,
        book_id: UUID,
        period_id: UUID,
        journal_reference: str,
        proposal: JournalProposal | _ReversalProposal | _ClosingProposal | _AdjustingProposal,
        policy: AccountingPolicy,
        proposal_record_id: UUID,
        lines: tuple[PostedJournalLine, ...],
    ) -> UUID:
        connection.execute(
            "SELECT set_config('accounting_core.journal_write_role', %s, true)",
            (_journal_write_role(proposal),),
        )
        journal_id = connection.execute(
            """
            INSERT INTO accounting_core.general_journal (
                tenant_account_id, legal_entity_id, accounting_book_id, fiscal_period_id,
                journal_reference, journal_status_code, transaction_currency_code,
                functional_currency_code, transaction_date, accounting_date,
                source_proposal_record_id, accounting_policy_version, posting_rule_version
            )
            VALUES (%s, %s, %s, %s, %s, 'posted', %s, %s, %s, %s, %s, %s, %s)
            RETURNING general_journal_id
            """,
            (
                tenant_id,
                legal_entity_id,
                book_id,
                period_id,
                journal_reference,
                proposal.transaction_currency,
                policy.functional_currency,
                proposal.transaction_date,
                proposal.accounting_date,
                proposal_record_id,
                policy.accounting_policy_version,
                policy.posting_rule_version,
            ),
        ).fetchone()[0]
        for line in lines:
            chart_account_id = connection.execute(
                """
                SELECT chart_account_id
                FROM accounting_core.chart_account
                WHERE tenant_account_id = %s
                  AND accounting_book_id = %s
                  AND chart_account_code = %s
                  AND valid_to IS NULL
                """,
                (tenant_id, book_id, line.chart_account_code),
            ).fetchone()
            if chart_account_id is None:
                raise AccountingValidationError(
                    f"Chart account {line.chart_account_code} is not recorded on this book. "
                    "Create the chart_account row, then retry posting."
                )
            connection.execute(
                """
                INSERT INTO accounting_core.journal_entry_line (
                    tenant_account_id, general_journal_id, line_number, chart_account_id,
                    account_role_code, debit_amount, credit_amount
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    journal_id,
                    line.line_number,
                    chart_account_id[0],
                    line.account_role_code,
                    line.debit_amount,
                    line.credit_amount,
                ),
            )
        for reference in proposal.source_event_references:
            connection.execute(
                """
                INSERT INTO accounting_core.journal_source_reference (
                    tenant_account_id, general_journal_id, source_reference, source_payload_hash
                )
                VALUES (%s, %s, %s, %s)
                """,
                (tenant_id, journal_id, reference, proposal.source_payload_hash),
            )
        return journal_id

    def _insert_receipt(
        self,
        connection: object,
        tenant_id: UUID,
        proposal_record_id: UUID,
        journal_id: UUID,
        receipt: PostingReceipt,
    ) -> None:
        connection.execute(
            """
            INSERT INTO accounting_integration.posting_receipt (
                tenant_account_id, proposal_record_id, general_journal_id,
                receipt_status_code, receipt_payload_hash
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                proposal_record_id,
                journal_id,
                receipt.posting_status_code,
                _canonical_receipt_hash(receipt),
            ),
        )

    def _insert_outbox(
        self,
        connection: object,
        tenant_id: UUID,
        event_type_code: str,
        aggregate_reference: str,
        payload_reference: str,
        receipt: PostingReceipt,
    ) -> None:
        connection.execute(
            """
            INSERT INTO accounting_integration.outbox_event (
                tenant_account_id, event_type_code, aggregate_reference,
                payload_reference, payload_hash
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                event_type_code,
                aggregate_reference,
                payload_reference,
                _canonical_receipt_hash(receipt),
            ),
        )

    def _receipt_for_idempotency_key(
        self, connection: object, tenant_id: UUID, proposal: JournalProposal
    ) -> PostingReceipt:
        return PostingReceipt(
            receipt_reference=f"urn:cwl:accounting:posting_receipt:{proposal.proposal_id}",
            journal_reference=f"urn:cwl:accounting:general_journal:{proposal.proposal_id}",
            posting_status_code="posted",
            source_proposal_id=proposal.proposal_id,
            source_payload_hash=proposal.source_payload_hash,
            tenant_reference=proposal.tenant_reference,
            legal_entity_reference=proposal.legal_entity_reference,
            accounting_book_reference=self._book_name_for_proposal(
                connection, tenant_id, proposal.idempotency_key
            ),
            accounting_policy_version=self._policy_version_for_proposal(
                connection, tenant_id, proposal.idempotency_key
            )[0],
            posting_rule_version=self._policy_version_for_proposal(
                connection, tenant_id, proposal.idempotency_key
            )[1],
            line_count=self._line_count_for_proposal(
                connection, tenant_id, proposal.idempotency_key
            ),
        )

    def _receipt_for_journal(
        self, connection: object, tenant_id: UUID, journal_reference: str
    ) -> PostingReceipt:
        row = connection.execute(
            """
            SELECT general_journal.journal_reference,
                   journal_proposal_record.source_payload_hash,
                   journal_proposal_record.external_proposal_id,
                   general_journal.accounting_policy_version,
                   general_journal.posting_rule_version,
                   accounting_book.book_name,
                   legal_entity_record.legal_entity_code,
                   (
                       SELECT COUNT(*)
                       FROM accounting_core.journal_entry_line
                       WHERE tenant_account_id = general_journal.tenant_account_id
                         AND general_journal_id = general_journal.general_journal_id
                   ),
                   original_journal.journal_reference
            FROM accounting_core.general_journal
            JOIN accounting_integration.journal_proposal_record
              ON journal_proposal_record.tenant_account_id = general_journal.tenant_account_id
             AND journal_proposal_record.proposal_record_id = general_journal.source_proposal_record_id
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = general_journal.tenant_account_id
             AND accounting_book.accounting_book_id = general_journal.accounting_book_id
            JOIN accounting_core.legal_entity_record
              ON legal_entity_record.tenant_account_id = general_journal.tenant_account_id
             AND legal_entity_record.legal_entity_id = general_journal.legal_entity_id
            LEFT JOIN accounting_core.journal_reversal
              ON journal_reversal.tenant_account_id = general_journal.tenant_account_id
             AND journal_reversal.reversal_journal_id = general_journal.general_journal_id
            LEFT JOIN accounting_core.general_journal AS original_journal
              ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
             AND original_journal.general_journal_id = journal_reversal.original_journal_id
            WHERE general_journal.tenant_account_id = %s
              AND general_journal.journal_reference = %s
            """,
            (tenant_id, journal_reference),
        ).fetchone()
        source_proposal_id = journal_reference.removeprefix(
            "urn:cwl:accounting:general_journal:"
        ).removesuffix(":reversal")
        return PostingReceipt(
            receipt_reference=f"{journal_reference}:receipt",
            journal_reference=row[0],
            posting_status_code="posted",
            source_proposal_id=source_proposal_id,
            source_payload_hash=row[1],
            tenant_reference=self._tenant_reference,
            legal_entity_reference=row[6],
            accounting_book_reference=row[5],
            accounting_policy_version=row[3],
            posting_rule_version=row[4],
            line_count=int(row[7]),
            reversal_of_journal_reference=row[8],
        )

    def _book_name_for_proposal(
        self, connection: object, tenant_id: UUID, idempotency_key: str
    ) -> str:
        return connection.execute(
            """
            SELECT accounting_book.book_name
            FROM accounting_integration.journal_proposal_record
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_proposal_record.tenant_account_id
             AND general_journal.source_proposal_record_id = journal_proposal_record.proposal_record_id
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = general_journal.tenant_account_id
             AND accounting_book.accounting_book_id = general_journal.accounting_book_id
            WHERE journal_proposal_record.tenant_account_id = %s
              AND journal_proposal_record.idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()[0]

    def _policy_version_for_proposal(
        self, connection: object, tenant_id: UUID, idempotency_key: str
    ) -> tuple[str, str]:
        return connection.execute(
            """
            SELECT general_journal.accounting_policy_version,
                   general_journal.posting_rule_version
            FROM accounting_integration.journal_proposal_record
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = journal_proposal_record.tenant_account_id
             AND general_journal.source_proposal_record_id = journal_proposal_record.proposal_record_id
            WHERE journal_proposal_record.tenant_account_id = %s
              AND journal_proposal_record.idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()

    def _line_count_for_proposal(
        self, connection: object, tenant_id: UUID, idempotency_key: str
    ) -> int:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_integration.journal_proposal_record
                JOIN accounting_core.general_journal
                  ON general_journal.tenant_account_id = journal_proposal_record.tenant_account_id
                 AND general_journal.source_proposal_record_id = journal_proposal_record.proposal_record_id
                JOIN accounting_core.journal_entry_line
                  ON journal_entry_line.tenant_account_id = general_journal.tenant_account_id
                 AND journal_entry_line.general_journal_id = general_journal.general_journal_id
                WHERE journal_proposal_record.tenant_account_id = %s
                  AND journal_proposal_record.idempotency_key = %s
                """,
                (tenant_id, idempotency_key),
            ).fetchone()[0]
        )

    def _load_journal_row(
        self,
        connection: object,
        tenant_id: UUID,
        *,
        idempotency_key: str = "",
        journal_reference: str = "",
    ) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT general_journal.general_journal_id,
                   general_journal.journal_reference,
                   general_journal.journal_status_code,
                   general_journal.accounting_date,
                   general_journal.transaction_currency_code,
                   general_journal.functional_currency_code,
                   general_journal.accounting_policy_version,
                   general_journal.posting_rule_version,
                   legal_entity_record.legal_entity_code,
                   accounting_book.book_name,
                   journal_proposal_record.idempotency_key,
                   journal_proposal_record.source_payload_hash,
                   journal_proposal_record.external_proposal_id,
                   original_journal.journal_reference,
                   journal_reversal.reversal_reason_code
            FROM accounting_core.general_journal
            JOIN accounting_integration.journal_proposal_record
              ON journal_proposal_record.tenant_account_id = general_journal.tenant_account_id
             AND journal_proposal_record.proposal_record_id = general_journal.source_proposal_record_id
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = general_journal.tenant_account_id
             AND accounting_book.accounting_book_id = general_journal.accounting_book_id
            JOIN accounting_core.legal_entity_record
              ON legal_entity_record.tenant_account_id = general_journal.tenant_account_id
             AND legal_entity_record.legal_entity_id = general_journal.legal_entity_id
            LEFT JOIN accounting_core.journal_reversal
              ON journal_reversal.tenant_account_id = general_journal.tenant_account_id
             AND journal_reversal.reversal_journal_id = general_journal.general_journal_id
            LEFT JOIN accounting_core.general_journal AS original_journal
              ON original_journal.tenant_account_id = journal_reversal.tenant_account_id
             AND original_journal.general_journal_id = journal_reversal.original_journal_id
            WHERE general_journal.tenant_account_id = %s
              AND (%s OR journal_proposal_record.idempotency_key = %s)
              AND (%s OR general_journal.journal_reference = %s)
            """,
            (
                tenant_id,
                not idempotency_key,
                idempotency_key,
                not journal_reference,
                journal_reference,
            ),
        ).fetchone()

    def _load_published_receipt(
        self, connection: object, tenant_id: UUID, idempotency_key: str
    ) -> dict[str, object]:
        row = connection.execute(
            """
            SELECT posting_receipt.posting_receipt_id,
                   posting_receipt.created_at,
                   posting_receipt.receipt_status_code,
                   general_journal.journal_reference,
                   general_journal.transaction_currency_code,
                   general_journal.functional_currency_code,
                   general_journal.accounting_policy_version,
                   general_journal.posting_rule_version,
                   accounting_book.book_name,
                   legal_entity_record.legal_entity_code,
                   fiscal_period.period_code,
                   (
                       SELECT COUNT(*)
                       FROM accounting_core.journal_entry_line
                       WHERE tenant_account_id = general_journal.tenant_account_id
                         AND general_journal_id = general_journal.general_journal_id
                   ),
                   journal_proposal_record.idempotency_key,
                   journal_proposal_record.external_proposal_id,
                   journal_proposal_record.source_payload_hash
            FROM accounting_integration.posting_receipt
            JOIN accounting_integration.journal_proposal_record
              ON journal_proposal_record.tenant_account_id = posting_receipt.tenant_account_id
             AND journal_proposal_record.proposal_record_id = posting_receipt.proposal_record_id
            JOIN accounting_core.general_journal
              ON general_journal.tenant_account_id = posting_receipt.tenant_account_id
             AND general_journal.general_journal_id = posting_receipt.general_journal_id
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = general_journal.tenant_account_id
             AND accounting_book.accounting_book_id = general_journal.accounting_book_id
            JOIN accounting_core.legal_entity_record
              ON legal_entity_record.tenant_account_id = general_journal.tenant_account_id
             AND legal_entity_record.legal_entity_id = general_journal.legal_entity_id
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id = general_journal.tenant_account_id
             AND fiscal_period.fiscal_period_id = general_journal.fiscal_period_id
            WHERE posting_receipt.tenant_account_id = %s
              AND journal_proposal_record.idempotency_key = %s
            """,
            (tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                "posting receipt is missing for this idempotency key. "
                "Accept the proposal, then retry the receipt read."
            )
        recorded_at = _format_timestamp(row[1])
        return {
            "receipt_id": str(row[0]),
            "receipt_contract_version": 1,
            "idempotency_key": row[12],
            "source_proposal_id": str(row[13]),
            "source_payload_hash": row[14],
            "tenant_reference": self._tenant_reference,
            "legal_entity_reference": row[9],
            "accounting_book_reference": row[8],
            "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{row[10]}",
            "journal_reference": row[3],
            "accounting_policy_version": row[6],
            "posting_rule_version": row[7],
            "posting_status_code": row[2],
            "recorded_at": recorded_at,
            "posted_at": recorded_at,
            "line_count": int(row[11]),
            "transaction_currency": row[4],
            "functional_currency": row[5],
        }

    def _load_lines(
        self, connection: object, tenant_id: UUID, journal_id: UUID
    ) -> tuple[PostedJournalLine, ...]:
        rows = connection.execute(
            """
            SELECT journal_entry_line.line_number,
                   chart_account.chart_account_code,
                   journal_entry_line.account_role_code,
                   journal_entry_line.debit_amount,
                   journal_entry_line.credit_amount
            FROM accounting_core.journal_entry_line
            JOIN accounting_core.chart_account
              ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
             AND chart_account.chart_account_id = journal_entry_line.chart_account_id
            WHERE journal_entry_line.tenant_account_id = %s
              AND journal_entry_line.general_journal_id = %s
            ORDER BY journal_entry_line.line_number
            """,
            (tenant_id, journal_id),
        ).fetchall()
        return tuple(
            PostedJournalLine(
                line_number=row[0],
                chart_account_code=row[1],
                account_role_code=row[2],
                debit_amount=Decimal(row[3]),
                credit_amount=Decimal(row[4]),
            )
            for row in rows
        )

    def _proposal_identity(
        self, connection: object, tenant_id: UUID, proposal_record_id: UUID
    ) -> tuple[str, str]:
        row = connection.execute(
            """
            SELECT source_payload_hash, external_proposal_id
            FROM accounting_integration.journal_proposal_record
            WHERE tenant_account_id = %s AND proposal_record_id = %s
            """,
            (tenant_id, proposal_record_id),
        ).fetchone()
        return row[0], str(row[1])

    def _legal_entity_code(
        self, connection: object, tenant_id: UUID, legal_entity_id: UUID
    ) -> str:
        return connection.execute(
            """
            SELECT legal_entity_code
            FROM accounting_core.legal_entity_record
            WHERE tenant_account_id = %s AND legal_entity_id = %s
            """,
            (tenant_id, legal_entity_id),
        ).fetchone()[0]

    def _book_name(self, connection: object, tenant_id: UUID, book_id: UUID) -> str:
        return connection.execute(
            """
            SELECT book_name
            FROM accounting_core.accounting_book
            WHERE tenant_account_id = %s AND accounting_book_id = %s
            """,
            (tenant_id, book_id),
        ).fetchone()[0]


class _ClosingProposal:
    """Minimal proposal shape used when persisting an AIS period-closing journal."""

    def __init__(
        self,
        *,
        source_payload_hash: str,
        transaction_currency: str,
        transaction_date: date,
        accounting_date: date,
        source_event_references: tuple[str, ...],
    ) -> None:
        self.source_payload_hash = source_payload_hash
        self.transaction_currency = transaction_currency
        self.transaction_date = transaction_date
        self.accounting_date = accounting_date
        self.source_event_references = source_event_references


class _AdjustingProposal:
    """Minimal proposal shape used when persisting an AIS-owned adjusting journal."""

    def __init__(
        self,
        *,
        source_payload_hash: str,
        transaction_currency: str,
        transaction_date: date,
        accounting_date: date,
        source_event_references: tuple[str, ...],
    ) -> None:
        self.source_payload_hash = source_payload_hash
        self.transaction_currency = transaction_currency
        self.transaction_date = transaction_date
        self.accounting_date = accounting_date
        self.source_event_references = source_event_references


class _ReversalProposal:
    """Minimal proposal shape used when persisting an equal-and-opposite journal."""

    def __init__(
        self,
        *,
        source_payload_hash: str,
        transaction_currency: str,
        transaction_date: date,
        accounting_date: date,
        source_event_references: tuple[str, ...],
    ) -> None:
        self.source_payload_hash = source_payload_hash
        self.transaction_currency = transaction_currency
        self.transaction_date = transaction_date
        self.accounting_date = accounting_date
        self.source_event_references = source_event_references


def _journal_write_role(
    proposal: JournalProposal | _ReversalProposal | _ClosingProposal | _AdjustingProposal,
) -> str:
    """Return the session-local role AIS sets before a journal INSERT."""
    if isinstance(proposal, _ClosingProposal):
        return "period_closing"
    if isinstance(proposal, _AdjustingProposal):
        return "adjusting"
    if isinstance(proposal, _ReversalProposal):
        return "reversal"
    return ""


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the checked-in PostgreSQL 18 accounting foundation in migration order."""
    if not migration_path.is_file():
        raise AccountingValidationError(
            f"Foundation migration is missing at {migration_path}. "
            "Restore database/migrations/0001_accounting_foundation.sql, then retry."
        )
    class_migration_path = migration_path.parent / "0002_chart_account_class.sql"
    if not class_migration_path.is_file():
        raise AccountingValidationError(
            f"Chart-account class migration is missing at {class_migration_path}. "
            "Restore database/migrations/0002_chart_account_class.sql, then retry."
        )
    submission_migration_path = migration_path.parent / "0003_home_tax_submission.sql"
    if not submission_migration_path.is_file():
        raise AccountingValidationError(
            f"Home-tax submission migration is missing at {submission_migration_path}. "
            "Restore database/migrations/0003_home_tax_submission.sql, then retry."
        )
    close_key_migration_path = migration_path.parent / "0004_close_idempotency_key.sql"
    if not close_key_migration_path.is_file():
        raise AccountingValidationError(
            f"Close-idempotency-key migration is missing at {close_key_migration_path}. "
            "Restore database/migrations/0004_close_idempotency_key.sql, then retry."
        )
    period_guard_migration_path = migration_path.parent / "0005_closed_period_guard.sql"
    if not period_guard_migration_path.is_file():
        raise AccountingValidationError(
            f"Closed-period guard migration is missing at {period_guard_migration_path}. "
            "Restore database/migrations/0005_closed_period_guard.sql, then retry."
        )
    concurrency_migration_path = migration_path.parent / "0006_concurrency_hot_partition.sql"
    if not concurrency_migration_path.is_file():
        raise AccountingValidationError(
            f"Concurrency and hot-partition migration is missing at {concurrency_migration_path}. "
            "Restore database/migrations/0006_concurrency_hot_partition.sql, then retry."
        )
    runtime_binding_migration_path = migration_path.parent / "0007_runtime_tenant_binding.sql"
    if not runtime_binding_migration_path.is_file():
        raise AccountingValidationError(
            f"Runtime-tenant binding migration is missing at {runtime_binding_migration_path}. "
            "Restore database/migrations/0007_runtime_tenant_binding.sql, then retry."
        )
    period_open_command_migration_path = (
        migration_path.parent / "0008_fiscal_period_open_command.sql"
    )
    if not period_open_command_migration_path.is_file():
        raise AccountingValidationError(
            f"Fiscal-period-open command migration is missing at {period_open_command_migration_path}. "
            "Restore database/migrations/0008_fiscal_period_open_command.sql, then retry."
        )
    book_period_control_migration_path = (
        migration_path.parent / "0009_accounting_book_period_control.sql"
    )
    if not book_period_control_migration_path.is_file():
        raise AccountingValidationError(
            f"Accounting-book-period control migration is missing at {book_period_control_migration_path}. "
            "Restore database/migrations/0009_accounting_book_period_control.sql, then retry."
        )
    soft_close_evidence_migration_path = (
        migration_path.parent / "0010_soft_close_command_evidence.sql"
    )
    if not soft_close_evidence_migration_path.is_file():
        raise AccountingValidationError(
            f"Soft-close command-evidence migration is missing at {soft_close_evidence_migration_path}. "
            "Restore database/migrations/0010_soft_close_command_evidence.sql, then retry."
        )
    bank_statement_migration_path = (
        migration_path.parent / "0011_bank_statement_evidence.sql"
    )
    if not bank_statement_migration_path.is_file():
        raise AccountingValidationError(
            f"Bank-statement evidence migration is missing at {bank_statement_migration_path}. "
            "Restore database/migrations/0011_bank_statement_evidence.sql, then retry."
        )
    assignment_identity_migration_path = (
        migration_path.parent / "0012_bank_assignment_command_identity.sql"
    )
    if not assignment_identity_migration_path.is_file():
        raise AccountingValidationError(
            "Bank-account assignment command-identity migration is missing at "
            f"{assignment_identity_migration_path}. Restore "
            "database/migrations/0012_bank_assignment_command_identity.sql, then retry."
        )
    reconciliation_control_migration_path = (
        migration_path.parent / "0013_reconciliation_run_exception_evidence.sql"
    )
    if not reconciliation_control_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation run/exception evidence migration is missing at "
            f"{reconciliation_control_migration_path}. Restore "
            "database/migrations/0013_reconciliation_run_exception_evidence.sql, then retry."
        )
    allocation_control_migration_path = (
        migration_path.parent / "0014_reconciliation_candidate_allocation.sql"
    )
    if not allocation_control_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation candidate/allocation migration is missing at "
            f"{allocation_control_migration_path}. Restore "
            "database/migrations/0014_reconciliation_candidate_allocation.sql, then retry."
        )
    policy_repair_migration_path = (
        migration_path.parent / "0015_reconciliation_policy_repair.sql"
    )
    if not policy_repair_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation policy-repair migration is missing at "
            f"{policy_repair_migration_path}. Restore "
            "database/migrations/0015_reconciliation_policy_repair.sql, then retry."
        )
    psycopg = _import_psycopg()
    try:
        with psycopg.connect(
            database_url, autocommit=True, cursor_factory=psycopg.ClientCursor
        ) as connection:
            connection.execute(migration_path.read_text(encoding="utf-8"))
            connection.execute(class_migration_path.read_text(encoding="utf-8"))
            connection.execute(submission_migration_path.read_text(encoding="utf-8"))
            connection.execute(close_key_migration_path.read_text(encoding="utf-8"))
            connection.execute(period_guard_migration_path.read_text(encoding="utf-8"))
            connection.execute(concurrency_migration_path.read_text(encoding="utf-8"))
            connection.execute(runtime_binding_migration_path.read_text(encoding="utf-8"))
            connection.execute(period_open_command_migration_path.read_text(encoding="utf-8"))
            connection.execute(book_period_control_migration_path.read_text(encoding="utf-8"))
            connection.execute(soft_close_evidence_migration_path.read_text(encoding="utf-8"))
            connection.execute(bank_statement_migration_path.read_text(encoding="utf-8"))
            connection.execute(
                assignment_identity_migration_path.read_text(encoding="utf-8")
            )
            connection.execute(
                reconciliation_control_migration_path.read_text(encoding="utf-8")
            )
            connection.execute(
                allocation_control_migration_path.read_text(encoding="utf-8")
            )
            connection.execute(
                policy_repair_migration_path.read_text(encoding="utf-8")
            )
    except Exception as error:
        raise AccountingValidationError(
            "Foundation migration failed. Inspect the PostgreSQL error, restore a clean "
            "database, then retry the migration."
        ) from error


def _readiness_statement_timeout_milliseconds(options: str) -> int | None:
    """Read the last libpq statement-timeout option, if one is configured."""
    matches = list(
        re.finditer(
            r"(?:^|\s)(?:-c\s*|--)?statement_timeout\s*=\s*(\S+)",
            options,
            re.IGNORECASE,
        )
    )
    if not matches:
        return None
    value = re.fullmatch(
        r"(?P<amount>\d+(?:\.\d+)?)(?P<unit>us|ms|s|min|h|d)?",
        matches[-1].group(1),
        re.IGNORECASE,
    )
    if value is None:
        return None
    unit_milliseconds = {
        "us": Decimal("0.001"),
        "ms": Decimal("1"),
        "s": Decimal("1000"),
        "min": Decimal("60000"),
        "h": Decimal("3600000"),
        "d": Decimal("86400000"),
    }
    return int(
        Decimal(value.group("amount"))
        * unit_milliseconds.get(
            (value.group("unit") or "ms").lower(), Decimal("1")
        )
    )


def _set_readiness_statement_timeout(connection: object, deadline: float) -> None:
    """Apply only the remaining total readiness budget to the next statement."""
    remaining_milliseconds = int((deadline - time.monotonic()) * 1000)
    if remaining_milliseconds <= 0:
        raise AccountingValidationError("readiness time budget expired.")
    configured_timeout = connection.execute(
        "SELECT current_setting('statement_timeout')::interval"
    ).fetchone()[0]
    if isinstance(configured_timeout, timedelta) and configured_timeout > timedelta(0):
        remaining_milliseconds = min(
            remaining_milliseconds,
            max(1, int(configured_timeout.total_seconds() * 1000)),
        )
    connection.execute(
        "SELECT pg_catalog.set_config('statement_timeout', %s, false)",
        (f"{remaining_milliseconds}ms",),
    )


def _import_psycopg():
    try:
        return importlib.import_module("psycopg")
    except ImportError as error:
        raise AccountingValidationError(
            "the accounting database adapter is unavailable on this deployment. "
            "Ask the platform operator to install the pinned runtime dependencies, "
            "then retry the request."
        ) from error


def _require_proposal_uuid(proposal_id: str) -> UUID:
    return uuid.UUID(_require_proposal_id(proposal_id))


def _canonical_snapshot_hash(
    *,
    tenant_reference: str,
    legal_entity_reference: str,
    accounting_book_reference: str,
    period_code: str,
    snapshot_currency_code: str,
    source_journal_count: int,
    lines: tuple[tuple[UUID, str, Decimal, Decimal], ...],
) -> str:
    payload = json.dumps(
        {
            "accounting_book_reference": accounting_book_reference,
            "legal_entity_reference": legal_entity_reference,
            "lines": [
                {
                    "chart_account_code": account_code,
                    "credit_total_amount": format(credit_total, "f"),
                    "debit_total_amount": format(debit_total, "f"),
                    "net_balance_amount": format(debit_total - credit_total, "f"),
                }
                for _account_id, account_code, debit_total, credit_total in lines
            ],
            "period_code": period_code,
            "snapshot_currency_code": snapshot_currency_code,
            "source_journal_count": source_journal_count,
            "tenant_reference": tenant_reference,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_closing_hash(
    *,
    tenant_reference: str,
    legal_entity_reference: str,
    accounting_book_reference: str,
    period_code: str,
    lines: tuple[PostedJournalLine, ...],
) -> str:
    payload = json.dumps(
        {
            "accounting_book_reference": accounting_book_reference,
            "legal_entity_reference": legal_entity_reference,
            "lines": [
                {
                    "account_role_code": line.account_role_code,
                    "chart_account_code": line.chart_account_code,
                    "credit_amount": format(line.credit_amount, "f"),
                    "debit_amount": format(line.debit_amount, "f"),
                    "line_number": line.line_number,
                }
                for line in lines
            ],
            "period_code": period_code,
            "tenant_reference": tenant_reference,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_receipt_hash(receipt: PostingReceipt) -> str:
    payload = json.dumps(
        {
            "journal_reference": receipt.journal_reference,
            "line_count": receipt.line_count,
            "posting_status_code": receipt.posting_status_code,
            "receipt_reference": receipt.receipt_reference,
            "reversal_of_journal_reference": receipt.reversal_of_journal_reference,
            "source_payload_hash": receipt.source_payload_hash,
            "source_proposal_id": receipt.source_proposal_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fiscal_year_identity(period_code: str, period_start_date: date | None) -> str:
    matched = re.match(r"^(\d{4})", period_code)
    if matched:
        return matched.group(1)
    if period_start_date is not None:
        return f"{period_start_date.year:04d}"
    raise AccountingValidationError(
        "fiscal year identity is missing for this period. "
        "Use a period_code that starts with the four-digit year, then retry the financial-statement read."
    )


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _vat_period_movement_kind(
    idempotency_key: str,
    debit_roles: set[str],
    credit_roles: set[str],
) -> str | None:
    if ":issued_invoice_void:" in idempotency_key or (
        "tax_payable" in debit_roles
        and "usage_revenue" in debit_roles
        and "accounts_receivable" in credit_roles
    ):
        return "voided"
    if ":invoice_draft:" in idempotency_key or (
        "tax_payable" in credit_roles
        and "usage_revenue" in credit_roles
        and "accounts_receivable" in debit_roles
    ):
        return "issued"
    return None


def _unapplied_cash_movement_kind(
    idempotency_key: str,
    debit_roles: set[str],
    credit_roles: set[str],
) -> str | None:
    if ":unapplied_cash_application:" in idempotency_key or (
        "unapplied_cash" in debit_roles and "accounts_receivable" in credit_roles
    ):
        return "applied"
    if ":unapplied_cash_refund:" in idempotency_key or (
        "unapplied_cash" in debit_roles and "cash_receipt" in credit_roles
    ):
        return "refunded"
    if ":unapplied_cash:" in idempotency_key or (
        "unapplied_cash" in credit_roles and "cash_receipt" in debit_roles
    ):
        return "parked"
    return None


def _exact_amount_text(value: Decimal) -> str:
    return format(value, "f")


def _unsigned_aging_amount_text(value: Decimal) -> str:
    amount_text = format(value, "f")
    if "." not in amount_text:
        return amount_text
    return amount_text.rstrip("0").rstrip(".")


_VAT_REGISTER_REQUIRED_KEYS = frozenset(
    {
        "tenant_reference",
        "legal_entity_reference",
        "accounting_book_reference",
        "book_reference",
        "fiscal_period_reference",
        "as_of_date",
        "chart_account_code",
        "account_role_code",
        "issued_amount",
        "voided_amount",
        "closing_amount",
    }
)


def _vat_register_is_loadable(register_document: dict[str, object]) -> bool:
    return _VAT_REGISTER_REQUIRED_KEYS.issubset(register_document.keys())


def _home_tax_register_view(register_document: dict[str, object]) -> dict[str, object]:
    if _vat_register_is_loadable(register_document):
        return dict(register_document)
    return {
        "as_of_date": str(register_document.get("as_of_date") or ""),
        "closing_amount": str(register_document.get("closing_amount") or "0"),
    }


def _home_tax_submission_document(
    *,
    home_tax_submission_id: str,
    tenant_reference: str,
    legal_entity_reference: str,
    book_reference: str,
    period_code: str,
    vat_period_register: dict[str, object],
    rejection_reason_code: str,
    submission_status_code: str = "rejected",
) -> dict[str, object]:
    return {
        "home_tax_submission_id": home_tax_submission_id,
        "tenant_reference": tenant_reference,
        "legal_entity_reference": legal_entity_reference,
        "book_reference": book_reference,
        "fiscal_period_reference": f"urn:cwl:accounting:fiscal_period:{period_code}",
        "vat_period_register": vat_period_register,
        "submission_status_code": submission_status_code,
        "rejection_reason_code": rejection_reason_code,
    }


def _fifo_aging_open_items(
    line_rows: list[tuple[object, ...]],
    *,
    increase_is_debit: bool,
) -> list[list[object]]:
    open_items: list[list[object]] = []
    for accounting_date, _journal_reference, _line_number, debit_amount, credit_amount in line_rows:
        increase_amount = Decimal(str(debit_amount)) if increase_is_debit else Decimal(
            str(credit_amount)
        )
        decrease_amount = Decimal(str(credit_amount)) if increase_is_debit else Decimal(
            str(debit_amount)
        )
        if increase_amount > 0:
            open_items.append([accounting_date, increase_amount])
            continue
        remaining_decrease = decrease_amount
        for open_item in open_items:
            applied_amount = min(open_item[1], remaining_decrease)
            open_item[1] = open_item[1] - applied_amount
            remaining_decrease = remaining_decrease - applied_amount
        open_items = [open_item for open_item in open_items if open_item[1] > 0]
    return open_items


def _receivable_aging_bucket(outstanding_days: int) -> str:
    if outstanding_days <= 30:
        return "current"
    if outstanding_days <= 60:
        return "days_31_60"
    if outstanding_days <= 90:
        return "days_61_90"
    return "days_over_90"
