"""Contrato Pandera del DataFrame de presupuestos limpio: valida y decide.

Pandera v2 se importa como `import pandera.pandas as pa` (en v1 era
`import pandera as pa`; la migración rompió muchos tutoriales).

El contrato es estricto SIEMPRE (`coerce=False`: no convierte tipos por su
cuenta); lo que se relaja es la política (policy.py), no el contrato.

Portado de app/ingestion/cleaning/schemas.py del curso.
"""

from pandera.pandas import Check, Column, DataFrameSchema

BUDGET_ID_PATTERN = r"^BUDGET-\d{4}-\d{4}$"
CLIENT_CODE_PATTERN = r"^CLI-\d{4}$"

BudgetRecord = DataFrameSchema(
    columns={
        "budget_id": Column(str, Check.str_matches(BUDGET_ID_PATTERN), nullable=False),
        # La limpieza convierte TBD/N/A en NA: se admite (no se descarta).
        "client_name": Column(str, nullable=True),
        "client_code": Column(
            str, Check.str_matches(CLIENT_CODE_PATTERN), nullable=False
        ),
        "currency": Column(str, Check.isin(["EUR", "USD", "GBP"]), nullable=False),
        "total_amount": Column(
            float, [Check.ge(0), Check.le(10_000_000)], nullable=False
        ),
        "signed_at": Column("datetime64[ns]", nullable=False),
    },
    strict=True,
    coerce=False,
)
