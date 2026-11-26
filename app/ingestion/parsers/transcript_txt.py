"""Parser de transcripciones .txt: un formato, dos modos.

En el corpus conviven dos formatos y el parser los detecta:

- Con etiquetas `[hh:mm:ss] Hablante: texto`: un Document por turno, con
  `speaker` y `timestamp` en `extra` (sirven para filtrar después).
- Legacy (2023, sin etiquetas): un Document por bloque de texto (separados
  por líneas en blanco), SIN `speaker`: no se inventa.

Las dos ramas producen la misma forma de Document; solo cambian la
granularidad y las claves de `extra`.

Diferencia con el curso: si el archivo no es UTF-8 (error típico: Latin-1),
se relee como Latin-1 en lugar de reemplazar caracteres por "�".

Portado de app/ingestion/parsers/transcript_txt.py del curso.
"""

import re
from collections.abc import Iterable
from typing import ClassVar

from app.ingestion.documents.models import Document
from app.ingestion.loaders.filesystem import LoadedBlob
from app.ingestion.parsers.protocol import ParseContext

# [10:00:12] Laura Fernández: …
_TURN_RE = re.compile(
    r"^\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<speaker>[^:]+?):\s*(?P<text>.*)$"
)
# Un archivo es "con etiquetas" si al menos tantas líneas siguen el patrón.
MIN_TAGGED_LINES = 3


class TranscriptTxtParser:
    supported_formats: ClassVar[set[str]] = {"txt"}

    def parse(self, blob: LoadedBlob, context: ParseContext) -> Iterable[Document]:
        text = decode_text(blob.bytes_)
        if has_speaker_tags(text):
            yield from self._parse_tagged(text, blob, context)
        else:
            yield from self._parse_legacy(text, blob, context)

    def _parse_tagged(
        self, text: str, blob: LoadedBlob, context: ParseContext
    ) -> Iterable[Document]:
        for index, line in enumerate(text.splitlines()):
            match = _TURN_RE.match(line.strip())
            if not match or not match.group("text").strip():
                continue
            yield Document(
                id=f"{context.source.name}:{blob.relative_path}:turn-{index:04d}",
                text=match.group("text").strip(),
                metadata=context.metadata(
                    blob,
                    extra={
                        "speaker": match.group("speaker").strip(),
                        "timestamp": match.group("ts"),
                        "format_mode": "tagged",
                    },
                ),
            )

    def _parse_legacy(
        self, text: str, blob: LoadedBlob, context: ParseContext
    ) -> Iterable[Document]:
        blocks = [
            block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()
        ]
        for index, block in enumerate(blocks):
            yield Document(
                id=f"{context.source.name}:{blob.relative_path}:block-{index:04d}",
                text=block,
                metadata=context.metadata(
                    blob, extra={"format_mode": "legacy", "block_index": index}
                ),
            )


def has_speaker_tags(text: str) -> bool:
    tagged = sum(1 for line in text.splitlines() if _TURN_RE.match(line.strip()))
    return tagged >= MIN_TAGGED_LINES


def decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")
