from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from app.attachments import Attachment, AttachmentError
from app.guardrails.input import InputGuardrailViolation, guardrail_detail
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
    SessionDebugInfo,
    SessionEstimationResponse,
)
from app.services import session_estimation
from app.services.llm_wrapper import InvalidLLMOutputError, LLMProviderError
from app.sessions import SessionNotFoundError, session_store
from app.tiers import Tier

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class SessionCreated(BaseModel):
    session_id: str


@router.post("", response_model=SessionCreated, status_code=201)
def create_session() -> SessionCreated:
    """Crea una sesión vacía. El cliente reenvía el session_id en cada petición
    posterior para reutilizar el historial y la memoria del proyecto."""
    session = session_store.create()
    logger.info("session_created", session_id=session.session_id)
    return SessionCreated(session_id=session.session_id)


@router.get("/{session_id}", response_model=SessionDebugInfo)
def get_session(session_id: str) -> SessionDebugInfo:
    """Endpoint de debug: expone cómo se está gestionando la memoria de la sesión
    (ventana, anclas, resumen, tier) sin mostrar el contenido de los mensajes."""
    try:
        return session_estimation.get_debug_info(session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))


# multipart/form-data: JSON y archivos no pueden ir en el mismo body, así que
# los campos tipados llegan como Form (FastAPI valida los enums igual) y los
# archivos como UploadFile. Los dos endpoints comparten el mismo contrato.
Transcript = Annotated[str, Form()]
ProjectTypeForm = Annotated[ProjectType, Form()]
DetailLevelForm = Annotated[DetailLevel, Form()]
OutputFormatForm = Annotated[OutputFormat, Form()]
Attachments = Annotated[list[UploadFile] | None, File()]
# Override humano del tier: si viene, gana sobre el resolver (explicit_override).
TierForm = Annotated[Tier | None, Form()]


@router.post("/{session_id}/estimate", response_model=SessionEstimationResponse)
async def estimate_in_session(
    session_id: str,
    transcript: Transcript,
    project_type: ProjectTypeForm,
    detail_level: DetailLevelForm = DetailLevel.MEDIUM,
    output_format: OutputFormatForm = OutputFormat.PHASES_TABLE,
    attachments: Attachments = None,
    tier: TierForm = None,
) -> SessionEstimationResponse:
    """Modo actor: una llamada de estimación (más metadata)."""
    return await _estimate_turn(
        session_id,
        transcript,
        project_type,
        detail_level,
        output_format,
        attachments,
        tier,
        acb=False,
    )


@router.post("/{session_id}/estimate-acb", response_model=SessionEstimationResponse)
async def estimate_in_session_acb(
    session_id: str,
    transcript: Transcript,
    project_type: ProjectTypeForm,
    detail_level: DetailLevelForm = DetailLevel.MEDIUM,
    output_format: OutputFormatForm = OutputFormat.PHASES_TABLE,
    attachments: Attachments = None,
    tier: TierForm = None,
) -> SessionEstimationResponse:
    """Actor-Critic-Boss: mismo contrato; la respuesta trae `acb` con la traza
    de rondas (veredicto, confianza e issues del crítico, decisión del Boss)."""
    return await _estimate_turn(
        session_id,
        transcript,
        project_type,
        detail_level,
        output_format,
        attachments,
        tier,
        acb=True,
    )


async def _estimate_turn(
    session_id: str,
    transcript: str,
    project_type: ProjectType,
    detail_level: DetailLevel,
    output_format: OutputFormat,
    attachments: list[UploadFile] | None,
    tier: Tier | None,
    acb: bool,
) -> SessionEstimationResponse:
    try:
        request = EstimationRequest(
            description=transcript,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False))

    files = [
        Attachment(filename=upload.filename or "adjunto", data=await upload.read())
        for upload in attachments or []
    ]

    with structlog.contextvars.bound_contextvars(
        session_id=session_id, project_type=project_type.value, acb=acb
    ):
        try:
            # La llamada al LLM es bloqueante: se ejecuta en un hilo para no frenar
            # el event loop (el endpoint es async para poder leer los archivos).
            return await run_in_threadpool(
                session_estimation.estimate_in_session,
                session_id,
                request,
                files,
                tier,
                acb,
            )
        except InputGuardrailViolation as error:
            raise HTTPException(status_code=400, detail=guardrail_detail(error))
        except SessionNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except AttachmentError as error:
            raise HTTPException(status_code=422, detail=str(error))
        except InvalidLLMOutputError as error:
            raise HTTPException(status_code=502, detail=str(error))
        except LLMProviderError:
            raise HTTPException(
                status_code=502,
                detail="Error al comunicarse con el proveedor de LLM.",
            )
