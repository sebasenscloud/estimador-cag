"""Caso de uso: estimar dentro de una sesión, con adjuntos opcionales.

1. Recuperar la sesión            → SessionNotFoundError si no existe
2. Validar y procesar adjuntos     → AttachmentError (política exception). El
                                      router elige por documento: pypdf, PyMuPDF,
                                      python-docx o multimodal (camino A)
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

from app.anchors import detect_anchor
from app.attachments import (
    Attachment,
    ProcessedAttachment,
    build_user_content,
    get_attachment_processor,
    validate_attachments,
)
from app.compression import apply_compression
from app.config import settings
from app.guardrails.output import enforce_scope_response
from app.prompts.loader import render_estimation_prompt
from app.schemas import (
    AttachmentInfo,
    EstimationRequest,
    EstimationResult,
    SessionDebugInfo,
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

    validate_attachments(attachments)
    router = get_attachment_processor()
    processed: list[ProcessedAttachment] = []
    try:
        for attachment in attachments:
            item = router.process(attachment)
            processed.append(item)
            logger.info(
                "attachment_routed",
                filename=item.filename,
                strategy=item.strategy,
                tool=item.tool,
                reason=item.reason,
                extracted_chars=item.extracted_chars,
            )
        return _run_turn(session, request, processed)
    finally:
        # Los archivos subidos a la Files API se borran al terminar el turno,
        # haya salido bien o no: su contenido ya quedó en project_metadata.
        _cleanup(processed)


def _run_turn(
    session, request: EstimationRequest, processed: list[ProcessedAttachment]
) -> SessionEstimationResponse:
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

    estimation_call = generate_structured_from_messages(messages, EstimationResult)
    result = enforce_scope_response(estimation_call.result)

    previous_metadata = session.metadata
    session.metadata, extraction_metrics = extract_project_metadata(
        session.metadata, user_content, result
    )
    llm_calls = [estimation_call.metrics]
    if extraction_metrics is not None:
        llm_calls.append(extraction_metrics)

    # Anclas: solo se mira el mensaje del usuario; el assistant entra por arrastre.
    anchor, anchor_metrics = detect_anchor(request.description)
    if anchor_metrics is not None:
        llm_calls.append(anchor_metrics)

    # Al historial va el texto del turno, no el de los adjuntos: su contenido ya
    # quedó destilado en project_metadata y así no infla los turnos siguientes.
    session.history.append(
        _history_user_message(user_message, processed),
        result.model_dump_json(),
        anchor_rules=anchor.matched_rules,
    )
    # append() no recorta: la compresión (y su posible llamada al LLM) es explícita.
    summary_metrics = apply_compression(session.history)
    if summary_metrics is not None:
        llm_calls.append(summary_metrics)
    session.total_turns += 1
    if anchor.is_anchor:
        logger.info(
            "anchor_detected",
            session_id=session.session_id,
            turn=session.total_turns,
            matched_rules=anchor.matched_rules,
            reason=anchor.reason or None,
            mode=settings.anchor_detection_mode,
        )
    session.last_llm_calls = llm_calls
    metadata_changes = session.record_metadata_change(
        previous_metadata,
        turn=session.total_turns,
        sources=["mensaje del usuario", *(item.filename for item in processed)],
    )

    logger.info(
        "session_estimation_completed",
        session_id=session.session_id,
        attachments=len(processed),
        attachment_chars=sum(item.extracted_chars for item in processed),
        out_of_scope=result.is_out_of_scope,
        history_turns=session.history.turn_count,
        turn_cost_usd=_total_cost(llm_calls),
    )
    return SessionEstimationResponse(
        session_id=session.session_id,
        history_turns=session.history.turn_count,
        result=result,
        project_metadata=session.metadata,
        prompt_version=settings.prompt_version,
        attachments=[_info(item) for item in processed],
        llm_calls=llm_calls,
        metadata_changes=metadata_changes,
        anchor_rules=anchor.matched_rules,
        anchor_reason=anchor.reason or None,
    )


def get_debug_info(session_id: str) -> SessionDebugInfo:
    """Estado interno de la sesión para observar cómo se gestiona la memoria."""
    session = session_store.get(session_id)
    return SessionDebugInfo(
        session_id=session.session_id,
        created_at=session.created_at,
        total_turns=session.total_turns,
        window_turns=session.history.turn_count,
        max_turns=session.history.max_turns,
        message_count=len(session.history),
        anchors_count=session.history.anchor_count,
        summary_chars=len(session.history.summary),
        last_resolved_tier=session.last_resolved_tier,
        last_tier_rule=session.last_tier_rule,
        project_metadata=session.metadata,
        metadata_log=session.metadata_log,
        last_llm_calls=session.last_llm_calls,
    )


def _total_cost(calls: list) -> float | None:
    costs = [call.cost_usd for call in calls]
    return None if None in costs else round(sum(costs), 6)


def _cleanup(processed: list[ProcessedAttachment]) -> None:
    for item in processed:
        if item.cleanup is None:
            continue
        try:
            item.cleanup()
        except Exception as error:  # noqa: BLE001 - la limpieza es best-effort
            logger.warning(
                "attachment_cleanup_failed", filename=item.filename, error=str(error)
            )


def _history_user_message(
    user_message: str, processed: list[ProcessedAttachment]
) -> str:
    if not processed:
        return user_message
    names = ", ".join(item.filename for item in processed)
    return f"{user_message}\n\n[Adjuntos de este turno: {names}]"


def _info(item: ProcessedAttachment) -> AttachmentInfo:
    return AttachmentInfo(
        filename=item.filename,
        strategy=item.strategy,
        tool=item.tool,
        reason=item.reason,
        extracted_chars=item.extracted_chars,
        truncated=item.truncated,
        warning=" ".join(item.warnings) or None,
    )
