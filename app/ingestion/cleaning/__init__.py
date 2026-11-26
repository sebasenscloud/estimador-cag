"""Limpieza y validación (paso 2.4): dos pasos distintos.

- budget_records.clean_budget_records: limpieza permisiva con pandas que
  REPARA y no juzga (nulos disfrazados, moneda, fechas, números, dedup).
- policy.validate_with_policy: validación estricta con Pandera que DECIDE y
  reparte cada fila en válida / cuarentena / descartada.

Viven en un solo módulo, entre el parser y el Document, no repartidas en
parches por el chunker, el embedder y el retriever: así se pueden auditar,
testear y parar el pipeline cuando algo falla.
"""

from app.ingestion.cleaning.budget_records import clean_budget_records
from app.ingestion.cleaning.policy import ValidationResult, validate_with_policy
from app.ingestion.cleaning.schemas import BudgetRecord

__all__ = [
    "BudgetRecord",
    "ValidationResult",
    "clean_budget_records",
    "validate_with_policy",
]
