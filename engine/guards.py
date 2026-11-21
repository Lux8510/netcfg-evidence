"""Database-level guarantees shared by fresh and existing databases.

Every guard is expressed as DDL so the same rules apply to direct SQL, not only
to the application layer. SQLite and PostgreSQL use different trigger syntax;
both are listed explicitly so nothing is silently skipped.
"""

from __future__ import annotations

SQLITE_FUNCTION = None

SQLITE_TRIGGERS: dict[str, str] = {
    "audit_events_no_update": (
        "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    ),
    "audit_events_no_delete": (
        "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    ),
    "audit_chain_entries_no_update": (
        "CREATE TRIGGER IF NOT EXISTS audit_chain_entries_no_update BEFORE UPDATE ON audit_chain_entries "
        "BEGIN SELECT RAISE(ABORT, 'audit_chain_entries is append-only'); END"
    ),
    "audit_chain_entries_no_delete": (
        "CREATE TRIGGER IF NOT EXISTS audit_chain_entries_no_delete BEFORE DELETE ON audit_chain_entries "
        "BEGIN SELECT RAISE(ABORT, 'audit_chain_entries is append-only'); END"
    ),
    "config_versions_no_update": (
        "CREATE TRIGGER IF NOT EXISTS config_versions_no_update BEFORE UPDATE ON config_versions "
        "BEGIN SELECT RAISE(ABORT, 'config_versions is append-only'); END"
    ),
    "config_versions_no_delete": (
        "CREATE TRIGGER IF NOT EXISTS config_versions_no_delete BEFORE DELETE ON config_versions "
        "BEGIN SELECT RAISE(ABORT, 'config_versions is append-only'); END"
    ),
    "findings_no_update": (
        "CREATE TRIGGER IF NOT EXISTS findings_no_update BEFORE UPDATE ON findings "
        "BEGIN SELECT RAISE(ABORT, 'findings is append-only'); END"
    ),
    "explanations_no_update": (
        "CREATE TRIGGER IF NOT EXISTS explanations_no_update BEFORE UPDATE ON explanations "
        "BEGIN SELECT RAISE(ABORT, 'explanations is append-only'); END"
    ),
    "change_requests_snapshot_immutable": (
        "CREATE TRIGGER IF NOT EXISTS change_requests_snapshot_immutable BEFORE UPDATE ON change_requests "
        "WHEN OLD.finding_ids IS NOT NEW.finding_ids OR OLD.findings_snapshot IS NOT NEW.findings_snapshot "
        "BEGIN SELECT RAISE(ABORT, 'change request evidence is immutable'); END"
    ),
    "change_requests_snapshot_shape": (
        "CREATE TRIGGER IF NOT EXISTS change_requests_snapshot_shape BEFORE INSERT ON change_requests "
        "WHEN json_valid(NEW.finding_ids) = 0 OR json_valid(NEW.findings_snapshot) = 0 "
        "OR json_type(NEW.finding_ids) <> 'array' OR json_type(NEW.findings_snapshot) <> 'array' "
        "OR json_array_length(NEW.finding_ids) <> json_array_length(NEW.findings_snapshot) "
        "BEGIN SELECT RAISE(ABORT, 'change request evidence must match finding_ids'); END"
    ),
    "change_requests_findings_valid": (
        "CREATE TRIGGER IF NOT EXISTS change_requests_findings_valid BEFORE INSERT ON change_requests "
        "WHEN EXISTS (SELECT 1 FROM json_each(NEW.finding_ids) AS j "
        "LEFT JOIN findings f ON f.id = j.value "
        "WHERE f.id IS NULL OR f.device_id <> NEW.device_id OR f.status <> 'violated') "
        "BEGIN SELECT RAISE(ABORT, 'change request requires violated findings of the same device'); END"
    ),
    "change_runs_before_immutable": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_before_immutable BEFORE UPDATE ON change_runs "
        "WHEN OLD.before_version_id IS NOT NEW.before_version_id "
        "BEGIN SELECT RAISE(ABORT, 'change run before version is immutable'); END"
    ),
    "change_runs_after_immutable": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_after_immutable BEFORE UPDATE ON change_runs "
        "WHEN OLD.after_version_id IS NOT NULL AND OLD.after_version_id IS NOT NEW.after_version_id "
        "BEGIN SELECT RAISE(ABORT, 'change run after version is immutable'); END"
    ),
    "change_runs_version_device": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_version_device BEFORE INSERT ON change_runs "
        "WHEN (SELECT device_id FROM config_versions WHERE id = NEW.before_version_id) <> NEW.device_id "
        "BEGIN SELECT RAISE(ABORT, 'change run versions must belong to the device'); END"
    ),
    "change_runs_after_version_device": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_after_version_device BEFORE UPDATE ON change_runs "
        "WHEN NEW.after_version_id IS NOT NULL AND (SELECT device_id FROM config_versions WHERE id = NEW.after_version_id) <> NEW.device_id "
        "BEGIN SELECT RAISE(ABORT, 'change run versions must belong to the device'); END"
    ),
    "findings_evidence_shape": (
        "CREATE TRIGGER IF NOT EXISTS findings_evidence_shape BEFORE INSERT ON findings "
        "WHEN json_valid(NEW.evidence) = 0 OR json_type(NEW.evidence) <> 'array' "
        "BEGIN SELECT RAISE(ABORT, 'finding evidence must be a JSON array'); END"
    ),
}

