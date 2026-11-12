from collections.abc import Iterator

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas import EstimationRequest, EstimationResponse, EstimationStreamEvent
from app.services import estimation
from app.services.llm_wrapper import InvalidLLMOutputError, LLMProviderError

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
def estimate(request: EstimationRequest) -> EstimationResponse:
    """Capa HTTP: valida el body, delega en el caso de uso y traduce errores."""
    with structlog.contextvars.bound_contextvars(
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
    ):
        try:
            return estimation.estimate(request)
        except InvalidLLMOutputError as error:
            raise HTTPException(status_code=502, detail=str(error))
        except LLMProviderError:
            raise HTTPException(
                status_code=502,
                detail="Error al comunicarse con el proveedor de LLM.",
            )


@router.post("/estimate/stream")
def estimate_stream(request: EstimationRequest) -> StreamingResponse:
    """Mismo caso de uso, entregado como NDJSON: una línea JSON por evento.

    Como el status 200 se envía al empezar el stream, los errores posteriores
    llegan como un evento `{"type": "error"}` en lugar de un código HTTP.
    """
    log_context = {
        "project_type": request.project_type.value,
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
    }

    def ndjson() -> Iterator[str]:
        with structlog.contextvars.bound_contextvars(**log_context):
            try:
                for event in estimation.stream_estimate(request):
                    yield event.model_dump_json() + "\n"
            except InvalidLLMOutputError as error:
                yield _error_line(str(error))
            except LLMProviderError:
                yield _error_line("Error al comunicarse con el proveedor de LLM.")
            except Exception:
                # El stream ya devolvió 200: un error inesperado tiene que llegar
                # como evento, o el cliente vería un stream cortado sin explicación.
                logger.exception("stream_unexpected_error")
                yield _error_line("Error interno del servicio IA.")

    return StreamingResponse(ndjson(), media_type="application/x-ndjson")


def _error_line(detail: str) -> str:
    return EstimationStreamEvent(type="error", detail=detail).model_dump_json() + "\n"
