import structlog
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, EstimationResponse
from app.services.llm_service import generate_text
from app.services.providers import LLMProviderError

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
def estimate(request: EstimationRequest) -> EstimationResponse:
    # 1. El body ya llega validado como EstimationRequest.
    # 2. Se renderizan el system y el user prompt desde las plantillas Jinja2.
    system_prompt, user_message = render_estimation_prompt(
        request, version=settings.prompt_version
    )

    # 3. Se llama al modelo con los dos mensajes separados.
    with structlog.contextvars.bound_contextvars(
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
    ):
        try:
            text = generate_text(system_prompt, user_message)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        except LLMProviderError:
            raise HTTPException(
                status_code=502,
                detail="Error al comunicarse con el proveedor de LLM.",
            )

    # 4. Se devuelve la respuesta con la versión de prompt usada.
    return EstimationResponse(text=text, prompt_version=settings.prompt_version)
