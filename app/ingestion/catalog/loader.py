"""Carga y valida el catálogo YAML.

`yaml.safe_load` (nunca `yaml.load`: el YAML no puede ejecutar código) y
validación contra DataCatalog. load_catalog es trivial a propósito: las
reglas (motivo obligatorio, nombres únicos) viven en el modelo, no acá.

    uv run python -m app.ingestion.catalog.loader data/catalog/catalog.yaml
"""

import sys
from pathlib import Path

import yaml

from app.ingestion.catalog.models import DataCatalog


def load_catalog(path: str | Path) -> DataCatalog:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DataCatalog.model_validate(raw)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("uso: python -m app.ingestion.catalog.loader <catalogo.yaml>")
        return 1
    catalog = load_catalog(argv[1])
    included = catalog.included_sources()
    print(f"Versión del catálogo: {catalog.version}")
    print(f"Fuentes totales:      {len(catalog.sources)}")
    print(f"Fuentes incluidas:    {len(included)}")
    for source in included:
        print(f"  - {source.name} ({source.format})")
    for source in catalog.sources:
        if source.decision.value != "include":
            reason = " ".join(source.decision_reason.split())
            print(f"  · {source.name}: {source.decision.value} — {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
