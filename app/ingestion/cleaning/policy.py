"""Política de validación: tres cubetas.

Pandera, por defecto, se corta en el primer error. En producción queremos la
lista COMPLETA de fallas (dice qué familia de suciedad pegó en la corrida):
por eso `lazy=True`, y después cada fila que falla va a una cubeta:

- valid: pasa todos los chequeos.
- quarantined: grave pero útil tras revisión (por ejemplo, una fecha que no se
  pudo convertir). NO entra al RAG; espera a un dueño.
- discarded: contaminación clara (monto negativo, id mal formado, moneda
  desconocida). Fuera, con log; no se reintenta.

Una fila que cae en las dos cubetas se descarta (descartar gana). Una falla
de tipo desconocido va a cuarentena, por las dudas.

El reporte es un dict serializable a JSON, para guardarlo junto al job.

Portado de app/ingestion/cleaning/policy.py del curso.
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pandera.pandas as pa

from app.ingestion.cleaning.schemas import BudgetRecord

# Cuarentena: la fila PUEDE volver tras una revisión manual.
QUARANTINE_CHECKS = {"not_nullable", "no_default", "column_in_dataframe"}
# Descarte: la fila está estructuralmente mal.
DISCARD_CHECKS = {
    "in_range",
    "less_than_or_equal_to",
    "greater_than_or_equal_to",
    "isin",
    "str_matches",
}


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    quarantined: pd.DataFrame
    discarded: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)


def validate_with_policy(df: pd.DataFrame) -> ValidationResult:
    """Valida `df` contra BudgetRecord y reparte las filas en tres cubetas."""
    empty = df.iloc[0:0].copy()
    if df.empty:
        return ValidationResult(df.copy(), empty, empty.copy(), {"input_rows": 0})
    try:
        validated = BudgetRecord.validate(df, lazy=True)
    except pa.errors.SchemaErrors as errors:
        return _route_failures(df, errors)
    return ValidationResult(
        validated,
        empty,
        empty.copy(),
        _report(len(df), len(validated), 0, 0, {}),
    )


def _route_failures(
    df: pd.DataFrame, errors: pa.errors.SchemaErrors
) -> ValidationResult:
    quarantine: set[int] = set()
    discard: set[int] = set()
    failures_by_check: dict[str, int] = {}

    for _, failure in errors.failure_cases.iterrows():
        check = str(failure.get("check", "unknown"))
        failures_by_check[check] = failures_by_check.get(check, 0) + 1
        index = failure.get("index")
        if index is None or pd.isna(index):
            continue  # falla de tabla (columna extra), no de una fila
        if any(name in check for name in DISCARD_CHECKS):
            discard.add(int(index))
        else:  # cuarentena conocida o falla desconocida: a revisión
            quarantine.add(int(index))

    quarantine -= discard  # descartar gana
    discarded = df.index.isin(discard)
    quarantined = df.index.isin(quarantine)
    valid = ~(discarded | quarantined)
    return ValidationResult(
        df.loc[valid].copy(),
        df.loc[quarantined].copy(),
        df.loc[discarded].copy(),
        _report(
            len(df),
            int(valid.sum()),
            int(quarantined.sum()),
            int(discarded.sum()),
            failures_by_check,
        ),
    )


def _report(total, valid, quarantined, discarded, failures) -> dict[str, Any]:
    return {
        "input_rows": total,
        "valid_rows": valid,
        "quarantined_rows": quarantined,
        "discarded_rows": discarded,
        "failures_by_check": failures,
    }
