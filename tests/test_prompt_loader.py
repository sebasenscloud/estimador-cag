import re

import pytest
from jinja2 import UndefinedError

from app.prompts import loader
from app.prompts.loader import (
    PromptVersionNotFoundError,
    available_versions,
    render_estimation_prompt,
)
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    EstimationResult,
    OutputFormat,
    ProjectType,
)

REQUEST = EstimationRequest(
    description="Una inmobiliaria quiere un SaaS para publicar propiedades y agendar visitas.",
    project_type=ProjectType.WEB_SAAS,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)

DETAIL_MARKERS = {
    DetailLevel.SUMMARY: "nivel resumido",
    DetailLevel.MEDIUM: "nivel intermedio",
    DetailLevel.DETAILED: "nivel detallado",
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


def test_output_format_does_not_change_the_prompt():
    # El formato es una decisión de la UI: los mismos datos sirven para todos.
    prompts = {render(output_format=option) for option in OutputFormat}

    assert len(prompts) == 1


def test_few_shot_examples_match_the_result_schema():
    system, _ = render_estimation_prompt(REQUEST)
    examples = re.findall(
        r"<estimacion>\s*(\{.*?\})\s*</estimacion>", system, re.DOTALL
    )

    assert len(examples) >= 2
    for example in examples:
        EstimationResult.model_validate_json(example)


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
    instructions = system.split("<ejemplos>")[0]

    assert "\n\n\n" not in system
    assert all(line == line.lstrip() for line in instructions.splitlines())


def test_unknown_version_raises_clear_error():
    with pytest.raises(PromptVersionNotFoundError, match="v99"):
        render_estimation_prompt(REQUEST, version="v99")


def test_missing_variable_fails_instead_of_rendering_empty():
    environment = loader._get_environment("v1")

    with pytest.raises(UndefinedError):
        environment.get_template("user.j2").render(description="sin tipo de proyecto")
