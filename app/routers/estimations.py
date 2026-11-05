from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.llm_service import generate_estimation
from app.services.providers import LLMProviderError

router = APIRouter(prefix="/api/v1", tags=["estimations"])


class EstimationRequest(BaseModel):
    transcription: str = Field(
        min_length=20, description="La transcripcion de la reunion"
    )


class EstimationResponse(BaseModel):
    estimation: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    datetime_created: datetime


@router.post("/estimate", response_model=EstimationResponse)
def estimate(request: EstimationRequest) -> EstimationResponse:
    try:
        result = generate_estimation(request.transcription)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except LLMProviderError:
        raise HTTPException(
            status_code=502, detail="Error al comunicarse con el proveedor de LLM."
        )

    return EstimationResponse(
        estimation=result["estimation"],
        model=result["model"],
        provider=result["provider"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cache_hit=result["cache_hit"],
        datetime_created=datetime.now(UTC),
    )
