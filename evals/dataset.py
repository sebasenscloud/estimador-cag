"""Golden dataset: 16 casos comiteados en el repo, evaluados como una suite de tests.

Tres reglas de oro (del curso):

- El dataset vive comiteado: los cambios se ven en un diff.
- Métricas deterministas siempre que se pueda.
- Un juez LLM complementa, nunca es el primer escalón.

Rangos, no números exactos: el modelo varía de forma legítima entre
ejecuciones; el caso detecta lo que se sale de proporción, no cada euro.

Convenciones propias:

- expected_in_summary: todos los ítems tienen que aparecer en el summary
  (sin distinguir mayúsculas). Un ítem "a|b" acepta cualquiera de las
  alternativas, porque el modelo puede responder en español o en inglés.
- expected_technologies_any_of: alcanza con que aparezca una. Vacío = no se
  evalúa.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from app.schemas import ProjectType

DATASET_PATH = Path(__file__).with_name("golden_dataset.json")

Range = tuple[int, int]


class GoldenCase(BaseModel):
    id: str = Field(pattern=r"^case-\d{3}-[a-z0-9-]+$")
    transcript: str = Field(min_length=20, max_length=2000)
    project_type: ProjectType
    expected_in_summary: list[str] = Field(min_length=1)
    expected_technologies_any_of: list[str] = []
    expected_phase_count_range: Range
    expected_cost_range_eur: Range
    expected_duration_weeks_range: Range

    @model_validator(mode="after")
    def ranges_are_ordered(self) -> "GoldenCase":
        for name in (
            "expected_phase_count_range",
            "expected_cost_range_eur",
            "expected_duration_weeks_range",
        ):
            low, high = getattr(self, name)
            if not 0 < low <= high:
                raise ValueError(f"{name} inválido: [{low}, {high}]")
        return self


def load_golden_dataset(path: Path = DATASET_PATH) -> list[GoldenCase]:
    cases = [GoldenCase.model_validate(raw) for raw in json.loads(path.read_text())]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Hay ids repetidos en el golden dataset")
    return cases
