import structlog
from fastapi import APIRouter, HTTPException

from app.schemas import EstimationRequest, EstimationResponse
from app.services import estimation
from app.services.llm_wrapper import InvalidLLMOutputError, LLMProviderError

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
