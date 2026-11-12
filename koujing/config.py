"""Global paths and defaults for the local calibration workbench."""

from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BASE_DIR = PACKAGE_DIR.parent
DEFAULT_PROFILE_DIR = BASE_DIR / "fixtures" / "profiles"
DEFAULT_DATASET_DIR = BASE_DIR / "fixtures" / "datasets"
DEFAULT_DB_PATH = BASE_DIR / "koujing.db"
DEFAULT_REPORT_DIR = BASE_DIR / "report"
DEFAULT_TEMPLATE_DIR = PACKAGE_DIR / "templates"

DEFAULT_TOLERANCE_ABS = 0.0
DEFAULT_TOLERANCE_REL = 0.1
DEFAULT_TASK_LIMIT = 50
MAX_TASK_LIMIT = 200
MAX_PROFILES_PER_RUN = 20
TASK_ID_PREFIX = "kj-"


@dataclass(frozen=True)
class Settings:
    """Resolved local locations used by both the CLI and HTTP service."""

    profile_dir: Path = DEFAULT_PROFILE_DIR
    dataset_dir: Path = DEFAULT_DATASET_DIR
    db_path: Path = DEFAULT_DB_PATH
    report_dir: Path = DEFAULT_REPORT_DIR
    template_dir: Path = DEFAULT_TEMPLATE_DIR

    def with_overrides(self, **changes: Path) -> "Settings":
        values = {
            "profile_dir": self.profile_dir,
            "dataset_dir": self.dataset_dir,
            "db_path": self.db_path,
            "report_dir": self.report_dir,
            "template_dir": self.template_dir,
        }
        values.update(changes)
        return Settings(**values)


def default_settings() -> Settings:
    return Settings()
