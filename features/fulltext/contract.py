"""features/fulltext 的公开接口：DTO、签名、异常、状态机。"""

from dataclasses import dataclass
from enum import Enum


class FulltextJobStatus(str, Enum):
    """12 态状态机（via domain/jobs.status + reason）。"""
    QUEUED = "queued"
    RESOLVING = "resolving"  # 推导候选
    FETCHING = "fetching"  # 试 direct 或 extension
    CAPTCHA_REQUIRED = "captcha_required"  # 等人点 captcha
    AUTH_REQUIRED = "auth_required"  # 等人登录
    VALIDATING = "validating"  # fileprobe 校验
    UPLOADING = "uploading"  # 上传 blobstore
    SUCCESS = "success"
    FAILED = "failed"


# 状态转移表（由 features/fulltext 传给 domain/jobs.transition）
FULLTEXT_TRANSITIONS = {
    "queued": ["resolving"],
    "resolving": ["fetching", "failed"],
    "fetching": ["captcha_required", "auth_required", "validating", "failed"],
    "captcha_required": ["fetching"],
    "auth_required": ["fetching"],
    "validating": ["uploading", "failed"],
    "uploading": ["success", "failed"],
}


@dataclass
class DownloadProxyConfig:
    """EZproxy 或机构代理配置。"""
    url_template: str  # e.g. "https://proxy.example.edu/login?url={url}"
    enabled: bool = True


class DownloadProxyError(Exception):
    pass


class FulltextJobError(Exception):
    pass
