"""Application settings. Everything comes from environment variables (or .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # When true, Terraform and the CloudStack API are replaced by fakes.
    dry_run: bool = False

    cloudstack_api_url: str = ""
    cloudstack_api_key: SecretStr = SecretStr("")
    cloudstack_secret_key: SecretStr = SecretStr("")

    data_dir: Path = REPO_ROOT / "data"
    terraform_dir: Path = REPO_ROOT / "terraform"
    terraform_bin: str = "terraform"
    terraform_timeout_seconds: int = 1800

    max_concurrent_jobs: int = 2
    workdir_retention_hours: float = 24
    plan_ttl_minutes: float = 60  # an unconfirmed plan expires after this
    shutdown_grace_seconds: float = 300  # how long a shutdown waits for running applies
    vm_action_timeout_seconds: float = 30
    cleanup_interval_seconds: int = 600
    catalog_ttl_seconds: int = 300
    fake_step_delay_seconds: float = 0.25

    min_disk_gb: int = 10
    max_disk_gb: int = 500
    max_instances: int = 5

    cors_origins: str = "http://localhost:5173"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def workdirs_dir(self) -> Path:
        return self.data_dir / "workdirs"

    @property
    def state_dir(self) -> Path:
        # Terraform state lives here, outside the per-job workdir, so that
        # cleaning up workdirs never orphans real infrastructure.
        return self.data_dir / "state"

    def secrets(self) -> list[str]:
        return [
            s
            for s in (
                self.cloudstack_api_key.get_secret_value(),
                self.cloudstack_secret_key.get_secret_value(),
            )
            if s
        ]

    def check_live_config(self) -> None:
        if self.dry_run:
            return
        missing = [
            name
            for name, value in (
                ("CLOUDSTACK_API_URL", self.cloudstack_api_url),
                ("CLOUDSTACK_API_KEY", self.cloudstack_api_key.get_secret_value()),
                ("CLOUDSTACK_SECRET_KEY", self.cloudstack_secret_key.get_secret_value()),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing environment variables: " + ", ".join(missing) + ". Set them or use DRY_RUN=true."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
