from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from koujing.loader import ProfileError, load_profile_file, load_profiles

PROFILE = {
    "id": "profile_sample",
    "name": "样例口径",
    "source": "sample.csv",
    "grain": "order",
    "filter": "status == 'paid'",
    "metric": "sum(amount)",
    "dimensions": ["region"],
    "exclusions": ["channel == 'test'"],
    "notes": "合成样例。",
}


@pytest.fixture
def loader_env(tmp_path: Path):
    profile_dir = tmp_path / "profiles"
    dataset_dir = tmp_path / "datasets"
    profile_dir.mkdir()
    dataset_dir.mkdir()
    (dataset_dir / "sample.csv").write_text(
        "id,region,amount,status,channel\n"
        "1,华东,100,paid,web\n"
        "2,华北,200,paid,app\n",
        encoding="utf-8",
    )

    def write_yaml(name: str, data: dict[str, Any], raw: str | None = None) -> Path:
        path = profile_dir / name
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        else:
            path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        return path

    return profile_dir, dataset_dir, write_yaml


def test_load_valid_profile(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("sample.yaml", PROFILE)
    profile = load_profile_file(path, dataset_dir)
    assert profile.id == "profile_sample"
    assert profile.dimensions == ("region",)
    assert profile.exclusions == ("channel == 'test'",)
    assert profile.to_dict()["metric"] == "sum(amount)"


def test_load_all_profiles_sorted(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    first = dict(PROFILE, id="profile_z")
    second = dict(PROFILE, id="profile_a")
    write_yaml("z.yaml", first)
    write_yaml("a.yaml", second)
    loaded = load_profiles(loader_env[0], dataset_dir)
    assert [item.id for item in loaded] == ["profile_a", "profile_z"]


def test_missing_field_has_path(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    data = dict(PROFILE)
    data.pop("notes")
    path = write_yaml("bad.yaml", data)
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "bad.yaml:notes" in str(caught.value)


def test_wrong_type_has_path(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, dimensions="region"))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "bad.yaml:dimensions" in str(caught.value)


def test_invalid_metric_expression(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, metric="sum(amount"))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "bad.yaml:metric" in str(caught.value)
    assert "表达式错误" in str(caught.value)


def test_unknown_column_is_reported(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, filter="unknown_col == 1"))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "bad.yaml:filter" in str(caught.value)
    assert "unknown_col" in str(caught.value)


def test_missing_dataset(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, source="absent.csv"))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "bad.yaml:source" in str(caught.value)


def test_source_path_traversal_rejected(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, source="../sample.csv"))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert caught.value.code == "invalid_profile"
    assert "CSV 文件名" in str(caught.value)


def test_duplicate_profile_id(loader_env) -> None:
    profile_dir, dataset_dir, write_yaml = loader_env
    write_yaml("one.yaml", PROFILE)
    write_yaml("two.yaml", dict(PROFILE, name="另一个名称"))
    with pytest.raises(ProfileError) as caught:
        load_profiles(profile_dir, dataset_dir)
    assert caught.value.code == "duplicate_profile"


def test_selection_preserves_requested_order(fixture_dirs) -> None:
    profile_dir, dataset_dir = fixture_dirs
    loaded = load_profiles(
        profile_dir,
        dataset_dir,
        ["profile_sales_b", "profile_sales_a"],
    )
    assert [item.id for item in loaded] == ["profile_sales_b", "profile_sales_a"]


def test_missing_selected_profile(fixture_dirs) -> None:
    profile_dir, dataset_dir = fixture_dirs
    with pytest.raises(ProfileError) as caught:
        load_profiles(profile_dir, dataset_dir, ["profile_sales_a", "does_not_exist"])
    assert caught.value.code == "profile_not_found"


def test_metric_without_aggregate_rejected(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, metric="amount"))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "aggregate_required" in str(caught.value)


def test_aggregate_in_filter_rejected(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, filter="sum(amount) > 1"))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "aggregate_not_allowed" in str(caught.value)


def test_unknown_field_rejected(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", dict(PROFILE, surprise=True))
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert "bad.yaml:surprise" in str(caught.value)


def test_malformed_yaml(loader_env) -> None:
    _, dataset_dir, write_yaml = loader_env
    path = write_yaml("bad.yaml", PROFILE, raw="id: [unterminated\n")
    with pytest.raises(ProfileError) as caught:
        load_profile_file(path, dataset_dir)
    assert caught.value.code == "invalid_profile"
    assert "yaml" in str(caught.value)
