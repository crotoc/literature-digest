"""app/api/extension/dist — 扩展源码分发。

接口：
  GET /download → ZIP 格式的扩展源码（Content-Type: application/zip）
"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from io import BytesIO
from sqlalchemy.orm import Session

from app.shell.deps import db
from features.extension_dist.service import package_extension

router = APIRouter(prefix="/dist", tags=["dist"])


@router.get("/download")
async def download_extension(session: Session = Depends(db)) -> StreamingResponse:
    """下载扩展源码 ZIP。

    返回：application/zip 格式的可加载扩展包。
    """
    dto = package_extension()

    return StreamingResponse(
        iter([dto.content]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{dto.filename}\""}
    )


__all__ = ["router"]
