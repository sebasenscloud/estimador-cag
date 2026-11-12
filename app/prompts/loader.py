from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas import (
    LOW_CONFIDENCE_PCT,
    OUT_OF_SCOPE_PREFIX,
    PROJECT_TYPE_LABELS,
    EstimationRequest,
)
from app.sessions import ProjectMetadata

PROMPTS_DIR = Path(__file__).parent


class PromptVersionNotFoundError(LookupError):
    """La versión de prompt pedida no existe para esa familia de prompts."""


def available_versions(family: str = "estimation") -> list[str]:
    return sorted(
        path.name for path in (PROMPTS_DIR / family).iterdir() if path.is_dir()
    )


@lru_cache
def _get_environment(version: str, family: str = "estimation") -> Environment:
    if version not in available_versions(family):
        raise PromptVersionNotFoundError(
            f"No existe la versión de prompt '{version}' para '{family}'. "
            f"Versiones disponibles: {', '.join(available_versions(family))}"
        )

    return Environment(
        loader=FileSystemLoader(PROMPTS_DIR / family / version),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_estimation_prompt(
    request: EstimationRequest,
    version: str = "v1",
    project_metadata: ProjectMetadata | None = None,
) -> tuple[str, str]:
    """Devuelve (system, user) renderizados con la versión de prompt indicada.

    `project_metadata` son los hechos conocidos de la sesión; sin sesión, o en
    el primer turno, el bloque <project_metadata> queda vacío.
    """
    environment = _get_environment(version)
    context = {
        "description": request.description.strip(),
        "project_type": request.project_type.value,
        "project_type_label": PROJECT_TYPE_LABELS[request.project_type],
        "detail_level": request.detail_level.value,
        "output_format": request.output_format.value,
        "low_confidence_pct": LOW_CONFIDENCE_PCT,
        "out_of_scope_prefix": OUT_OF_SCOPE_PREFIX,
        "project_metadata": project_metadata or ProjectMetadata(),
    }

    system = environment.get_template("system.j2").render(**context).strip()
    user = environment.get_template("user.j2").render(**context).strip()
    return system, user


def render_metadata_extraction_prompt(
    current: ProjectMetadata, version: str = "v1"
) -> tuple[str, str]:
    """Prompt del extractor: (system, encabezado del mensaje user).

    El resto del mensaje user lo arma el servicio con el contenido del turno
    (transcripción y adjuntos) y la estimación que devolvió el modelo.
    """
    environment = _get_environment(version, family="metadata_extraction")
    context = {"project_metadata": current}
    system = environment.get_template("system.j2").render(**context).strip()
    user = environment.get_template("user.j2").render(**context).strip()
    return system, user
