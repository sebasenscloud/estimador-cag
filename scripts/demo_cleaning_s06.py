"""Demo del paso 2.4: limpieza + validación sobre los presupuestos de ejemplo.

    uv run python -m scripts.demo_cleaning_s06

Historia esperada (diapositiva 18 del curso): entran 6 archivos, la
deduplicación junta las dos versiones de BUDGET-2024-0005 (gana la v2, firmada
después) y quedan 5 filas → 3 válidas, 1 en cuarentena (0002: su fecha no se
pudo convertir) y 1 descartada (0004: total_amount -50000).

Portado de scripts/demo_cleaning_s06.py del curso.
"""

import json
from pathlib import Path

from app.ingestion.cleaning import clean_budget_records, validate_with_policy

BUDGETS_DIR = Path(__file__).resolve().parent.parent / "data" / "seed" / "budgets"


def main() -> int:
    paths = sorted(BUDGETS_DIR.glob("*.json"))
    if not paths:
        print(f"ERROR: no hay presupuestos en {BUDGETS_DIR}")
        return 1
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    print(f"Archivos leídos: {len(records)}")

    df = clean_budget_records(records)
    print(f"Filas tras limpieza + dedup: {len(df)}")

    result = validate_with_policy(df)
    print("\nReporte:")
    for key, value in result.report.items():
        print(f"  {key}: {value}")
    if not result.discarded.empty:
        print("\nDescartadas:")
        print(result.discarded[["budget_id", "total_amount"]].to_string(index=False))
    if not result.quarantined.empty:
        print("\nEn cuarentena:")
        print(
            result.quarantined[["budget_id", "client_name", "signed_at"]].to_string(
                index=False
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
