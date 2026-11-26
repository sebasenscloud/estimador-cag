"""El protocolo Parser y el ParseContext que recibe.

¿Por qué un Protocol y no una clase base abstracta? Los parsers no comparten
implementación (no hay nada útil que heredar), y el tipado estructural hace
trivial meter un falso en los tests: cualquier clase con `supported_formats`
y `parse(blob, context)` cumple el contrato. Agregar un formato es registrar
una clase.

Portado de app/ingestion/parsers/protocol.py del curso.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol, runtime_checkable

from app.ingestion.catalog.models import CatalogSource
from app.ingestion.documents.models import Document, DocumentMetadata
from app.ingestion.loaders.filesystem import LoadedBlob


@dataclass(frozen=True)
class ParseContext:
    """Lo que el parser necesita y no está en el blob. Se arma una vez por
    corrida: `ingested_at` y `source_version` son constantes en toda la
    corrida, y eso permite deduplicar aguas abajo."""

    source: CatalogSource
    source_version: str
    ingested_at: datetime

    def metadata(self, blob: LoadedBlob, extra: dict) -> DocumentMetadata:
        """Metadatos comunes del Document (fuente, linaje, sensibilidad)."""
        return DocumentMetadata(
            source_name=self.source.name,
            source_version=self.source_version,
            ingested_at=self.ingested_at,
            lineage=list(self.source.lineage),
            sensitivity_pii_flags=list(self.source.sensitivity.pii_flags),
            sensitivity_access_level=self.source.sensitivity.access_level,
            location=blob.relative_path,
            extra=extra,
        )


@runtime_checkable
class Parser(Protocol):
    supported_formats: ClassVar[set[str]]

    def parse(self, blob: LoadedBlob, context: ParseContext) -> Iterable[Document]: ...
