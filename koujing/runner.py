"""Shared orchestration for CLI, HTTP service, and tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .attributor import attribute_comparison, build_suggestions
from .calculator import calculate_profile
from .comparator import ComparisonError, compare_calculations
from .config import (
    DEFAULT_TOLERANCE_ABS,
    DEFAULT_TOLERANCE_REL,
    MAX_PROFILES_PER_RUN,
    TASK_ID_PREFIX,
)
from .loader import load_profiles
from .models import ComparisonResult, TaskResult
from .reporter import write_reports
from .store import Store


class RunnerError(Exception):
    def __init__(self, message: str, code: str = "runner_error"):
        self.message = message
        self.code = code
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class CalibrationRun:
    task: TaskResult
    report_paths: tuple[Path, ...]


def _digest(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise RunnerError(f"无法读取数据集 {path.name}: {exc}", "dataset_read_error") from exc


def build_task_id(
    profiles: Sequence[Any],
    dataset_dir: Path,
    tolerance_abs: float,
    tolerance_rel: float,
) -> str:
    sources = sorted({profile.source for profile in profiles})
    seed = {
        "profiles": [profile.to_dict() for profile in profiles],
        "datasets": {source: _digest(dataset_dir / source) for source in sources},
        "tolerance": {"absolute": tolerance_abs, "relative": tolerance_rel},
    }
    # Decimal aggregation changes results, so a profile opting in must not share
    # a task id with the same declaration evaluated in float mode.  Leaving the
    # key out while nobody opts in preserves existing task ids.
    decimal_profiles = [profile.id for profile in profiles if profile.decimal_mode]
    if decimal_profiles:
        seed["decimal_mode"] = decimal_profiles
    encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return TASK_ID_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def _validate_ids(profile_ids: Iterable[str]) -> list[str]:
    if isinstance(profile_ids, str):
        raise RunnerError("profiles 必须是口径 id 的序列", "invalid_profile_id")
    try:
        values = list(profile_ids)
    except TypeError as exc:
        raise RunnerError("profiles 必须是口径 id 的序列", "invalid_profile_id") from exc
    if len(values) < 2:
        raise RunnerError("至少需要两个口径才能校准", "too_few_profiles")
    if len(values) > MAX_PROFILES_PER_RUN:
        raise RunnerError(f"一次最多比对 {MAX_PROFILES_PER_RUN} 个口径", "too_many_profiles")
    if any(not isinstance(item, str) or not item for item in values):
        raise RunnerError("口径 id 必须是非空字符串", "invalid_profile_id")
    if len(set(values)) != len(values):
        raise RunnerError("口径 id 不能重复", "duplicate_profile")
    return values


def run_calibration(
    profile_ids: Iterable[str],
    profile_dir: Path | str,
    dataset_dir: Path | str,
    db_path: Path | str,
    report_dir: Path | str | None = None,
    tolerance_abs: float = DEFAULT_TOLERANCE_ABS,
    tolerance_rel: float = DEFAULT_TOLERANCE_REL,
    write_reports_enabled: bool = True,
    generated_at: str | None = None,
) -> CalibrationRun:
    ids = _validate_ids(profile_ids)
    if generated_at is not None and not isinstance(generated_at, str):
        raise RunnerError("generated_at 必须是字符串或 null", "invalid_generated_at")
    profile_dir, dataset_dir = Path(profile_dir), Path(dataset_dir)
    profiles = load_profiles(profile_dir, dataset_dir, ids)
    calculations = tuple(calculate_profile(profile, dataset_dir) for profile in profiles)
    comparisons: list[ComparisonResult] = []
    for calculation in calculations[1:]:
        try:
            comparisons.append(
                compare_calculations(
                    calculations[0],
                    calculation,
                    tolerance_abs=tolerance_abs,
                    tolerance_rel=tolerance_rel,
                )
            )
        except ComparisonError as exc:
            raise RunnerError(str(exc), exc.code) from exc

    profile_map = {profile.id: profile for profile in profiles}
    attributions = []
    for other, comparison in zip(calculations[1:], comparisons):
        attributions.extend(
            attribute_comparison(profile_map[profiles[0].id], profile_map[other.profile_id], comparison)
        )
    suggestions = build_suggestions(comparisons, attributions)
    task = TaskResult(
        task_id=build_task_id(profiles, dataset_dir, float(tolerance_abs), float(tolerance_rel)),
        base_profile=profiles[0].id,
        generated_at=generated_at,
        profiles=tuple(profiles),
        calculations=calculations,
        comparisons=tuple(comparisons),
        attributions=tuple(attributions),
        suggestions=tuple(suggestions),
    )
    Store(db_path).save_task(task)
    paths: tuple[Path, ...] = ()
    if write_reports_enabled:
        if report_dir is None:
            raise RunnerError("启用报告时必须提供输出目录", "missing_report_dir")
        paths = write_reports(task, report_dir)
    return CalibrationRun(task=task, report_paths=paths)


def regenerate_reports(
    task_id: str,
    db_path: Path | str,
    out_dir: Path | str,
    formats: Iterable[str] = ("json", "md"),
) -> tuple[Path, ...]:
    payload = Store(db_path).get_task(task_id)
    if payload is None:
        raise RunnerError(f"任务不存在: {task_id}", "task_not_found")
    return write_reports(payload, out_dir, formats)
