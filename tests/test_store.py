from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

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
