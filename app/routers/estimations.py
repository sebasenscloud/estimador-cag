from fastapi import APIRouter, HTTPException

from app.schemas import EstimationRequest, EstimationResponse
from app.services.llm_service import generate_estimation
from app.services.providers import LLMProviderError

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
def estimate(request: EstimationRequest) -> EstimationResponse:
    try:
        return generate_estimation(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except LLMProviderError:
        raise HTTPException(
            status_code=502, detail="Error al comunicarse con el proveedor de LLM."
        )
