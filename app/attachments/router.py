"""Router de adjuntos: decide, documento por documento, cómo procesarlo.

ATTACHMENT_STRATEGY define el margen de decisión:

- auto        → el router elige entre camino A y camino B según el documento.
- local_text  → siempre camino B (pypdf o PyMuPDF según el documento).
- multimodal  → los PDFs van siempre por el camino A.

Reglas para un PDF en modo auto (en este orden):

1. Casi sin texto por página (escaneado)    → camino A: no hay texto que extraer.
2. Muchas imágenes y poco texto (diagramas)  → camino A: la información es visual.
3. Tiene tablas                              → PyMuPDF: las convierte a Markdown.
4. Es largo                                  → PyMuPDF: mucho más rápido.
5. Texto simple                              → pypdf: lo más liviano.

Si el modelo configurado no acepta PDF, el camino A no está disponible y se
cae a PyMuPDF con un aviso.
"""

from dataclasses import dataclass

from app.attachments.analysis import PdfProfile, analyze_pdf
from app.attachments.base import Attachment, AttachmentError, ProcessedAttachment
from app.attachments.extractors import (
    extract_docx,
    extract_pdf_with_pymupdf,
    extract_pdf_with_pypdf,
)
from app.attachments.multimodal import model_supports_pdf, process_multimodal
from app.config import settings

PYPDF = "pypdf"
PYMUPDF = "pymupdf"
MULTIMODAL = "multimodal"


@dataclass(frozen=True)
class Decision:
    tool: str
    reason: str
    warning: str | None = None


def decide_pdf(
    profile: PdfProfile, strategy: str, multimodal_available: bool
) -> Decision:
    """Decisión pura (sin I/O): fácil de testear y de explicar."""
    scanned = profile.chars_per_page < settings.attachment_min_chars_per_page
    visual = (
        profile.images_per_page >= settings.attachment_image_heavy_per_page
        and profile.chars_per_page < settings.attachment_visual_max_chars_per_page
    )

    if strategy == "multimodal":
        if multimodal_available:
            return Decision(MULTIMODAL, "ATTACHMENT_STRATEGY=multimodal")
        return Decision(
            PYMUPDF,
            "ATTACHMENT_STRATEGY=multimodal, pero el modelo no acepta PDF",
            warning="El modelo configurado no acepta PDF: se extrajo el texto.",
        )

    if strategy == "auto" and (scanned or visual):
        why = (
            f"casi sin texto ({profile.chars_per_page:.0f} caracteres por página): "
            "probablemente escaneado"
            if scanned
            else f"dominado por imágenes ({profile.images_per_page:.1f} por página)"
        )
        if multimodal_available:
            return Decision(MULTIMODAL, f"PDF {why}")
        return Decision(
            PYMUPDF,
            f"PDF {why}, pero el modelo no acepta PDF",
            warning="El contenido es sobre todo visual y el modelo no acepta PDF: "
            "puede faltar información.",
        )

    if scanned:
        return Decision(
            PYMUPDF,
            "ATTACHMENT_STRATEGY=local_text con un PDF casi sin texto",
            warning="No se pudo extraer texto. Si es un PDF escaneado, solo el camino "
            "multimodal puede leerlo.",
        )
    if profile.tables:
        return Decision(PYMUPDF, f"{profile.tables} tabla(s): se convierten a Markdown")
    if profile.pages > settings.attachment_large_pdf_pages:
        return Decision(PYMUPDF, f"PDF largo ({profile.pages} páginas)")
    return Decision(PYPDF, "PDF de texto simple")


class AttachmentRouter:
    def __init__(self, strategy: str, max_chars: int, model: str):
        self.strategy = strategy
        self.max_chars = max_chars
        self.multimodal_available = model_supports_pdf(model)

    def process(self, attachment: Attachment) -> ProcessedAttachment:
        if attachment.extension == ".docx":
            # Las APIs multimodales no aceptan Word: siempre extracción local.
            return self._text_result(
                attachment,
                extract_docx(attachment.data),
                tool="python-docx",
                reason="documento Word",
            )
        if attachment.extension != ".pdf":
            raise AttachmentError(f"'{attachment.filename}': formato no soportado.")

        decision = decide_pdf(
            analyze_pdf(attachment.data), self.strategy, self.multimodal_available
        )
        if decision.tool == MULTIMODAL:
            return process_multimodal(attachment, decision.reason)

        extract = (
            extract_pdf_with_pypdf
            if decision.tool == PYPDF
            else extract_pdf_with_pymupdf
        )
        result = self._text_result(
            attachment,
            extract(attachment.data),
            tool=decision.tool,
            reason=decision.reason,
        )
        if decision.warning:
            result.warnings.insert(0, decision.warning)
        return result

    def _text_result(
        self, attachment: Attachment, text: str, tool: str, reason: str
    ) -> ProcessedAttachment:
        text = text.strip()
        warnings = []
        if not text:
            warnings.append("No se pudo extraer texto del documento.")
        truncated = len(text) > self.max_chars
        if truncated:
            text = text[: self.max_chars]
            warnings.append(f"El texto se recortó a {self.max_chars} caracteres.")

        return ProcessedAttachment(
            filename=attachment.filename,
            content_parts=[
                {
                    "type": "text",
                    "text": f"--- attachment: {attachment.filename} ---\n{text}",
                }
            ],
            strategy="local_text",
            tool=tool,
            reason=reason,
            extracted_chars=len(text),
            truncated=truncated,
            warnings=warnings,
        )