POSTGRES_FUNCTION = """
CREATE OR REPLACE FUNCTION prevent_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql
"""

POSTGRES_TRUNCATE_FUNCTION = """
CREATE OR REPLACE FUNCTION prevent_truncate() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql
"""

POSTGRES_FINDINGS_VALID_FUNCTION = """
CREATE OR REPLACE FUNCTION validate_change_request_findings() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM json_array_elements_text(NEW.finding_ids) AS fid
             LEFT JOIN findings f ON f.id = fid
             WHERE f.id IS NULL OR f.device_id <> NEW.device_id OR f.status <> 'violated') THEN
    RAISE EXCEPTION 'change request requires violated findings of the same device';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

POSTGRES_CHANGE_RUN_FUNCTION = """
CREATE OR REPLACE FUNCTION validate_change_run_versions() RETURNS trigger AS $$
BEGIN
  IF (SELECT device_id FROM config_versions WHERE id = NEW.before_version_id)
     IS DISTINCT FROM NEW.device_id THEN
    RAISE EXCEPTION 'change run versions must belong to the device';
  END IF;
  IF NEW.after_version_id IS NOT NULL AND
     (SELECT device_id FROM config_versions WHERE id = NEW.after_version_id)
     IS DISTINCT FROM NEW.device_id THEN
    RAISE EXCEPTION 'change run versions must belong to the device';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

