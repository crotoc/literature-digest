"""全项目异常基类。

各模块在自己 contract.py 里派生，不要让 app 层去 except 具体的 ORM/HTTP 异常。
"""


class AppError(Exception):
    """可预期的业务错误，会被渲染成 4xx。"""

    status_code = 400
    code = "app_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
