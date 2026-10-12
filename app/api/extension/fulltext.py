"""app/api/extension/fulltext — 全文下载命令循环。

命令循环：扩展主动拉取下一步（GET /next），上报页面观察和文件上传。
状态机在服务端（features/fulltext），扩展无状态。

接口：
  GET  /next    → 下一步命令（openxxx / fetch / prompt / done / fail）
  POST /observe → 上报页面元数据（url / title / has_captcha / ...）
  POST /upload  → 上报取到并校验过的 PDF 字节
"""

from fastapi import APIRouter

router = APIRouter(prefix="/fulltext", tags=["fulltext"])

# TODO: 实现 /next /observe /upload 三个端点
