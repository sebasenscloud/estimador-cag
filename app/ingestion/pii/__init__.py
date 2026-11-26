"""Seudonimización compatible con GDPR (paso 2.5).

- analyzer: Presidio conectado a spaCy EN ESPAÑOL (sin él, no ve nombres
  hispanos y no avisa).
- recognizers: identificadores del dominio (BUDGET-YYYY-NNNN, CLI-NNNN).
- pseudonymizer: ConsistentPseudonymizer, mismo valor → mismo seudónimo.
- mapping_store: el almacén de mapeos (en memoria), que hace posible el
  derecho al olvido.
"""

from app.ingestion.pii.analyzer import build_analyzer
from app.ingestion.pii.mapping_store import InMemoryMappingStore, MappingStore
from app.ingestion.pii.pseudonymizer import (
    AppliedMapping,
    ConsistentPseudonymizer,
    PseudonymizationResult,
)
from app.ingestion.pii.recognizers import BudgetIdRecognizer, ClientCodeRecognizer

__all__ = [
    "AppliedMapping",
    "BudgetIdRecognizer",
    "ClientCodeRecognizer",
    "ConsistentPseudonymizer",
    "InMemoryMappingStore",
    "MappingStore",
    "PseudonymizationResult",
    "build_analyzer",
]
