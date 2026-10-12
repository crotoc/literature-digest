"""app/api/extension — 浏览器扩展作为第二客户端的 API 端点。

扩展不是 feature，是独立客户端。装配层在这里横跨多个 feature：
- fulltext.py：全文下载命令循环（/next /observe /upload）
- dist.py：扩展源码分发（/download）
"""

from fastapi import APIRouter

from app.api.extension.fulltext import router as fulltext_router
from app.api.extension.dist import router as dist_router

router = APIRouter(prefix="/api/extension", tags=["extension"])
router.include_router(fulltext_router)
router.include_router(dist_router)

__all__ = ["router"]
