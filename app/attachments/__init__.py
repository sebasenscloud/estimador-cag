"""Procesamiento de adjuntos (PDF y Word) con los dos caminos.

- Camino A, multimodal (`multimodal.py`): el PDF va al LLM, inline en base64 o
  por la Files API (ATTACHMENT_MULTIMODAL_MODE).
- Camino B, extracción local (`extractors.py`): pypdf, PyMuPDF o python-docx.
- El router (`router.py`) decide documento por documento según
  ATTACHMENT_STRATEGY (auto, local_text o multimodal).

Cada adjunto se convierte en bloques de contenido (`content_parts`) en el
formato de LiteLLM/OpenAI, y el servicio los agrega al mensaje `user` sin
saber de qué camino vienen.
"""

from app.attachments.analysis import PdfProfile, analyze_pdf
from app.attachments.base import (
    SUPPORTED_EXTENSIONS,
    Attachment,
    AttachmentError,
    ProcessedAttachment,
)
from app.attachments.router import AttachmentRouter, Decision, decide_pdf
from app.config import settings

__all__ = [
    "Attachment",
    "AttachmentError",
    "AttachmentRouter",
    "Decision",
    "PdfProfile",
    "ProcessedAttachment",
    "analyze_pdf",
    "build_user_content",
    "decide_pdf",
    "get_attachment_processor",
    "validate_attachments",
]

STRATEGIES = ("auto", "local_text", "multimodal")
MULTIMODAL_MODES = ("inline", "files_api")


def get_attachment_processor() -> AttachmentRouter:
    if settings.attachment_strategy not in STRATEGIES:
        raise ValueError(
            f"ATTACHMENT_STRATEGY desconocida: '{settings.attachment_strategy}'. "
            f"Valores soportados: {', '.join(STRATEGIES)}."
        )
    if settings.attachment_multimodal_mode not in MULTIMODAL_MODES:
        raise ValueError(
            f"ATTACHMENT_MULTIMODAL_MODE desconocido: "
            f"'{settings.attachment_multimodal_mode}'. "
            f"Valores soportados: {', '.join(MULTIMODAL_MODES)}."
        )
    return AttachmentRouter(
        strategy=settings.attachment_strategy,
        max_chars=settings.attachment_max_chars,
        model=settings.llm_model,
    )


def validate_attachments(attachments: list[Attachment]) -> None:
    """Valida cantidad, tamaño y formato antes de procesar nada (política exception)."""
    if len(attachments) > settings.attachment_max_files:
        raise AttachmentError(
            f"Se aceptan como máximo {settings.attachment_max_files} adjuntos."
        )
    for attachment in attachments:
        if attachment.extension not in SUPPORTED_EXTENSIONS:
            raise AttachmentError(
                f"'{attachment.filename}': formato no soportado. Formatos aceptados: "
                f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}."
            )
        if len(attachment.data) > settings.attachment_max_bytes:
            raise AttachmentError(
                f"'{attachment.filename}' supera el máximo de "
                f"{settings.attachment_max_bytes // (1024 * 1024)} MB."
            )


def build_user_content(
    user_message: str, processed: list[ProcessedAttachment]
) -> list[dict]:
    """Arma el `content` del mensaje user: el texto del prompt y, a continuación,
    los bloques de cada adjunto. No depende del camino que los generó."""
    content = [{"type": "text", "text": user_message}]
    for item in processed:
        content.extend(item.content_parts)
    return content
