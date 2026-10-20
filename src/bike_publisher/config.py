from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    db_path: Path
    browser_profile: Path
    browser_channel: str
    city: str
    radius_min: int
    radius_max: int
    default_delay_minutes: int
    action_delay_seconds: float
    between_posts_seconds: int
    headless: bool
    account_slot: str = ''
    expected_account_id: str = ''

    @property
    def worker_dir(self) -> Path:
        return self.data_dir / 'workers' / self.account_slot if self.account_slot else self.data_dir

    @classmethod
    def from_root(cls, root: Path | None = None) -> "Settings":
        project_root = (root or Path(__file__).resolve().parents[2]).resolve()
        load_dotenv(project_root / ".env")
        data = Path(os.getenv("BIKEBOT_DATA_DIR", "data"))
        if not data.is_absolute():
            data = project_root / data
        # Keep Chrome's writable session inside the ignored project data folder.
        # The Codex sandbox can read, but cannot write, to %LOCALAPPDATA%.
        default_browser = data / "browser_profile"
        browser_value = os.getenv("BIKEBOT_BROWSER_PROFILE", "").strip()
        browser = Path(browser_value) if browser_value else default_browser
        if not browser.is_absolute():
            browser = project_root / browser
        data.mkdir(parents=True, exist_ok=True)
        return cls(
            root=project_root,
            data_dir=data,
            db_path=data / "app.db",
            browser_profile=browser,
            browser_channel=os.getenv("BIKEBOT_BROWSER_CHANNEL", "chrome").strip(),
            city=os.getenv("BIKEBOT_CITY", "Poznań"),
            radius_min=int(os.getenv("BIKEBOT_RADIUS_MIN", "15")),
            radius_max=int(os.getenv("BIKEBOT_RADIUS_MAX", "200")),
            default_delay_minutes=int(os.getenv("BIKEBOT_DEFAULT_DELAY_MINUTES", "90")),
            action_delay_seconds=max(
                0.5, float(os.getenv("BIKEBOT_ACTION_DELAY_SECONDS", "2.5"))
            ),
            between_posts_seconds=max(
                30, int(os.getenv("BIKEBOT_BETWEEN_POSTS_SECONDS", "60"))
            ),
            headless=os.getenv("BIKEBOT_HEADLESS", "true").lower() == "true",
            account_slot=os.getenv('BIKEBOT_ACCOUNT_SLOT', '').strip(),
        )


def ensure_dirs(settings: Settings) -> None:
    for path in (settings.data_dir, settings.data_dir / "photos", settings.data_dir / "incoming", settings.data_dir / "logs", settings.data_dir / "backups", settings.browser_profile):
        path.mkdir(parents=True, exist_ok=True)
