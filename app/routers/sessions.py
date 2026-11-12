from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from app.attachments import Attachment, AttachmentError
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
    SessionEstimationResponse,
)
from app.services import session_estimation
from app.services.llm_wrapper import InvalidLLMOutputError, LLMProviderError
from app.sessions import SessionNotFoundError, session_store

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


@router.post("/{session_id}/estimate", response_model=SessionEstimationResponse)
async def estimate_in_session(
    session_id: str,
    # multipart/form-data: JSON y archivos no pueden ir en el mismo body, así que
    # los campos tipados llegan como Form (FastAPI valida los enums igual) y los
    # archivos como UploadFile.
    transcript: Annotated[str, Form()],
    project_type: Annotated[ProjectType, Form()],
    detail_level: Annotated[DetailLevel, Form()] = DetailLevel.MEDIUM,
    output_format: Annotated[OutputFormat, Form()] = OutputFormat.PHASES_TABLE,
    attachments: Annotated[list[UploadFile] | None, File()] = None,
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
        session_id=session_id, project_type=project_type.value
    ):
        try:
            # La llamada al LLM es bloqueante: se ejecuta en un hilo para no frenar
            # el event loop (el endpoint es async para poder leer los archivos).
            return await run_in_threadpool(
                session_estimation.estimate_in_session, session_id, request, files
            )
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
