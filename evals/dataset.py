"""Golden dataset: 16 casos comiteados en el repo, evaluados como una suite de tests.

Copia del dataset del curso (rama session_05_live). Tres reglas de oro:

- El dataset vive comiteado: los cambios se ven en un diff.
- Métricas deterministas siempre que se pueda.
- Un juez LLM complementa, nunca es el primer escalón.

Rangos, no números exactos: el modelo varía de forma legítima entre
ejecuciones; el caso detecta lo que se sale de proporción (fuera de
presupuesto, falta una tecnología, tier equivocado), no cada euro.
Las expectativas son opcionales: un caso solo evalúa lo que declara.

Cambio propio: en expected_in_summary, "a|b" acepta cualquiera de las
alternativas. El modelo responde en español y el dataset del curso espera
términos en inglés (casos 002, 008, 010 y 011).
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import DetailLevel, OutputFormat, ProjectType

TierStr = Literal["auto", "executive", "pm", "developer", "default"]

_DEFAULT_PATH = Path(__file__).resolve().parent / "golden_dataset.json"


class GoldenCase(BaseModel):
    """Un transcript y el rango esperado alrededor de la respuesta del modelo."""

    id: str = Field(min_length=3, max_length=64)
    transcript: str = Field(min_length=20, max_length=80_000)
    project_type: ProjectType
    detail_level: DetailLevel = DetailLevel.MEDIUM
    output_format: OutputFormat = OutputFormat.PHASES_TABLE
    # "auto" = lo resuelve el resolver; otro valor = override.
    tier: TierStr = "auto"

    expected_out_of_scope: bool = False
    expected_in_summary: list[str] = Field(default_factory=list)
    expected_technologies_any_of: list[str] = Field(default_factory=list)
    expected_phase_count_range: tuple[int, int] | None = None
    expected_cost_range_eur: tuple[int, int] | None = None
    expected_duration_weeks_range: tuple[int, int] | None = None
    # Se compara contra last_resolved_tier de la sesión.
    expected_tier: TierStr | None = None


def load_dataset(path: Path | str | None = None) -> list[GoldenCase]:
    target = Path(path) if path else _DEFAULT_PATH
    raw = json.loads(target.read_text(encoding="utf-8"))
    return [GoldenCase.model_validate(entry) for entry in raw]
