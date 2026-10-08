"""请求中间件：request_id + 异常处理走同一条路径。

规避清单 #3：不要用 @app.exception_handler(Exception)。Starlette 会把它提到
最外层中间件之上，于是本中间件设的 X-Request-Id 在 500 响应上会丢失。
成功和失败必须是同一条出口。
"""

import logging
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from infra.errors import AppError
from infra.logging import set_request_id

log = logging.getLogger(__name__)
REQUEST_ID_HEADER = "X-Request-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        set_request_id(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except AppError as error:
            log.warning("app_error", extra={"extra_fields": {"code": error.code}})
            response = JSONResponse(
                {"error": error.code, "message": error.message},
                status_code=error.status_code,
            )
        except Exception:
            log.exception("unhandled")
            response = JSONResponse(
                {"error": "internal_error", "request_id": request_id},
                status_code=500,
            )
        finally:
            set_request_id(None)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
