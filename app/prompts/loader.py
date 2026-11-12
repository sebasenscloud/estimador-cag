from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas import (
    LOW_CONFIDENCE_PCT,
    OUT_OF_SCOPE_PREFIX,
    PROJECT_TYPE_LABELS,
    EstimationRequest,
)

ESTIMATION_PROMPTS_DIR = Path(__file__).parent / "estimation"


class PromptVersionNotFoundError(LookupError):
    """La versión de prompt pedida no existe en app/prompts/estimation/."""


def available_versions() -> list[str]:
    return sorted(
        path.name for path in ESTIMATION_PROMPTS_DIR.iterdir() if path.is_dir()
    )


@lru_cache
def _get_environment(version: str) -> Environment:
    if version not in available_versions():
        raise PromptVersionNotFoundError(
            f"No existe la versión de prompt '{version}'. "
            f"Versiones disponibles: {', '.join(available_versions())}"
        )

    return Environment(
        loader=FileSystemLoader(ESTIMATION_PROMPTS_DIR / version),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_estimation_prompt(
    request: EstimationRequest, version: str = "v1"
) -> tuple[str, str]:
    """Devuelve (system, user) renderizados con la versión de prompt indicada."""
    environment = _get_environment(version)
    context = {
        "description": request.description.strip(),
        "project_type": request.project_type.value,
        "project_type_label": PROJECT_TYPE_LABELS[request.project_type],
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
        "low_confidence_pct": LOW_CONFIDENCE_PCT,
        "out_of_scope_prefix": OUT_OF_SCOPE_PREFIX,
    }

    system = environment.get_template("system.j2").render(**context).strip()
    user = environment.get_template("user.j2").render(**context).strip()
    return system, user
