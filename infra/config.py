"""进程级配置：只读 .env / 环境变量。

设置分三处，不要混：
  - 这里（.env）：部署参数与密钥
  - 各模块 config.yaml：静态参考数据（CSL 样式表、出版商规则等）
  - DB settings_site / settings_account：用户可改的设置，三级回退
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    app_secret_key: str
    database_url: str = "sqlite:///./data/dev.sqlite3"
    blob_root: Path = REPO_ROOT / "data" / "blobs"
    log_level: str = "INFO"
    timezone: str = "UTC"
    allow_self_signup: bool = True

    @field_validator("app_secret_key")
    @classmethod
    def _long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("APP_SECRET_KEY 至少 32 字符")
        return value


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
