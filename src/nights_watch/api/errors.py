"""422s that say where and why, without echoing the payload back (it may not even encode)."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def validation_failed(request: Request, error: RequestValidationError) -> JSONResponse:
    detail = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in error.errors()]
    return JSONResponse({"detail": detail}, status_code=422)
