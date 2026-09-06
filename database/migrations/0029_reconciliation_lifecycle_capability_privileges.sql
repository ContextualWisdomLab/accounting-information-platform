BEGIN;

-- The session-lock helpers are SECURITY DEFINER coordination capabilities.
-- PostgreSQL grants EXECUTE on new functions to PUBLIC by default, so leave
-- them unavailable to ordinary schema users until the purpose-limited runtime
-- capability owner explicitly grants the named lifecycle command boundary.
REVOKE ALL ON FUNCTION accounting_core.acquire_reconciliation_lifecycle_session(text, uuid)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION accounting_core.release_reconciliation_lifecycle_session(text, uuid)
    FROM PUBLIC;

COMMIT;
