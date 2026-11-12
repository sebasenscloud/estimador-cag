"""Caso de uso: estimar dentro de una sesión, con adjuntos opcionales.

1. Recuperar la sesión            → SessionNotFoundError si no existe
2. Validar y procesar adjuntos     → AttachmentError (política exception)
3. Render del prompt               → mismas plantillas que la estimación simple
4. Armar messages                  → el user es una lista de bloques: el prompt y
                                      a continuación los bloques de cada adjunto
5. LLM + Instructor                → fix · retry
6. Output guardrail                → filter
7. Actualizar project_metadata     → extractor LLM (si falla, conserva la ficha)

No usa la caché exact-match: dentro de una sesión la respuesta va a depender
del historial y de la memoria del proyecto (pasos siguientes).
"""

import structlog

from app.attachments import (
    Attachment,
    ProcessedAttachment,
    build_user_content,
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
from app.services.llm_wrapper import generate_structured_from_messages
from app.services.metadata_extractor import extract_project_metadata
from app.sessions import session_store

logger = structlog.get_logger()


def estimate_in_session(
    session_id: str, request: EstimationRequest, attachments: list[Attachment]
) -> SessionEstimationResponse:
    session = session_store.get(session_id)

    processor = get_attachment_processor()
    validate_attachments(attachments, processor)
    processed = [processor.process(attachment) for attachment in attachments]

    # El system prompt lleva los hechos conocidos de la sesión en <project_metadata>.
    system_prompt, user_message = render_estimation_prompt(
        request,
        version=settings.prompt_version,
        project_metadata=session.metadata,
    )
    user_content = build_user_content(user_message, processed)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    result = generate_structured_from_messages(messages, EstimationResult)
    result = enforce_scope_response(result)

    session.metadata = extract_project_metadata(session.metadata, user_content, result)

    logger.info(
        "session_estimation_completed",
        session_id=session.session_id,
        attachments=len(processed),
        attachment_chars=sum(item.extracted_chars for item in processed),
        out_of_scope=result.is_out_of_scope,
    )
    return SessionEstimationResponse(
        session_id=session.session_id,
        result=result,
        project_metadata=session.metadata,
        prompt_version=settings.prompt_version,
        attachments=[_info(item, processor.strategy) for item in processed],
    )


def _info(item: ProcessedAttachment, strategy: str) -> AttachmentInfo:
    return AttachmentInfo(
        filename=item.filename,
        strategy=strategy,
        extracted_chars=item.extracted_chars,
        truncated=item.truncated,
        warning=" ".join(item.warnings) or None,
    )
