"""El Document canónico: el contrato común de la ingesta.

Venga de JSON, TXT, XLSX o PDF, sale lo mismo: texto + metadatos. Es lo único
que verá la Sesión 7 (chunking y embeddings).

Plano a propósito: DocumentMetadata tiene los campos estructurados que
necesita cualquier consumidor y lo propio de cada formato va en `extra`. Esa
válvula de escape mantiene el contrato uniforme sin inventar subtipos por
fuente.

`extra="forbid"`: si un parser cuela un campo no declarado, Pydantic falla.
Un error fuerte es mejor que uno silencioso.

El `id` es determinista (fuente + ruta + posición): estable entre
re-ingestas, para que el índice pueda deduplicar.

Portado de app/ingestion/documents/models.py del curso.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    source_version: str  # versión del catálogo vigente al ingerir
    ingested_at: datetime
    lineage: list[str] = Field(default_factory=list)
    sensitivity_pii_flags: list[str] = Field(default_factory=list)
    sensitivity_access_level: str = "internal"
    location: str = ""  # ruta de origen, relativa a la raíz de datos
    extra: dict[str, Any] = Field(default_factory=dict)  # lo propio del formato


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    metadata: DocumentMetadata
