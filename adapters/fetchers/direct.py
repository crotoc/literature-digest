"""无身份同步 PDF 下载器：直接取公开 PDF，无 SSO/proxy。

只走 caps/httpfetch（SSRF 防护、重试、429 退避）。
返回 Done(bytes) 或 Failed(reason)，永不返回 Dispatched（异步不适用这里）。
"""

from dataclasses import dataclass
from typing import Union

from caps.fileprobe import FileProbeError, check_upload
from caps.httpfetch import HttpFetchError, fetch


@dataclass
class Done:
    bytes: bytes


@dataclass
class Failed:
    reason: str


FetchResult = Union[Done, Failed]


def fetch_pdf(
    candidate_url: str,
    max_bytes: int = 50_000_000,
    allowed_media_types: list[str] | None = None,
    transport_mock=None,
) -> FetchResult:
    """
    尝试从 candidate_url 同步下载 PDF。

    流程：
    1. 用 caps/httpfetch 取字节（带 SSRF 防护、429 退避）
    2. 用 caps/fileprobe 校验 PDF magic + 大小 + 类型
    3. 返回 Done(bytes) 或 Failed(reason)

    无身份访问，仅适用于 OA 链接和公开 PDF。
    """
    if allowed_media_types is None:
        allowed_media_types = ["application/pdf"]

    try:
        # 1. 抓字节
        if transport_mock:
            result = fetch(candidate_url, transport=transport_mock, timeout=30)
        else:
            result = fetch(candidate_url, timeout=30)

        # 2. 校验（magic number + 大小 + MIME）
        try:
            check_upload(
                result.content,
                max_bytes=max_bytes,
                allowed_media_types=allowed_media_types,
                reject_damaged=True,
            )
        except FileProbeError as e:
            return Failed(reason=f"probe_failed: {str(e)}")

        return Done(bytes=result.content)

    except HttpFetchError as e:
        return Failed(reason=f"fetch_failed: {str(e)}")
    except Exception as e:
        return Failed(reason=f"unexpected_error: {str(e)}")
