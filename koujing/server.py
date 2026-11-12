"""Local-only Flask service for the calibration workbench."""

from __future__ import annotations

import argparse
import json
from typing import Any

from flask import Flask, render_template, request

from .calculator import CalculationError
from .comparator import ComparisonError
from .config import (
    DEFAULT_TASK_LIMIT,
    DEFAULT_TOLERANCE_ABS,
    DEFAULT_TOLERANCE_REL,
    MAX_TASK_LIMIT,
    Settings,
    default_settings,
)
from .loader import ProfileError
from .runner import RunnerError, run_calibration
from .store import Store, StoreError


def _json_response(payload: Any, status: int = 200):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return Flask.response_class(body, status=status, mimetype="application/json")


def _error(code: str, message: str, status: int):
    return _json_response({"error": {"code": code, "message": message}}, status=status)


def create_app(settings: Settings | None = None, store: Store | None = None) -> Flask:
    resolved = settings or default_settings()
    app = Flask(__name__, template_folder=str(resolved.template_dir))
    app.config["KOUJING_SETTINGS"] = resolved
    ledger = store or Store(resolved.db_path)
    app.extensions["koujing_store"] = ledger

    @app.get("/")
    def index():
        profiles = sorted(path.stem for path in resolved.profile_dir.glob("*.yaml"))
        return render_template(
            "index.html",
            profile_count=len(profiles),
            profile_ids=profiles,
        )

    @app.get("/tasks")
    def tasks():
        raw_limit = request.args.get("limit", str(DEFAULT_TASK_LIMIT))
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return _error("invalid_limit", "limit 必须是整数", 400)
        if not 1 <= limit <= MAX_TASK_LIMIT:
            return _error("invalid_limit", f"limit 必须在 1 到 {MAX_TASK_LIMIT} 之间", 400)
        try:
            items = ledger.list_tasks(limit=limit)
        except StoreError as exc:
            return _error(exc.code, exc.message, 500)
        return _json_response({"count": len(items), "tasks": items})

    @app.get("/tasks/<task_id>")
    def task_detail(task_id: str):
        try:
            payload = ledger.get_task(task_id)
        except StoreError as exc:
            return _error(exc.code, exc.message, 400)
        if payload is None:
            return _error("task_not_found", f"任务不存在: {task_id}", 404)
        return _json_response(payload)

    @app.post("/run")
    def run():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error("invalid_json", "请求体必须是 JSON 对象", 400)
        allowed = {"profiles", "tolerance_abs", "tolerance_rel", "write_reports"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            return _error("unknown_field", f"不支持的字段: {', '.join(unknown)}", 400)
        profile_ids = body.get("profiles")
        if not isinstance(profile_ids, list) or not all(
            isinstance(item, str) and item for item in profile_ids
        ):
            return _error("invalid_profiles", "profiles 必须是非空字符串列表", 400)
        if len(profile_ids) < 2:
            return _error("too_few_profiles", "至少需要两个口径", 400)
        tolerances = {}
        defaults = {
            "tolerance_abs": DEFAULT_TOLERANCE_ABS,
            "tolerance_rel": DEFAULT_TOLERANCE_REL,
        }
        for name in ("tolerance_abs", "tolerance_rel"):
            value = body.get(name, defaults[name])
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                return _error("invalid_tolerance", f"{name} 必须是非负数", 400)
            tolerances[name] = float(value)
        write_reports_enabled = body.get("write_reports", True)
        if not isinstance(write_reports_enabled, bool):
            return _error("invalid_write_reports", "write_reports 必须是布尔值", 400)
        try:
            outcome = run_calibration(
                profile_ids=profile_ids,
                profile_dir=resolved.profile_dir,
                dataset_dir=resolved.dataset_dir,
                db_path=resolved.db_path,
                report_dir=resolved.report_dir if write_reports_enabled else None,
                tolerance_abs=tolerances["tolerance_abs"],
                tolerance_rel=tolerances["tolerance_rel"],
                write_reports_enabled=write_reports_enabled,
            )
        except (RunnerError, ProfileError, ComparisonError, CalculationError, StoreError) as exc:
            code = getattr(exc, "code", "run_failed")
            return _error(code, str(exc), 400)
        return _json_response(
            {
                "task_id": outcome.task.task_id,
                "task": outcome.task.to_dict(),
                "reports": [str(path) for path in outcome.report_paths],
            }
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动本地口径校准台")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port 必须在 1 到 65535 之间")
    app = create_app()
    print(f"口径校准台已启动: http://127.0.0.1:{args.port}/")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
