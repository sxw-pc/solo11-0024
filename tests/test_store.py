from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from koujing.models import PAYLOAD_SCHEMA_VERSION, migrate_payload
from koujing.reporter import render_json, render_markdown
from koujing.store import Store, StoreError


def _count(db_path: Path, table: str, task_id: str | None = None) -> int:
    connection = sqlite3.connect(str(db_path))
    try:
        if task_id is None:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        else:
            row = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE task_id = ?", (task_id,)
            ).fetchone()
        return int(row[0])
    finally:
        connection.close()


def _task_row(db_path: Path, task_id: str) -> tuple[int, str]:
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT payload_version, payload_json FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        assert row is not None
        return int(row[0]), str(row[1])
    finally:
        connection.close()


def _set_payload(db_path: Path, task_id: str, payload: dict, version: int) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(
            "UPDATE tasks SET payload_json = ?, payload_version = ? WHERE task_id = ?",
            (json.dumps(payload, ensure_ascii=False), version, task_id),
        )
        connection.commit()
    finally:
        connection.close()


def test_save_and_query_task(sales_task, tmp_path: Path) -> None:
    store = Store(tmp_path / "ledger.db")
    payload = store.get_task(sales_task.task_id)
    assert payload is not None
    assert payload["task_id"] == sales_task.task_id
    assert payload["summary"]["over_tolerance_groups"] == 2


