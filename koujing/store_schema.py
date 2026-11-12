"""SQLite DDL for the local calibration ledger."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    generated_at TEXT,
    base_profile TEXT NOT NULL,
    profile_count INTEGER NOT NULL,
    tolerance_abs REAL NOT NULL,
    tolerance_rel REAL NOT NULL,
    summary_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles_loaded (
    task_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    grain TEXT NOT NULL,
    filter_expr TEXT NOT NULL,
    metric_expr TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    exclusions_json TEXT NOT NULL,
    notes TEXT NOT NULL,
    rows_read INTEGER NOT NULL,
    rows_filtered INTEGER NOT NULL,
    rows_excluded INTEGER NOT NULL,
    rows_aggregated INTEGER NOT NULL,
    group_count INTEGER NOT NULL,
    unparsable_rows INTEGER NOT NULL,
    PRIMARY KEY (task_id, ordinal),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS comparisons (
    task_id TEXT NOT NULL,
    base_profile TEXT NOT NULL,
    compare_profile TEXT NOT NULL,
    matched_count INTEGER NOT NULL,
    within_count INTEGER NOT NULL,
    over_count INTEGER NOT NULL,
    only_base_count INTEGER NOT NULL,
    only_compare_count INTEGER NOT NULL,
    tolerance_abs REAL NOT NULL,
    tolerance_rel REAL NOT NULL,
    PRIMARY KEY (task_id, base_profile, compare_profile),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS diffs (
    task_id TEXT NOT NULL,
    base_profile TEXT NOT NULL,
    compare_profile TEXT NOT NULL,
    group_label TEXT NOT NULL,
    group_key_json TEXT NOT NULL,
    base_value REAL,
    compare_value REAL,
    diff_value REAL,
    diff_rate REAL,
    base_zero INTEGER NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (task_id, base_profile, compare_profile, group_label, status),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS attributions (
    task_id TEXT NOT NULL,
    base_profile TEXT NOT NULL,
    compare_profile TEXT NOT NULL,
    group_label TEXT NOT NULL,
    clause TEXT NOT NULL,
    base_clause TEXT NOT NULL,
    compare_clause TEXT NOT NULL,
    explanation TEXT NOT NULL,
    affected_groups_json TEXT NOT NULL,
    PRIMARY KEY (task_id, base_profile, compare_profile, group_label, clause),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
);
"""
