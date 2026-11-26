"""Modelos Pydantic v2 del catálogo de fuentes.

El catálogo es el contrato entre lo que existe "en la naturaleza" y lo que la
ingesta acepta. Tres decisiones válidas:

- include: la fuente entra al pipeline.
- review: problemas conocidos, espera la firma de un dueño. Queda en disco,
  no se ingiere. Es la tercera vía: no colapsar todo a incluir/excluir.
- exclude: afuera, a propósito. El motivo (decision_reason) es obligatorio:
  excluir es una decisión defendible, no una omisión silenciosa.

La calidad se puntúa en cuatro dimensiones de 1 a 5 que NO se promedian:
completitud 5 y fiabilidad 1 no es "calidad 3", son datos completos que
pueden ser mentira.

Portado de app/ingestion/catalog/models.py del curso.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CatalogDecision(str, Enum):
    INCLUDE = "include"
    REVIEW = "review"  # la tercera vía
    EXCLUDE = "exclude"


class QualityScore(BaseModel):
    """Puntaje por dimensión (1 a 5), anclado en hechos concretos."""

    model_config = ConfigDict(extra="forbid")

    completeness: int = Field(ge=1, le=5)  # ¿qué % cumple el esquema?
    consistency: int = Field(ge=1, le=5)  # ¿EUR, eur, € y euros a la vez?
    actuality: int = Field(ge=1, le=5)  # ¿cuándo fue el último dato?
    reliability: int = Field(ge=1, le=5)  # ¿fuente autoritativa o una copia?


class Sensitivity(BaseModel):
    """Si la fuente tiene PII y de qué tipo. La ingesta lo usa para decidir si
    pasa los documentos por la pseudonimización (paso 2.5)."""

    model_config = ConfigDict(extra="forbid")

    has_pii: bool
    pii_flags: list[str] = Field(default_factory=list)
    access_level: Literal["public", "internal", "confidential"] = "internal"


class CatalogSource(BaseModel):
    """Una fuente auditada."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    location: str  # ruta relativa a la raíz de datos; la resuelve el loader
    owners: list[str] = Field(default_factory=list)
    format: Literal["json", "txt", "xlsx", "docx", "pdf", "csv"]
    volume_estimate: str = ""  # texto libre, ej. "≈ 6 ficheros, 250 KB"
    refresh_declared: str = ""  # lo que dice el dueño: "semanal"
    refresh_observed: str = ""  # lo que midió el inspector
    quality: QualityScore
    sensitivity: Sensitivity
    lineage: list[str] = Field(default_factory=list)
    decision: CatalogDecision
    decision_reason: str = ""
    last_audited: datetime | None = None

    @field_validator("name")
    @classmethod
    def _name_is_snake_case(cls, value: str) -> str:
        if not value or not all(c.islower() or c.isdigit() or c == "_" for c in value):
            raise ValueError(
                "CatalogSource.name tiene que ser snake_case en minúsculas "
                "(es el identificador que usa el endpoint de ingesta)"
            )
        return value

    @model_validator(mode="after")
    def _decision_requires_reason_when_not_include(self) -> "CatalogSource":
        if self.decision is not CatalogDecision.INCLUDE and not self.decision_reason:
            raise ValueError(
                f"decision={self.decision.value} exige un decision_reason no vacío"
            )
        return self


class DataCatalog(BaseModel):
    """El catálogo completo, versionado en git junto al código."""

    model_config = ConfigDict(extra="forbid")

    version: str
    description: str = ""
    sources: list[CatalogSource]

    @field_validator("sources")
    @classmethod
    def _names_are_unique(cls, sources: list[CatalogSource]) -> list[CatalogSource]:
        names = [source.name for source in sources]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise ValueError(f"Fuentes repetidas en el catálogo: {duplicated}")
        return sources

    def included_sources(self) -> list[CatalogSource]:
        return [s for s in self.sources if s.decision is CatalogDecision.INCLUDE]

    def find(self, name: str) -> CatalogSource | None:
        return next((s for s in self.sources if s.name == name), None)
