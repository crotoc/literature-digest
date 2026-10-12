"""app/api/extension/dist — 扩展源码分发。

接口：
  GET /download → ZIP 格式的扩展源码（Content-Type: application/zip）
"""

from fastapi import APIRouter

router = APIRouter(prefix="/dist", tags=["dist"])

# TODO: 实现 /download 端点，调 features/extension_dist.package_extension()
