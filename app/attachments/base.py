from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePath

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx"})


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
    """Resultado de procesar un adjunto, sea cual sea el camino.

    `content_parts` son bloques en el formato de mensajes de LiteLLM/OpenAI que
    se agregan al mensaje `user`: {"type": "text"} en el camino B y
    {"type": "file"} en el camino A.
    """

    filename: str
    content_parts: list[dict]
    strategy: str = "local_text"  # "local_text" (camino B) o "multimodal" (camino A)
    tool: str = ""  # pypdf, pymupdf, python-docx, multimodal_inline, ...
    reason: str = ""  # por qué el router eligió esa herramienta
    extracted_chars: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    # Limpieza posterior al turno (por ejemplo, borrar el archivo de la Files API).
    cleanup: Callable[[], None] | None = None
