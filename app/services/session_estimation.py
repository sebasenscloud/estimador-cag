"""Caso de uso: estimar dentro de una sesión, con adjuntos opcionales.

1. Recuperar la sesión            → SessionNotFoundError si no existe
2. Validar y procesar adjuntos     → AttachmentError (política exception)
3. Armar messages                  → history.to_messages_list(): system regenerado
                                      con la metadata actual + turnos de la ventana,
                                      y al final el user actual (prompt + adjuntos)
4. LLM + Instructor                → fix · retry
5. Output guardrail                → filter
6. Actualizar project_metadata     → extractor LLM (si falla, conserva la ficha)
7. Guardar el turno en el historial → solo si todo salió bien

No usa la caché exact-match: dentro de una sesión la respuesta depende del
historial y de la memoria del proyecto, no solo del pedido actual.
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
from app.sessions import ProjectMetadata, session_store

logger = structlog.get_logger()


def estimate_in_session(
    session_id: str, request: EstimationRequest, attachments: list[Attachment]
) -> SessionEstimationResponse:
    session = session_store.get(session_id)

    processor = get_attachment_processor()
    validate_attachments(attachments, processor)
    processed = [processor.process(attachment) for attachment in attachments]

    def build_system_prompt(metadata: ProjectMetadata) -> str:
        system_prompt, _ = render_estimation_prompt(
            request, version=settings.prompt_version, project_metadata=metadata
        )
        return system_prompt

    _, user_message = render_estimation_prompt(request, version=settings.prompt_version)
    user_content = build_user_content(user_message, processed)
    messages = [
        *session.history.to_messages_list(session.metadata, build_system_prompt),
        {"role": "user", "content": user_content},
    ]

    result = generate_structured_from_messages(messages, EstimationResult)
    result = enforce_scope_response(result)

    session.metadata = extract_project_metadata(session.metadata, user_content, result)

    # Al historial va el texto del turno, no el de los adjuntos: su contenido ya
    # quedó destilado en project_metadata y así no infla los turnos siguientes.
    session.history.add_turn(
        _history_user_message(user_message, processed), result.model_dump_json()
    )

    logger.info(
        "session_estimation_completed",
        session_id=session.session_id,
        attachments=len(processed),
        attachment_chars=sum(item.extracted_chars for item in processed),
        out_of_scope=result.is_out_of_scope,
        history_turns=session.history.turn_count,
    )
    return SessionEstimationResponse(
        session_id=session.session_id,
        history_turns=session.history.turn_count,
        result=result,
        project_metadata=session.metadata,
        prompt_version=settings.prompt_version,
        attachments=[_info(item, processor.strategy) for item in processed],
    )


def _history_user_message(
    user_message: str, processed: list[ProcessedAttachment]
) -> str:
    if not processed:
        return user_message
    names = ", ".join(item.filename for item in processed)
    return f"{user_message}\n\n[Adjuntos de este turno: {names}]"


def _info(item: ProcessedAttachment, strategy: str) -> AttachmentInfo:
    return AttachmentInfo(
        filename=item.filename,
        strategy=strategy,
        extracted_chars=item.extracted_chars,
        truncated=item.truncated,
        warning=" ".join(item.warnings) or None,
    )
