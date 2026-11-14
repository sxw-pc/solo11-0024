"""Transactional SQLite ledger for calibration tasks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import PAYLOAD_SCHEMA_VERSION, PayloadSchemaError, migrate_payload
from .store_schema import SCHEMA


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class StoreError(Exception):
    def __init__(self, message: str, code: str = "store_error"):
        self.message = message
        self.code = code
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class Store:
    """A small repository whose saves are idempotent per deterministic task id."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.executescript(SCHEMA)
                self._migrate(connection)
        except sqlite3.Error as exc:
            raise StoreError(f"无法初始化数据库: {exc}", "database_init_error") from exc
        finally:
            connection.close()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Bring ledger databases created by older versions up to date."""
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        if "payload_version" not in columns:
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN payload_version INTEGER NOT NULL DEFAULT 1"
            )

    def save_task(self, task: TaskResult) -> dict[str, Any]:
        payload = task.to_dict()
        task_id = payload["task_id"]
        tolerance_abs, tolerance_rel = 0.0, 0.0
        if payload["comparisons"]:
            tolerance = payload["comparisons"][0]["tolerance"]
            tolerance_abs, tolerance_rel = tolerance["absolute"], tolerance["relative"]
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                self._insert_task(connection, payload, tolerance_abs, tolerance_rel)
                self._insert_profiles(connection, payload)
                self._insert_comparisons(connection, payload)
                self._insert_diffs(connection, payload)
                self._insert_attributions(connection, payload)
        except Exception as exc:
            raise StoreError(f"任务写入失败，事务已回滚: {exc}") from exc
        finally:
            connection.close()
        return payload

    @staticmethod
    def _insert_task(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
        tolerance_abs: float,
        tolerance_rel: float,
    ) -> None:
        connection.execute(
            "INSERT INTO tasks (task_id, generated_at, base_profile, profile_count, "
            "tolerance_abs, tolerance_rel, summary_json, payload_json, payload_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["task_id"],
                payload["generated_at"],
                payload["base_profile"],
                payload["summary"]["profile_count"],
                tolerance_abs,
                tolerance_rel,
                _json(payload["summary"]),
                _json(payload),
                PAYLOAD_SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _insert_profiles(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        calculations = {item["profile_id"]: item for item in payload["calculations"]}
        for ordinal, profile in enumerate(payload["profiles"]):
            stats = calculations[profile["id"]]["stats"]
            connection.execute(
                "INSERT INTO profiles_loaded (task_id, ordinal, profile_id, name, source, grain, "
                "filter_expr, metric_expr, dimensions_json, exclusions_json, notes, rows_read, "
                "rows_filtered, rows_excluded, rows_aggregated, group_count, unparsable_rows) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["task_id"],
                    ordinal,
                    profile["id"],
                    profile["name"],
                    profile["source"],
                    profile["grain"],
                    profile["filter"],
                    profile["metric"],
                    _json(profile["dimensions"]),
                    _json(profile["exclusions"]),
                    profile["notes"],
                    stats["rows_read"],
                    stats["rows_filtered"],
                    stats["rows_excluded"],
                    stats["rows_aggregated"],
                    stats["groups"],
                    stats["unparsable_rows"],
                ),
            )

    @staticmethod
    def _insert_comparisons(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for item in payload["comparisons"]:
            summary = item["summary"]
            connection.execute(
                "INSERT INTO comparisons (task_id, base_profile, compare_profile, matched_count, "
                "within_count, over_count, only_base_count, only_compare_count, tolerance_abs, "
                "tolerance_rel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["task_id"],
                    item["base_profile"],
                    item["compare_profile"],
                    summary["matched_groups"],
                    summary["within_tolerance_groups"],
                    summary["over_tolerance_groups"],
                    summary["only_base_groups"],
                    summary["only_compare_groups"],
                    item["tolerance"]["absolute"],
                    item["tolerance"]["relative"],
                ),
            )

    @staticmethod
    def _insert_diffs(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for comparison in payload["comparisons"]:
            common = {
                "task_id": payload["task_id"],
                "base_profile": comparison["base_profile"],
                "compare_profile": comparison["compare_profile"],
            }
            for item in comparison["matched"]:
                if item["within_tolerance"]:
                    continue
                connection.execute(
                    "INSERT INTO diffs (task_id, base_profile, compare_profile, group_label, "
                    "group_key_json, base_value, compare_value, diff_value, diff_rate, base_zero, "
                    "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        common["task_id"], common["base_profile"], common["compare_profile"],
                        item["label"], _json(item["key"]), item["base_value"],
                        item["compare_value"], item["diff"], item["diff_rate"],
                        int(item["base_zero"]), "over_tolerance",
                    ),
                )
            for status, side, value_field in (
                ("only_base", "only_base", "value"),
                ("only_compare", "only_compare", "value"),
            ):
                for item in comparison[side]:
                    base_value = item[value_field] if status == "only_base" else None
                    compare_value = item[value_field] if status == "only_compare" else None
                    connection.execute(
                        "INSERT INTO diffs (task_id, base_profile, compare_profile, group_label, "
                        "group_key_json, base_value, compare_value, diff_value, diff_rate, base_zero, "
                        "status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            common["task_id"], common["base_profile"], common["compare_profile"],
                            item["label"], _json(item["key"]), base_value, compare_value,
                            None, None, 0, status,
                        ),
                    )

    @staticmethod
    def _insert_attributions(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for attribution in payload["attributions"]:
            for clue in attribution["clues"]:
                connection.execute(
                    "INSERT INTO attributions (task_id, base_profile, compare_profile, "
                    "group_label, clause, base_clause, compare_clause, explanation, "
                    "affected_groups_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        payload["task_id"],
                        attribution["base_profile"],
                        attribution["compare_profile"],
                        attribution["group_label"],
                        clue["clause"],
                        clue["base"],
                        clue["compare"],
                        clue["explanation"],
                        _json(clue["affected_groups"]),
                    ),
                )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        if not isinstance(task_id, str) or not task_id:
            raise StoreError("task_id 必须是非空字符串", "invalid_task_id")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_json, payload_version FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"查询任务失败: {exc}", "query_error") from exc
        finally:
            connection.close()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise StoreError(f"任务数据损坏: {exc}", "corrupt_task") from exc
        try:
            # Migrate on the in-memory copy only; the archived row is never
            # rewritten, so replaying old tasks stays side-effect free.
            return migrate_payload(payload, row["payload_version"])
        except PayloadSchemaError as exc:
            raise StoreError(exc.message, exc.code) from exc

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise StoreError("limit 必须是 1 到 500 的整数", "invalid_limit")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT task_id, generated_at, base_profile, profile_count, tolerance_abs, "
                "tolerance_rel, summary_json FROM tasks ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"列出任务失败: {exc}", "query_error") from exc
        finally:
            connection.close()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            try:
                summary = json.loads(row["summary_json"])
            except json.JSONDecodeError as exc:
                raise StoreError(f"任务摘要损坏: {exc}", "corrupt_task") from exc
            tasks.append(
                {
                    "task_id": row["task_id"],
                    "generated_at": row["generated_at"],
                    "base_profile": row["base_profile"],
                    "profile_count": row["profile_count"],
                    "tolerance": {
                        "absolute": row["tolerance_abs"],
                        "relative": row["tolerance_rel"],
                    },
                    "summary": summary,
                }
            )
        return tasks
