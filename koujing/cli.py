"""Command line interface for local calibration runs and the SQLite ledger."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .calculator import CalculationError
from .comparator import ComparisonError
from .config import (
    DEFAULT_DATASET_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_PROFILE_DIR,
    DEFAULT_REPORT_DIR,
    DEFAULT_TOLERANCE_ABS,
    DEFAULT_TOLERANCE_REL,
)
from .loader import ProfileError
from .reporter import render_json, render_markdown
from .runner import RunnerError, regenerate_reports, run_calibration
from .store import Store, StoreError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m koujing.cli",
        description="本地口径校准台：口径计算、交叉比对、差异归因与报告归档。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行一次校准")
    run_parser.add_argument("--profiles", action="append", required=True, help="口径 id，可重复")
    run_parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    run_parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    run_parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_DIR)
    run_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    run_parser.add_argument("--tolerance-abs", type=float, default=DEFAULT_TOLERANCE_ABS)
    run_parser.add_argument("--tolerance-rel", type=float, default=DEFAULT_TOLERANCE_REL)
    run_parser.add_argument(
        "--timestamp", action="store_true", help="写入生成时间（默认关闭以保证字节级可复现）"
    )

    list_parser = subparsers.add_parser("list", help="列出最近任务")
    list_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    list_parser.add_argument("--limit", type=int, default=20)

    show_parser = subparsers.add_parser("show", help="查看任务详情或重新生成报告")
    show_parser.add_argument("task_id")
    show_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    show_parser.add_argument("--format", choices=("json", "markdown"), default="json")
    show_parser.add_argument("--out", type=Path, help="指定后按 task_id 重新生成 Markdown 与 JSON")
    return parser


def _run(args: argparse.Namespace) -> int:
    generated_at = None
    if args.timestamp:
        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    run = run_calibration(
        profile_ids=args.profiles,
        profile_dir=args.profile_dir,
        dataset_dir=args.dataset_dir,
        db_path=args.db,
        report_dir=args.out,
        tolerance_abs=args.tolerance_abs,
        tolerance_rel=args.tolerance_rel,
        generated_at=generated_at,
    )
    summary = run.task.summary()
    print(f"task_id: {run.task.task_id}")
    print(
        "summary: "
        f"profiles={summary['profile_count']} "
        f"matched={summary['matched_groups']} "
        f"over={summary['over_tolerance_groups']} "
        f"only_base={summary['only_base_groups']} "
        f"only_compare={summary['only_compare_groups']}"
    )
    for path in run.report_paths:
        print(f"report: {path}")
    return 0


def _list(args: argparse.Namespace) -> int:
    tasks = Store(args.db).list_tasks(limit=args.limit)
    if not tasks:
        print("暂无任务")
        return 0
    print("task_id\tgenerated_at\tprofiles\tstatus")
    for task in tasks:
        print(
            f"{task['task_id']}\t{task['generated_at'] or '-'}\t"
            f"{task['profile_count']}\t{task['summary'].get('status', '-')}"
        )
    return 0


def _show(args: argparse.Namespace) -> int:
    store = Store(args.db)
    payload = store.get_task(args.task_id)
    if payload is None:
        print(f"任务不存在: {args.task_id}", file=sys.stderr)
        return 1
    if args.format == "markdown":
        sys.stdout.write(render_markdown(payload))
    else:
        sys.stdout.write(render_json(payload))
    if args.out is not None:
        paths = regenerate_reports(args.task_id, args.db, args.out)
        for path in paths:
            print(f"regenerated: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "list":
            return _list(args)
        return _show(args)
    except (
        RunnerError,
        ProfileError,
        ComparisonError,
        CalculationError,
        StoreError,
        ValueError,
        OSError,
    ) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
