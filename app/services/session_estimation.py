"""Caso de uso: estimar dentro de una sesión, con adjuntos opcionales.

1. Recuperar la sesión            → SessionNotFoundError si no existe
2. Validar y procesar adjuntos     → AttachmentError (política exception)
3. Concatenar texto a la transcripción con "--- attachment: nombre ---"
4. Render del prompt               → mismas plantillas que la estimación simple
5. LLM + Instructor                → fix · retry
6. Output guardrail                → filter

No usa la caché exact-match: dentro de una sesión la respuesta va a depender
del historial y de la memoria del proyecto (pasos siguientes).
"""

import structlog

from app.attachments import (
    Attachment,
    ProcessedAttachment,
    apply_attachments,
    get_attachment_processor,
    validate_attachments,
)
from app.config import settings
from app.guardrails.output import enforce_scope_response
from app.prompts.loader import render_estimation_prompt
from app.schemas import (
    AttachmentInfo,
    EstimationRequest,
    EstimationResult,
    SessionEstimationResponse,
)
from app.services.llm_wrapper import generate_structured
from app.sessions import session_store

logger = structlog.get_logger()


def estimate_in_session(
    session_id: str, request: EstimationRequest, attachments: list[Attachment]
) -> SessionEstimationResponse:
    session = session_store.get(session_id)

    processor = get_attachment_processor()
    validate_attachments(attachments, processor)
    processed = [processor.process(attachment) for attachment in attachments]

    # El límite de 2000 caracteres aplica a lo que escribe el usuario (ya
    # validado); el texto de los adjuntos se suma después, para el prompt.
    full_transcript = apply_attachments(request.description, processed)
    prompt_request = request.model_copy(update={"description": full_transcript})
    system_prompt, user_message = render_estimation_prompt(
        prompt_request, version=settings.prompt_version
    )

    result = generate_structured(system_prompt, user_message, EstimationResult)
    result = enforce_scope_response(result)

    logger.info(
        "session_estimation_completed",
        session_id=session.session_id,
        attachments=len(processed),
        attachment_chars=sum(len(item.text) for item in processed),
        out_of_scope=result.is_out_of_scope,
    )
    return SessionEstimationResponse(
        session_id=session.session_id,
        result=result,
        prompt_version=settings.prompt_version,
        attachments=[_info(item, processor.strategy) for item in processed],
    )


def _info(item: ProcessedAttachment, strategy: str) -> AttachmentInfo:
    return AttachmentInfo(
        filename=item.filename,
        strategy=strategy,
        extracted_chars=len(item.text),
        truncated=item.truncated,
        warning=" ".join(item.warnings) or None,
    )
