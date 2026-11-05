import pytest
from jinja2 import UndefinedError

from app.prompts import loader
from app.prompts.loader import (
    PromptVersionNotFoundError,
    available_versions,
    render_estimation_prompt,
)
from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType

REQUEST = EstimationRequest(
    description="Una inmobiliaria quiere un SaaS para publicar propiedades y agendar visitas.",
    project_type=ProjectType.WEB_SAAS,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)

FORMAT_MARKERS = {
    OutputFormat.PHASES_TABLE: "tabla en markdown organizada por fases",
    OutputFormat.LINE_ITEMS: "un ítem por tarea",
    OutputFormat.NARRATIVE: "texto narrativo en párrafos",
}

DETAIL_MARKERS = {
    DetailLevel.SUMMARY: "estimación resumida",
    DetailLevel.MEDIUM: "estimación intermedia",
    DetailLevel.DETAILED: "estimación detallada",
}


def render(**changes) -> tuple[str, str]:
    return render_estimation_prompt(REQUEST.model_copy(update=changes))


def test_returns_system_and_user_strings():
    system, user = render_estimation_prompt(REQUEST)

    assert isinstance(system, str) and system
    assert isinstance(user, str) and user


def test_default_version_is_v1():
    assert render_estimation_prompt(REQUEST) == render_estimation_prompt(
        REQUEST, version="v1"
    )
    assert "v1" in available_versions()


def test_system_includes_role_and_examples():
    system, _ = render_estimation_prompt(REQUEST)

    assert system.startswith("Sos un estimador experto")
    assert "<ejemplos>" in system
    assert system.count("<ejemplo numero=") >= 2


def test_user_wraps_description_and_project_type():
    _, user = render_estimation_prompt(REQUEST)

    assert f"<descripcion>\n{REQUEST.description}\n</descripcion>" in user
    assert "<tipo_de_proyecto>SaaS web</tipo_de_proyecto>" in user


@pytest.mark.parametrize("output_format", list(OutputFormat))
def test_only_the_selected_output_format_is_rendered(output_format):
    system, _ = render(output_format=output_format)

    for option, marker in FORMAT_MARKERS.items():
        assert (marker in system.lower()) == (option == output_format)


@pytest.mark.parametrize("detail_level", list(DetailLevel))
def test_only_the_selected_detail_level_is_rendered(detail_level):
    system, _ = render(detail_level=detail_level)

    for option, marker in DETAIL_MARKERS.items():
        assert (marker in system.lower()) == (option == detail_level)


def test_no_template_syntax_leaks_into_output():
    system, user = render_estimation_prompt(REQUEST)

    for text in (system, user):
        assert "{%" not in text
        assert "{{" not in text


def test_block_tags_leave_no_indented_or_triple_blank_lines():
    system, _ = render_estimation_prompt(REQUEST)

    assert "\n\n\n" not in system
    assert all(
        line == line.lstrip() or line.startswith("|") for line in system.splitlines()
    )


def test_unknown_version_raises_clear_error():
    with pytest.raises(PromptVersionNotFoundError, match="v99"):
        render_estimation_prompt(REQUEST, version="v99")


def test_missing_variable_fails_instead_of_rendering_empty():
    environment = loader._get_environment("v1")

    with pytest.raises(UndefinedError):
        environment.get_template("user.j2").render(description="sin tipo de proyecto")
