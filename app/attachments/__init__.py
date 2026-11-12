"""Procesamiento de adjuntos (PDF y Word).

Camino elegido: B, extracción local. El texto se extrae en el servicio IA y se
concatena a la transcripción con un separador claro.

Para que el camino A (multimodal directo) se pueda sumar sin rehacer nada, todo
pasa por la interfaz `AttachmentProcessor`: el endpoint y el servicio solo
llaman a `get_attachment_processor()` y a `apply_attachments()`. Un procesador
del camino A devolvería referencias a archivos (Files API) en lugar de texto, y
se elegiría con ATTACHMENT_STRATEGY.
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
    text: str
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

        return ProcessedAttachment(
            filename=attachment.filename,
            text=text,
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


def apply_attachments(transcript: str, processed: list[ProcessedAttachment]) -> str:
    """Camino B: concatena el texto de cada adjunto a la transcripción."""
    sections = [transcript.strip()]
    for item in processed:
        sections.append(f"--- attachment: {item.filename} ---\n{item.text}")
    return "\n\n".join(sections)
