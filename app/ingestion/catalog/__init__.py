"""Catálogo versionado de fuentes de datos (paso 2.2).

Un DataCatalog es CÓDIGO (Pydantic + YAML en git), no documentación. Cada
CatalogSource declara lo que sabemos de una fuente (dueños, refresco, calidad,
sensibilidad, linaje) y una decisión explícita (include / review / exclude),
con motivo obligatorio si no es include.
"""

from app.ingestion.catalog.loader import load_catalog
from app.ingestion.catalog.models import (
    CatalogDecision,
    CatalogSource,
    DataCatalog,
    QualityScore,
    Sensitivity,
)

__all__ = [
    "CatalogDecision",
    "CatalogSource",
    "DataCatalog",
    "QualityScore",
    "Sensitivity",
    "load_catalog",
]
