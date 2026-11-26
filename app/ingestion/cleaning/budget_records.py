"""Limpieza reparable de los presupuestos: repara, no juzga.

Cinco pasos sobre un DataFrame que tratamos como CRUDO:

1. Nulos disfrazados: "TBD", "N/A", "" → pd.NA. Son la suciedad más fácil
   de pasar por alto y la más difícil de depurar después: si llegan al RAG,
   se vectorizan como si fueran un dato.
2. Moneda: "eur" y "EUR" colapsan a mayúsculas (cada variante sería un token
   distinto). Otra moneda se conserva: la validación decide.
3. Fechas: `pd.to_datetime(errors="coerce", dayfirst=True)`. Lo que no se
   puede convertir pasa a NaT y la validación lo enruta.
4. Números: total_amount con `errors="coerce"` (en exportaciones manuales se
   cuelan textos).
5. Deduplicación con regla de negocio explícita: si el mismo budget_id
   aparece dos veces con distinto contenido, gana el de signed_at más
   reciente. Es una decisión de negocio, no técnica: escribirla en código es
   parte del entregable.

Ojo con el paso 3 (la lección de la demo del curso): pandas infiere el
formato de la PRIMERA fecha. Con ISO (`2024-05-02`) primero, la española
`12/03/2024` del BUDGET-2024-0002 no encaja → NaT → la validación la manda a
cuarentena por `not_nullable`. La limpieza no pudo repararla y la validación
la cazó: por eso son dos pasos. Para repararla habría que parsear cada
formato explícitamente (o `format="mixed"`).

Portado de app/ingestion/cleaning/budget_records.py del curso.
"""

import hashlib
import json
from collections.abc import Iterable

import pandas as pd

NULL_PLACEHOLDERS = {"TBD", "N/A", "n/a", "tbd", "", "null", "None", "-"}
TEXT_COLUMNS = ("client_name", "contact", "contact_email", "notes")


def clean_budget_records(records: Iterable[dict]) -> pd.DataFrame:
    """DataFrame limpio a partir de los presupuestos crudos (el JSON original,
    no el Markdown del Document: se limpia antes de cualquier embedding)."""
    df = pd.DataFrame(list(records))
    if df.empty:
        return df

    # 1 · Nulos disfrazados
    for column in TEXT_COLUMNS:
        if column in df.columns:
            df[column] = df[column].apply(_null_if_placeholder)

    # 2 · Moneda
    if "currency" in df.columns:
        df["currency"] = df["currency"].astype("string").str.upper()

    # 3 · Fechas (permisivo: lo inválido pasa a NaT)
    if "signed_at" in df.columns:
        df["signed_at"] = pd.to_datetime(
            df["signed_at"], errors="coerce", dayfirst=True
        )

    # 4 · Números
    if "total_amount" in df.columns:
        df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce")

    # 5 · Dedup: gana la versión firmada más tarde
    if {"budget_id", "signed_at"}.issubset(df.columns):
        df["content_hash"] = df.apply(_content_hash, axis=1)
        df = df.sort_values(by=["budget_id", "signed_at"], na_position="first")
        df = df.drop_duplicates(subset=["budget_id"], keep="last").reset_index(
            drop=True
        )
    return df


def _null_if_placeholder(value):
    if isinstance(value, str) and value.strip() in NULL_PLACEHOLDERS:
        return pd.NA
    return value


def _content_hash(row: pd.Series) -> str:
    """JSON canónico de la fila → sha256 (NaN normalizado a None)."""
    payload = {
        key: None if _is_missing(value) else _serializable(value)
        for key, value in row.items()
        if key != "content_hash"
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_missing(value) -> bool:
    return not isinstance(value, list | dict) and pd.isna(value)


def _serializable(value):
    return value.isoformat() if isinstance(value, pd.Timestamp) else value