POSTGRES_TRIGGERS: dict[str, str] = {
    "audit_events_no_update": (
        "DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events; "
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "audit_events_no_delete": (
        "DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events; "
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "audit_events_no_truncate": (
        "DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events; "
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION prevent_truncate()"
    ),
    "audit_chain_entries_no_update": (
        "DROP TRIGGER IF EXISTS audit_chain_entries_no_update ON audit_chain_entries; "
        "CREATE TRIGGER audit_chain_entries_no_update BEFORE UPDATE ON audit_chain_entries "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "audit_chain_entries_no_delete": (
        "DROP TRIGGER IF EXISTS audit_chain_entries_no_delete ON audit_chain_entries; "
        "CREATE TRIGGER audit_chain_entries_no_delete BEFORE DELETE ON audit_chain_entries "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "config_versions_no_update": (
        "DROP TRIGGER IF EXISTS config_versions_no_update ON config_versions; "
        "CREATE TRIGGER config_versions_no_update BEFORE UPDATE ON config_versions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "config_versions_no_delete": (
        "DROP TRIGGER IF EXISTS config_versions_no_delete ON config_versions; "
        "CREATE TRIGGER config_versions_no_delete BEFORE DELETE ON config_versions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "config_versions_no_truncate": (
        "DROP TRIGGER IF EXISTS config_versions_no_truncate ON config_versions; "
        "CREATE TRIGGER config_versions_no_truncate BEFORE TRUNCATE ON config_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION prevent_truncate()"
    ),
    "findings_no_update": (
        "DROP TRIGGER IF EXISTS findings_no_update ON findings; "
        "CREATE TRIGGER findings_no_update BEFORE UPDATE ON findings "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "explanations_no_update": (
        "DROP TRIGGER IF EXISTS explanations_no_update ON explanations; "
        "CREATE TRIGGER explanations_no_update BEFORE UPDATE ON explanations "
        "FOR EACH ROW EXECUTE FUNCTION prevent_mutation()"
    ),
    "change_requests_snapshot_shape": (
        "DROP TRIGGER IF EXISTS change_requests_snapshot_shape ON change_requests; "
        "CREATE TRIGGER change_requests_snapshot_shape BEFORE INSERT ON change_requests "
        "FOR EACH ROW WHEN (json_typeof(NEW.finding_ids) <> 'array' "
        "OR json_typeof(NEW.findings_snapshot) <> 'array' "
        "OR json_array_length(NEW.finding_ids) <> json_array_length(NEW.findings_snapshot)) "
        "EXECUTE FUNCTION prevent_mutation()"
    ),
    "change_requests_findings_valid": (
        "DROP TRIGGER IF EXISTS change_requests_findings_valid ON change_requests; "
        "CREATE TRIGGER change_requests_findings_valid BEFORE INSERT ON change_requests "
        "FOR EACH ROW EXECUTE FUNCTION validate_change_request_findings()"
    ),
    "change_runs_before_immutable": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_before_immutable BEFORE UPDATE ON change_runs "
        "WHEN OLD.before_version_id IS NOT NEW.before_version_id "
        "BEGIN SELECT RAISE(ABORT, 'change run before version is immutable'); END"
    ),
    "change_runs_after_immutable": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_after_immutable BEFORE UPDATE ON change_runs "
        "WHEN OLD.after_version_id IS NOT NULL AND OLD.after_version_id IS NOT NEW.after_version_id "
        "BEGIN SELECT RAISE(ABORT, 'change run after version is immutable'); END"
    ),
    "change_runs_version_device": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_version_device BEFORE INSERT ON change_runs "
        "WHEN (SELECT device_id FROM config_versions WHERE id = NEW.before_version_id) <> NEW.device_id "
        "BEGIN SELECT RAISE(ABORT, 'change run versions must belong to the device'); END"
    ),
    "change_runs_after_version_device": (
        "CREATE TRIGGER IF NOT EXISTS change_runs_after_version_device BEFORE UPDATE ON change_runs "
        "WHEN NEW.after_version_id IS NOT NULL AND (SELECT device_id FROM config_versions WHERE id = NEW.after_version_id) <> NEW.device_id "
        "BEGIN SELECT RAISE(ABORT, 'change run versions must belong to the device'); END"
    ),
    "change_runs_before_immutable": (
        "DROP TRIGGER IF EXISTS change_runs_before_immutable ON change_runs; "
        "CREATE TRIGGER change_runs_before_immutable BEFORE UPDATE ON change_runs "
        "FOR EACH ROW WHEN (OLD.before_version_id IS DISTINCT FROM NEW.before_version_id) "
        "EXECUTE FUNCTION prevent_mutation()"
    ),
    "change_runs_after_immutable": (
        "DROP TRIGGER IF EXISTS change_runs_after_immutable ON change_runs; "
        "CREATE TRIGGER change_runs_after_immutable BEFORE UPDATE ON change_runs "
        "FOR EACH ROW WHEN (OLD.after_version_id IS NOT NULL AND OLD.after_version_id IS DISTINCT FROM NEW.after_version_id) "
        "EXECUTE FUNCTION prevent_mutation()"
    ),
    "change_runs_version_device": (
        "DROP TRIGGER IF EXISTS change_runs_version_device ON change_runs; "
        "CREATE TRIGGER change_runs_version_device BEFORE INSERT ON change_runs "
        "FOR EACH ROW EXECUTE FUNCTION validate_change_run_versions()"
    ),
    "change_runs_after_version_device": (
        "DROP TRIGGER IF EXISTS change_runs_after_version_device ON change_runs; "
        "CREATE TRIGGER change_runs_after_version_device BEFORE UPDATE ON change_runs "
        "FOR EACH ROW EXECUTE FUNCTION validate_change_run_versions()"
    ),
    "findings_evidence_shape": (
        "DROP TRIGGER IF EXISTS findings_evidence_shape ON findings; "
        "CREATE TRIGGER findings_evidence_shape BEFORE INSERT ON findings "
        "FOR EACH ROW WHEN (json_typeof(NEW.evidence) <> 'array') "
        "EXECUTE FUNCTION prevent_mutation()"
    ),
    "change_requests_snapshot_immutable": (
        "DROP TRIGGER IF EXISTS change_requests_snapshot_immutable ON change_requests; "
        "CREATE TRIGGER change_requests_snapshot_immutable BEFORE UPDATE ON change_requests "
        "FOR EACH ROW WHEN (OLD.finding_ids IS DISTINCT FROM NEW.finding_ids "
        "OR OLD.findings_snapshot IS DISTINCT FROM NEW.findings_snapshot) "
        "EXECUTE FUNCTION prevent_mutation()"
    ),
}

BASELINE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS one_active_baseline ON baselines (device_id) WHERE active"
)
SQLITE_BASELINE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS one_active_baseline ON baselines (device_id) WHERE active = 1"
)
