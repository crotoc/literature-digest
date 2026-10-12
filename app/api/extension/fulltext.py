"""app/api/extension/fulltext — 全文下载命令循环。

命令循环：扩展主动拉取下一步（GET /next），上报页面观察和文件上传。
状态机在服务端（features/fulltext），扩展无状态。

认证：api_token 由 domain/accounts.api_tokens 存储，扩展在 Authorization 头传 Bearer token。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.shell.deps import db
from domain.accounts import verify_api_token, get_account

router = APIRouter(prefix="/fulltext", tags=["fulltext"])


class ObservationPayload(BaseModel):
    """扩展上报的页面观察：只有元数据，无 cookie/正文。"""
    url: str
    title: str | None = None
    has_captcha: bool = False
    has_password_field: bool = False
    has_auth_prompt: bool = False


class UploadPayload(BaseModel):
    """扩展上报的文件上传（bytes 在 multipart/form-data 里）。"""
    job_id: int
    source_url: str


async def get_api_account(request: Request, session: Session = Depends(db)) -> dict:
    """从 Authorization: Bearer <token> 头认证，返回 account info。"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid Authorization")

    token = auth_header[7:]
    try:
        api_token_dto = verify_api_token(session, token)
        account = get_account(session, api_token_dto.account_id)
        if not account:
            raise HTTPException(status_code=401, detail="invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")

    return {"account_id": account.id, "account": account}


@router.get("/next")
async def next_command(
    job_id: int,
    auth: dict = Depends(get_api_account),
) -> dict:
    """获取下一步命令。

    返回示例（不同状态）：
      { "op": "open_hidden", "url": "...", "req_id": "uuid" }
      { "op": "fetch", "url": "...", "req_id": "uuid" }
      { "op": "prompt_sso", "url": "..." }
      { "op": "done" }
      { "op": "fail", "reason": "no_candidate" }
    """
    # TODO: 调 features/fulltext.next_command(session, job_id, auth["account_id"])
    return {"op": "done", "message": "not implemented yet"}


@router.post("/observe")
async def record_observation(
    job_id: int,
    payload: ObservationPayload,
    auth: dict = Depends(get_api_account),
) -> dict:
    """上报页面观察（元数据仅）。

    返回：{ "status": "ok" } 或错误。
    """
    # TODO: 调 features/fulltext.record_observation(session, job_id, payload)
    return {"status": "ok", "message": "not implemented yet"}


@router.post("/upload")
async def record_upload(
    job_id: int,
    auth: dict = Depends(get_api_account),
) -> dict:
    """上报校验过的 PDF 字节。

    返回：{ "status": "success" } 或 { "status": "failed", "reason": "..." }
    """
    # TODO: 调 features/fulltext.record_upload(session, job_id, file_bytes)
    return {"status": "ok", "message": "not implemented yet"}


__all__ = ["router"]
