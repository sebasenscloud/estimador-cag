from datetime import datetime, timezone

import anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.llm_service import generate_estimation

router = APIRouter(prefix="/api/v1", tags=["estimations"])

class EstimationRequest(BaseModel):
    transcription: str = Field(
        min_length=20,
        description="La transcripcion de la reunion"
    )

class EstimationResponse(BaseModel):
    estimation :str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    datetime_created: datetime

@router.post("/estimate", response_model=EstimationResponse)
def estimate(request:EstimationRequest)->EstimationResponse:
    result = generate_estimation(request.transcription)
    return EstimationResponse(
        estimation=result["estimation"],
        model=settings.llm_model,
        provider=settings.llm_provider,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        datetime_created=datetime.now(timezone.utc),
    )