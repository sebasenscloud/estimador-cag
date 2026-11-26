"""Parser de los presupuestos JSON del Proyecto 2.

Un JSON no se "extrae": se decide cómo representarlo. Cada presupuesto se
renderiza como **Markdown estructurado**, no como json.dumps: el embedding
entiende mejor `## Cliente · BetaNorte` que `{"client_name": "BetaNorte"}`.
Los títulos (Cliente, Total, Fases) son anclas semánticas; los números se
mantienen, solo cambia el encuadre.

Este parser NO limpia: la moneda `eur` o la fecha `12/03/2024` pasan tal
cual. La limpieza y la validación viven en un solo lugar (paso 2.4).

Portado de app/ingestion/parsers/budget_json.py del curso.
"""

import json
from collections.abc import Iterable
from typing import ClassVar

from app.ingestion.documents.models import Document
from app.ingestion.loaders.filesystem import LoadedBlob
from app.ingestion.parsers.protocol import ParseContext


class BudgetJsonParser:
    supported_formats: ClassVar[set[str]] = {"json"}

    def parse(self, blob: LoadedBlob, context: ParseContext) -> Iterable[Document]:
        payload = json.loads(blob.bytes_)
        yield Document(
            id=f"{context.source.name}:{payload['budget_id']}:{blob.relative_path}",
            text=render_budget_markdown(payload),
            metadata=context.metadata(
                blob,
                extra={
                    key: payload.get(key)
                    for key in (
                        "budget_id",
                        "client_code",
                        "currency",
                        "total_amount",
                        "signed_at",
                    )
                },
            ),
        )


def render_budget_markdown(payload: dict) -> str:
    """Un H1 (el id del presupuesto) y un H2 por bloque semántico."""
    currency = payload.get("currency", "?")
    lines = [
        f"# Presupuesto {payload.get('budget_id', '?')}",
        "",
        "## Cliente",
        f"- Nombre: {payload.get('client_name', '-')}",
        f"- Código interno: {payload.get('client_code', '-')}",
    ]
    if payload.get("contact"):
        lines.append(f"- Contacto: {payload['contact']}")
    if payload.get("contact_email"):
        lines.append(f"- Email: {payload['contact_email']}")
    lines += [
        "",
        "## Total",
        f"- Importe: {payload.get('total_amount', '?')} {currency}",
        f"- Firmado: {payload.get('signed_at', '-')}",
        "",
        "## Fases",
    ]
    phases = payload.get("phases") or []
    lines += [
        f"- **{phase.get('name', '?')}** — {phase.get('weeks', '?')} semanas · "
        f"{phase.get('amount', '?')} {currency}"
        for phase in phases
    ] or ["- (sin fases declaradas)"]
    if payload.get("notes"):
        lines += ["", "## Notas", payload["notes"]]
    return "\n".join(lines).rstrip() + "\n"
