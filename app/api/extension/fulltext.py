"""app/api/extension/fulltext — 全文下载命令循环。

命令循环：扩展主动拉取下一步（GET /next），上报页面观察和文件上传。
状态机在服务端（features/fulltext），扩展无状态。

认证：api_token 由 domain/accounts.api_tokens 存储，扩展在 Authorization 头传 Bearer token。
"""

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
import uuid

from app.shell.deps import db
from domain.accounts import verify_api_token, get_account
from domain.jobs.models import Job

router = APIRouter(prefix="/fulltext", tags=["fulltext"])


class ObservationPayload(BaseModel):
    """扩展上报的页面观察：只有元数据，无 cookie/正文。"""
    url: str
    title: str | None = None
    has_captcha: bool = False
    has_password_field: bool = False
    has_auth_prompt: bool = False


class CommandResponse(BaseModel):
    """下一步命令响应。"""
    op: str  # open_hidden, open_visible, close_tab, fetch, prompt_sso, done, fail
    url: str | None = None
    req_id: str | None = None
    reason: str | None = None
    hint: str | None = None


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
    session: Session = Depends(db),
) -> CommandResponse:
    """获取下一步命令。

    根据 job 状态返回对应的命令原语：
      op=open_hidden: 后台开隐形 tab
      op=fetch: 带 cookie 从 URL 下载
      op=done: 完成
      op=fail: 失败
    """
    job = session.query(Job).filter_by(id=job_id).first()
    if not job or job.account_id != auth["account_id"]:
        raise HTTPException(status_code=404, detail="job not found")

    # 简化版：直接根据 status 返回命令
    # TODO: 完整逻辑应该考虑 reason、重试策略等

    if job.status == "queued":
        return CommandResponse(op="done", reason="job_not_started")
    elif job.status == "resolving":
        # 下发打开隐形 tab 的命令，去访问原始 URL
        return CommandResponse(
            op="open_hidden",
            url=job.source_url,
            req_id=str(uuid.uuid4()),
        )
    elif job.status == "fetching":
        return CommandResponse(
            op="fetch",
            url=job.source_url,
            req_id=str(uuid.uuid4()),
        )
    elif job.status == "uploading":
        return CommandResponse(op="done", reason="waiting_for_upload")
    elif job.status == "success":
        return CommandResponse(op="done")
    elif job.status == "failed":
        return CommandResponse(
            op="fail",
            reason=job.reason or "unknown_error",
        )
    else:
        return CommandResponse(op="done", reason="unknown_status")


@router.post("/observe")
async def record_observation(
    job_id: int,
    payload: ObservationPayload,
    auth: dict = Depends(get_api_account),
    session: Session = Depends(db),
) -> dict:
    """上报页面观察（元数据仅）。

    返回：{ "status": "ok" } 或错误。
    """
    job = session.query(Job).filter_by(id=job_id).first()
    if not job or job.account_id != auth["account_id"]:
        raise HTTPException(status_code=404, detail="job not found")

    # 调 features/fulltext.record_observation
    from features.fulltext.service import record_observation as record_obs

    try:
        record_obs(session, job_id, payload.dict())
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload")
async def record_upload(
    job_id: int,
    file: UploadFile = File(...),
    auth: dict = Depends(get_api_account),
    session: Session = Depends(db),
) -> dict:
    """上报校验过的 PDF 字节。

    返回：{ "status": "success" } 或 { "status": "failed", "reason": "..." }
    """
    job = session.query(Job).filter_by(id=job_id).first()
    if not job or job.account_id != auth["account_id"]:
        raise HTTPException(status_code=404, detail="job not found")

    # 调 features/fulltext.record_upload
    from features.fulltext.service import record_upload as record_up

    try:
        file_bytes = await file.read()
        record_up(session, job_id, file_bytes, file.filename or "unknown.pdf")
        return {"status": "success"}
    except Exception as e:
        return {"status": "failed", "reason": str(e)}


__all__ = ["router"]
