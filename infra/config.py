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
    # `caps.secrets.derive_key` 的 salt 参数——和 app_secret_key 一起派生出
    # 具体用途（如 domain/connections 的凭据加密）的 Fernet 密钥。单独一个
    # 字段而不是复用 app_secret_key 本身，因为 app_secret_key 只是人能管理
    # 的口令，不保证是合法的 44 字符 base64 Fernet 密钥。
    app_secret_salt: str
    database_url: str = "sqlite:///./data/dev.sqlite3"
    blob_root: Path = REPO_ROOT / "data" / "blobs"
    log_level: str = "INFO"
    # JSON Lines，和 blob_root 一样常驻磁盘而非可选——features/logs_viewer
    # 的读/清/导三个操作都要有一个具体文件可以指向，不做『不落盘就什么都
    # 读不到』这种默认行为。
    log_file: Path = REPO_ROOT / "data" / "app.log"
    timezone: str = "UTC"
    allow_self_signup: bool = True

    @field_validator("app_secret_key")
    @classmethod
    def _long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("APP_SECRET_KEY 至少 32 字符")
        return value

    @field_validator("app_secret_salt")
    @classmethod
    def _salt_long_enough(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("APP_SECRET_SALT 至少 8 字符")
        return value


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
