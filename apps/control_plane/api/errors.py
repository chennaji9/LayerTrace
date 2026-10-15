"""统一错误体与 API 异常映射。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from packages.contracts.schemas import ContractLimitError, ErrorOut


class ApiError(Exception):
    """带错误码的 API 异常。"""

    def __init__(self, status_code: int, code: str, message: str, field: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field
        super().__init__(message)


def error_response(status_code: int, code: str, message: str, field: str | None = None) -> JSONResponse:
    body = ErrorOut(code=code, message=message, field=field).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=body, headers={"Cache-Control": "no-store"})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.field)

    @app.exception_handler(ContractLimitError)
    async def _limit(_: Request, exc: ContractLimitError) -> JSONResponse:
        # 超限整批拒绝（413），错误体可定位限制类型，不截断
        return error_response(413, f"payload_too_large.{exc.limit_type}", exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        # Pydantic 校验中的 ContractLimitError 也要映射为 413
        if isinstance(first.get("ctx", {}).get("error"), ContractLimitError):
            inner: ContractLimitError = first["ctx"]["error"]
            return error_response(413, f"payload_too_large.{inner.limit_type}", inner.detail)
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = first.get("msg", "请求不符合契约")
        return error_response(400, "bad_request", msg, loc or None)

    @app.exception_handler(ValidationError)
    async def _pydantic(_: Request, exc: ValidationError) -> JSONResponse:
        return error_response(400, "bad_request", str(exc))

    @app.exception_handler(404)
    async def _not_found(_: Request, __) -> JSONResponse:
        return error_response(404, "not_found", "对象不存在或不在认证范围内")

    @app.exception_handler(500)
    async def _internal(_: Request, __) -> JSONResponse:
        return error_response(500, "internal", "内部错误")
