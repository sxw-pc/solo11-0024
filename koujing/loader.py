"""YAML profile loading with strict, path-addressable validation."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from .expression import ExpressionError, validate_expression
from .models import Profile

GRAINS = frozenset({"order", "user", "event"})
REQUIRED_FIELDS = (
    "id",
    "name",
    "source",
    "grain",
    "filter",
    "metric",
    "dimensions",
    "exclusions",
    "notes",
)
OPTIONAL_FIELDS = ("decimal_mode",)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ProfileError(Exception):
    """A profile declaration problem tied to a concrete YAML path."""

    def __init__(self, path: str, message: str, code: str = "invalid_profile"):
        self.path = path
        self.message = message
        self.code = code
        super().__init__(f"{path}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def _fail(path: Path | str, field: str, message: str, code: str = "invalid_profile") -> ProfileError:
    return ProfileError(f"{path}:{field}" if field else str(path), message, code)


def _require_text(data: dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise _fail(path, key, "必须是字符串")
    if key != "notes" and not value.strip():
        raise _fail(path, key, "不能为空")
    return value


def _string_list(value: Any, path: Path, field: str, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(path, field, "必须是字符串列表")
    if not value and not allow_empty:
        raise _fail(path, field, "至少需要一个元素")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise _fail(path, f"{field}[{index}]", "必须是非空字符串")
        if item in result:
            raise _fail(path, f"{field}[{index}]", f"重复的元素 {item!r}")
        result.append(item)
    return tuple(result)


def _dataset_columns(dataset_dir: Path, source: str, path: Path) -> tuple[str, ...]:
    candidate = Path(source)
    if (
        not source
        or candidate.name != source
        or "/" in source
        or "\\" in source
        or source in {".", ".."}
        or candidate.suffix.lower() != ".csv"
    ):
        raise _fail(path, "source", "只允许 fixtures/datasets 下的 CSV 文件名")
    dataset_path = dataset_dir / source
    if not dataset_path.is_file():
        raise _fail(path, "source", f"数据集不存在: {source}")
    try:
        with dataset_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames
    except OSError as exc:
        raise _fail(path, "source", f"无法读取数据集: {exc}") from exc
    if not columns:
        raise _fail(path, "source", "数据集缺少表头")
    normalized = tuple(name.strip() for name in columns if name and name.strip())
    if len(normalized) != len(set(normalized)):
        raise _fail(path, "source", "数据集表头存在重复列名")
    return normalized


def _validate_expression(
    text: str,
    columns: Sequence[str],
    path: Path,
    field: str,
    *,
    allow_aggregates: bool,
    require_aggregate: bool = False,
) -> None:
    try:
        validate_expression(
            text,
            columns,
            allow_aggregates=allow_aggregates,
            require_aggregate=require_aggregate,
        )
    except ExpressionError as exc:
        raise _fail(path, field, f"表达式错误: {exc}") from exc


def load_profile_file(path: Path, dataset_dir: Path) -> Profile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _fail(path, "yaml", f"YAML 解析失败: {exc}") from exc
    except OSError as exc:
        raise _fail(path, "", f"无法读取文件: {exc}") from exc
    if not isinstance(raw, dict):
        raise _fail(path, "yaml", "顶层必须是映射")
    unknown = sorted(set(raw) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise _fail(path, unknown[0], "未知字段")
    for key in REQUIRED_FIELDS:
        if key not in raw:
            raise _fail(path, key, "缺少必需字段")

    profile_id = _require_text(raw, "id", path)
    if not _ID_PATTERN.fullmatch(profile_id):
        raise _fail(path, "id", "只允许字母、数字、下划线和中划线")
    name = _require_text(raw, "name", path)
    source = _require_text(raw, "source", path)
    grain = _require_text(raw, "grain", path)
    if grain not in GRAINS:
        raise _fail(path, "grain", f"必须是 {', '.join(sorted(GRAINS))} 之一")
    filter_text = _require_text(raw, "filter", path)
    metric_text = _require_text(raw, "metric", path)
    dimensions = _string_list(raw["dimensions"], path, "dimensions", allow_empty=False)
    exclusions = _string_list(raw["exclusions"], path, "exclusions", allow_empty=True)
    notes = _require_text(raw, "notes", path)
    decimal_mode = raw.get("decimal_mode", False)
    if not isinstance(decimal_mode, bool):
        raise _fail(path, "decimal_mode", "必须是布尔值")
    columns = _dataset_columns(dataset_dir, source, path)

    missing_dimensions = [item for item in dimensions if item not in columns]
    if missing_dimensions:
        raise _fail(path, "dimensions", f"数据集中不存在列: {', '.join(missing_dimensions)}")
    _validate_expression(filter_text, columns, path, "filter", allow_aggregates=False)
    for index, exclusion in enumerate(exclusions):
        _validate_expression(
            exclusion, columns, path, f"exclusions[{index}]", allow_aggregates=False
        )
    _validate_expression(
        metric_text,
        columns,
        path,
        "metric",
        allow_aggregates=True,
        require_aggregate=True,
    )
    return Profile(
        id=profile_id,
        name=name,
        source=source,
        grain=grain,
        filter=filter_text,
        metric=metric_text,
        dimensions=dimensions,
        exclusions=exclusions,
        notes=notes,
        decimal_mode=decimal_mode,
    )


def load_profiles(
    profile_dir: Path | str,
    dataset_dir: Path | str,
    profile_ids: Iterable[str] | None = None,
) -> list[Profile]:
    profile_dir, dataset_dir = Path(profile_dir), Path(dataset_dir)
    if not profile_dir.is_dir():
        raise ProfileError(str(profile_dir), "口径目录不存在", "profile_dir_not_found")
    paths = sorted(profile_dir.glob("*.yaml"), key=lambda item: item.name)
    if not paths:
        raise ProfileError(str(profile_dir), "口径目录中没有 YAML 文件", "no_profiles")
    by_id: dict[str, Profile] = {}
    origins: dict[str, str] = {}
    loaded: list[Profile] = []
    for path in paths:
        profile = load_profile_file(path, dataset_dir)
        if profile.id in by_id:
            raise ProfileError(
                str(path), f"口径 id {profile.id!r} 与 {origins[profile.id]} 重复", "duplicate_profile"
            )
        by_id[profile.id] = profile
        origins[profile.id] = path.name
        loaded.append(profile)
    if profile_ids is None:
        return loaded
    requested = list(profile_ids)
    if not requested:
        raise ProfileError("profiles", "至少选择一个口径", "no_profiles_selected")
    if len(set(requested)) != len(requested):
        raise ProfileError("profiles", "口径 id 不能重复", "duplicate_selection")
    missing = [item for item in requested if item not in by_id]
    if missing:
        raise ProfileError(
            "profiles", f"本地口径不存在: {', '.join(missing)}", "profile_not_found"
        )
    return [by_id[item] for item in requested]
