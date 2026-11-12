from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from koujing.config import (  # noqa: E402
    DEFAULT_DATASET_DIR,
    DEFAULT_PROFILE_DIR,
    DEFAULT_TEMPLATE_DIR,
    Settings,
)
from koujing.loader import load_profiles  # noqa: E402
from koujing.models import (  # noqa: E402
    CalcStats,
    CalculationResult,
    GroupValue,
    Number,
    Profile,
)
from koujing.runner import run_calibration  # noqa: E402
from koujing.server import create_app  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def fixture_dirs() -> tuple[Path, Path]:
    return DEFAULT_PROFILE_DIR, DEFAULT_DATASET_DIR


@pytest.fixture(scope="session")
def sales_profiles() -> tuple[Profile, Profile]:
    profiles = load_profiles(DEFAULT_PROFILE_DIR, DEFAULT_DATASET_DIR)
    by_id = {item.id: item for item in profiles}
    return by_id["profile_sales_a"], by_id["profile_sales_b"]


@pytest.fixture
def calc_factory():
    def make(
        profile_id: str,
        groups: Mapping[tuple[str, ...], Number],
        dimensions: tuple[str, ...] = ("region",),
        stats: CalcStats | None = None,
    ) -> CalculationResult:
        ordered = tuple(
            GroupValue(key=key, value=value)
            for key, value in sorted(groups.items(), key=lambda item: item[0])
        )
        actual_stats = stats or CalcStats(
            rows_read=len(groups),
            rows_filtered=len(groups),
            rows_excluded=0,
            rows_aggregated=len(groups),
            groups=len(groups),
            unparsable_rows=0,
        )
        return CalculationResult(
            profile_id=profile_id,
            profile_name=profile_id,
            grain="order",
            dimensions=dimensions,
            stats=actual_stats,
            groups=ordered,
        )

    return make


@pytest.fixture
def profile_factory():
    def make(profile_id: str = "profile_base", **changes: object) -> Profile:
        values = {
            "id": profile_id,
            "name": profile_id,
            "source": "sample.csv",
            "grain": "order",
            "filter": "status == 'paid'",
            "metric": "sum(amount)",
            "dimensions": ("region",),
            "exclusions": (),
            "notes": "",
        }
        values.update(changes)
        return Profile(**values)  # type: ignore[arg-type]

    return make


@pytest.fixture
def sales_task(tmp_path: Path):
    outcome = run_calibration(
        ["profile_sales_a", "profile_sales_b"],
        DEFAULT_PROFILE_DIR,
        DEFAULT_DATASET_DIR,
        tmp_path / "ledger.db",
        write_reports_enabled=False,
    )
    return outcome.task


@pytest.fixture
def api_client(tmp_path: Path):
    settings = Settings(
        profile_dir=DEFAULT_PROFILE_DIR,
        dataset_dir=DEFAULT_DATASET_DIR,
        db_path=tmp_path / "api.db",
        report_dir=tmp_path / "reports",
        template_dir=DEFAULT_TEMPLATE_DIR,
    )
    app = create_app(settings)
    app.config.update(TESTING=True)
    return app.test_client()
