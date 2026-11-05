import time

import structlog
from fastapi import FastAPI, Request

from app.config import settings
from app.logging_config import configure_logging
from app.routers import estimations

configure_logging(settings.log_level, json_logs=settings.app_env == "production")
logger = structlog.get_logger()

app = FastAPI(title="Estimador CAG")

app.include_router(estimations.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.perf_counter() - start) * 1000),
    )
    return response


@app.get("/")
def health():
    return {"status": "ok"}