def test_all_ledger_tables_are_populated(sales_task, tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    assert _count(db_path, "tasks", sales_task.task_id) == 1
    assert _count(db_path, "profiles_loaded", sales_task.task_id) == 2
    assert _count(db_path, "comparisons", sales_task.task_id) == 1
    assert _count(db_path, "diffs", sales_task.task_id) == 4
    assert _count(db_path, "attributions", sales_task.task_id) == 8


def test_duplicate_task_id_is_idempotent(sales_task, tmp_path: Path) -> None:
    store = Store(tmp_path / "ledger.db")
    store.save_task(sales_task)
    store.save_task(sales_task)
    assert _count(tmp_path / "ledger.db", "tasks", sales_task.task_id) == 1
    assert len(store.list_tasks()) == 1


def test_transaction_rolls_back_on_failure(
    sales_task, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "ledger.db")

    def explode(connection, payload):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(Store, "_insert_attributions", staticmethod(explode))
    failed = replace(sales_task, task_id="kj-rollback")
    with pytest.raises(StoreError) as caught:
        store.save_task(failed)
    assert "回滚" in str(caught.value)
    assert store.get_task("kj-rollback") is None
    assert _count(tmp_path / "ledger.db", "tasks", "kj-rollback") == 0


def test_list_tasks_and_limit(sales_task, tmp_path: Path) -> None:
    store = Store(tmp_path / "ledger.db")
    items = store.list_tasks(limit=10)
    assert len(items) == 1
    assert items[0]["task_id"] == sales_task.task_id
    assert items[0]["profile_count"] == 2
    assert items[0]["tolerance"]["relative"] == 0.1
    with pytest.raises(StoreError):
        store.list_tasks(limit=0)


def test_missing_task_returns_none(tmp_path: Path) -> None:
    store = Store(tmp_path / "empty.db")
    assert store.get_task("kj-missing") is None


def test_saved_payload_is_stamped_with_current_version(sales_task, tmp_path: Path) -> None:
    version, _ = _task_row(tmp_path / "ledger.db", sales_task.task_id)
    assert version == PAYLOAD_SCHEMA_VERSION


def test_get_task_backfills_legacy_payload(sales_task, tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    legacy = sales_task.to_dict()
    assert "refined_signals" not in legacy["summary"]
    _set_payload(db_path, sales_task.task_id, legacy, version=1)
    _, archived_json = _task_row(db_path, sales_task.task_id)

    store = Store(db_path)
    payload = store.get_task(sales_task.task_id)
    assert payload is not None
    assert payload["summary"]["refined_signals"] == {}
    comparison = payload["comparisons"][0]
    assert comparison["summary"]["refined_signals"] == {}
    assert comparison["matched"]
    assert all(item["signals"] == [] for item in comparison["matched"])
    # The backfilled payload renders through every reporter path.
    assert "## 4. 比对结果" in render_markdown(payload)
    rendered = json.loads(render_json(payload))
    assert rendered["comparisons"][0]["summary"]["refined_signals"] == {}
    # Backfill happens on the in-memory copy only: the archived row is untouched.
    version_after, json_after = _task_row(db_path, sales_task.task_id)
    assert version_after == 1
    assert json_after == archived_json
    assert "refined_signals" not in json_after


def test_migrate_payload_does_not_mutate_input(sales_task) -> None:
    legacy = sales_task.to_dict()
    snapshot = json.dumps(legacy, ensure_ascii=False, sort_keys=True)
    migrated = migrate_payload(legacy, 1)
    assert json.dumps(legacy, ensure_ascii=False, sort_keys=True) == snapshot
    assert migrated["comparisons"][0]["summary"]["refined_signals"] == {}
    assert migrated["comparisons"][0]["matched"][0]["signals"] == []


def test_get_task_rejects_newer_payload_version(sales_task, tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    _set_payload(db_path, sales_task.task_id, sales_task.to_dict(), version=99)
    store = Store(db_path)
    with pytest.raises(StoreError) as caught:
        store.get_task(sales_task.task_id)
    assert caught.value.code == "unsupported_payload_version"


def test_get_task_rejects_unmigratable_structure(sales_task, tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    store = Store(db_path)

    broken_summary = sales_task.to_dict()
    del broken_summary["summary"]["matched_groups"]
    _set_payload(db_path, sales_task.task_id, broken_summary, version=1)
    with pytest.raises(StoreError) as caught:
        store.get_task(sales_task.task_id)
    assert caught.value.code == "payload_migration_error"

    broken_shape = sales_task.to_dict()
    broken_shape["comparisons"] = {"unexpected": True}
    _set_payload(db_path, sales_task.task_id, broken_shape, version=1)
    with pytest.raises(StoreError) as caught:
        store.get_task(sales_task.task_id)
    assert caught.value.code == "payload_migration_error"


def test_legacy_database_gains_payload_version_column(sales_task, tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(
            "CREATE TABLE tasks ("
            "task_id TEXT PRIMARY KEY, generated_at TEXT, base_profile TEXT NOT NULL, "
            "profile_count INTEGER NOT NULL, tolerance_abs REAL NOT NULL, "
            "tolerance_rel REAL NOT NULL, summary_json TEXT NOT NULL, "
            "payload_json TEXT NOT NULL)"
        )
        payload = sales_task.to_dict()
        connection.execute(
            "INSERT INTO tasks (task_id, generated_at, base_profile, profile_count, "
            "tolerance_abs, tolerance_rel, summary_json, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["task_id"],
                payload["generated_at"],
                payload["base_profile"],
                2,
                0.0,
                0.1,
                json.dumps(payload["summary"], ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    store = Store(db_path)
    columns = {
        row[1]
        for row in sqlite3.connect(str(db_path)).execute("PRAGMA table_info(tasks)")
    }
    assert "payload_version" in columns
    version, archived_json = _task_row(db_path, sales_task.task_id)
    assert version == 1

    payload = store.get_task(sales_task.task_id)
    assert payload is not None
    assert payload["summary"]["refined_signals"] == {}
    assert render_markdown(payload).startswith("# 口径校准报告")
    # Migration must not rewrite the archived row.
    assert _task_row(db_path, sales_task.task_id) == (1, archived_json)


def test_replay_keeps_single_row_and_relation_counts(sales_task, tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    store = Store(db_path)
    store.save_task(sales_task)
    store.save_task(sales_task)
    assert _count(db_path, "tasks", sales_task.task_id) == 1
    assert _count(db_path, "profiles_loaded", sales_task.task_id) == 2
    assert _count(db_path, "comparisons", sales_task.task_id) == 1
    assert _count(db_path, "diffs", sales_task.task_id) == 4
    assert _count(db_path, "attributions", sales_task.task_id) == 8
    assert len(store.list_tasks()) == 1
