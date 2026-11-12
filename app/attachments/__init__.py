"""Procesamiento de adjuntos (PDF y Word).

Camino elegido: B, extracción local. El texto se extrae en el servicio IA y va
al prompt detrás de la transcripción, con un separador claro.

Para que el camino A (multimodal directo) se sume sin rehacer nada, cada
procesador devuelve "content parts": bloques en el formato estándar de mensajes
de LiteLLM/OpenAI, que el servicio agrega al mensaje `user` sin saber de qué
camino vienen.

- Camino B (hoy):   {"type": "text", "text": "--- attachment: x.pdf ---\n..."}
- Camino A (futuro): {"type": "file", "file": {"file_id": "..."}}

Sumar el camino A = una clase que implemente `AttachmentProcessor` y una rama en
`get_attachment_processor()`, elegida con ATTACHMENT_STRATEGY.
"""

import io
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Protocol

import docx
import pypdf

from app.config import settings


class AttachmentError(ValueError):
    """El adjunto no se puede procesar: formato no soportado, demasiado grande..."""


@dataclass
class Attachment:
    filename: str
    data: bytes

    @property
    def extension(self) -> str:
        return PurePath(self.filename).suffix.lower()


@dataclass
class ProcessedAttachment:
    filename: str
    # Bloques que se agregan al mensaje `user`, en el formato de LiteLLM/OpenAI.
    content_parts: list[dict]
    extracted_chars: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


class AttachmentProcessor(Protocol):
    strategy: str
    supported_extensions: frozenset[str]

    def process(self, attachment: Attachment) -> ProcessedAttachment: ...


class LocalTextExtractor:
    """Camino B: pypdf para PDF y python-docx para Word."""

    strategy = "local_text"
    supported_extensions = frozenset({".pdf", ".docx"})

    def __init__(self, max_chars: int):
        self.max_chars = max_chars

    def process(self, attachment: Attachment) -> ProcessedAttachment:
        if attachment.extension == ".pdf":
            text = _extract_pdf(attachment.data)
        else:
            text = _extract_docx(attachment.data)

        text = text.strip()
        warnings = []
        if not text:
            warnings.append(
                "No se pudo extraer texto. Si es un PDF escaneado, la extracción "
                "local no puede leerlo (el camino multimodal sí)."
            )
        truncated = len(text) > self.max_chars
        if truncated:
            text = text[: self.max_chars]
            warnings.append(f"El texto se recortó a {self.max_chars} caracteres.")

        block = f"--- attachment: {attachment.filename} ---\n{text}"
        return ProcessedAttachment(
            filename=attachment.filename,
            content_parts=[{"type": "text", "text": block}],
            extracted_chars=len(text),
            truncated=truncated,
            warnings=warnings,
        )


def _extract_pdf(data: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except pypdf.errors.PdfReadError as error:
        raise AttachmentError(f"El PDF no se pudo leer: {error}") from error


def _extract_docx(data: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as error:
        raise AttachmentError(f"El documento Word no se pudo leer: {error}") from error

    lines = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def get_attachment_processor() -> AttachmentProcessor:
    if settings.attachment_strategy == "local_text":
        return LocalTextExtractor(max_chars=settings.attachment_max_chars)
    raise ValueError(
        f"ATTACHMENT_STRATEGY desconocida: '{settings.attachment_strategy}'. "
        "Valores soportados: local_text."
    )


def validate_attachments(
    attachments: list[Attachment], processor: AttachmentProcessor
) -> None:
    """Valida cantidad, tamaño y formato antes de procesar nada (política exception)."""
    if len(attachments) > settings.attachment_max_files:
        raise AttachmentError(
            f"Se aceptan como máximo {settings.attachment_max_files} adjuntos."
        )
    for attachment in attachments:
        if attachment.extension not in processor.supported_extensions:
            raise AttachmentError(
                f"'{attachment.filename}': formato no soportado. Formatos aceptados: "
                f"{', '.join(sorted(processor.supported_extensions))}."
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
