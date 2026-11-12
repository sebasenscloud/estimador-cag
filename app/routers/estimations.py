import structlog
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, EstimationResponse, EstimationResult
from app.services.llm_service import (
    InvalidLLMOutputError,
    LLMProviderError,
    generate_structured,
)

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
def estimate(request: EstimationRequest) -> EstimationResponse:
    # 1. El body ya llega validado como EstimationRequest.
    # 2. Se renderizan el system y el user prompt desde las plantillas Jinja2.
    system_prompt, user_message = render_estimation_prompt(
        request, version=settings.prompt_version
    )

    # 3. Se llama al modelo con los dos mensajes separados y se exige que la
    #    respuesta cumpla EstimationResult (Instructor valida y reintenta).
    with structlog.contextvars.bound_contextvars(
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
    ):
        try:
            result, cached = generate_structured(
                system_prompt, user_message, EstimationResult
            )
        except InvalidLLMOutputError as error:
            raise HTTPException(status_code=502, detail=str(error))
        except LLMProviderError:
            raise HTTPException(
                status_code=502,
                detail="Error al comunicarse con el proveedor de LLM.",
            )

    # 4. Se devuelven datos tipados; la presentación queda en manos del cliente.
    return EstimationResponse(
        result=result, prompt_version=settings.prompt_version, cached=cached
    )
