import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.prompts.loader import render_estimation_prompt
from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType
from app.services import metadata_extractor, session_estimation
from app.services.llm_wrapper import LLMProviderError
from app.sessions import ProjectMetadata, session_store

client = TestClient(app)

REQUEST = EstimationRequest(
    description="El cliente quiere una app de turnos para su cadena de peluquerías.",
    project_type=ProjectType.MOBILE_APP,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)

KNOWN = ProjectMetadata(
    project_name="Turnito",
    assumed_team_size=3,
    mentioned_technologies=["Flutter", "Stripe"],
    agreed_scope="Incluye pagos online, sin app de escritorio.",
)


def metadata_block(system_prompt: str) -> str:
    start = system_prompt.rindex("<project_metadata>")
    end = system_prompt.rindex("</project_metadata>")
    return system_prompt[start + len("<project_metadata>") : end].strip()


def test_block_is_empty_without_metadata():
    system, _ = render_estimation_prompt(REQUEST)

    assert metadata_block(system) == ""


def test_block_is_empty_for_a_new_session():
    system, _ = render_estimation_prompt(REQUEST, project_metadata=ProjectMetadata())

    assert metadata_block(system) == ""


def test_block_lists_the_known_facts():
    system, _ = render_estimation_prompt(REQUEST, project_metadata=KNOWN)
    block = metadata_block(system)

    assert "Nombre del proyecto: Turnito" in block
    assert "Tamaño de equipo asumido: 3 personas" in block
    assert "Tecnologías mencionadas: Flutter, Stripe" in block
    assert "Alcance acordado: Incluye pagos online" in block


def test_block_only_shows_the_facts_that_exist():
    system, _ = render_estimation_prompt(
        REQUEST, project_metadata=ProjectMetadata(mentioned_technologies=["AWS"])
    )

    assert metadata_block(system) == "- Tecnologías mencionadas: AWS"


def test_extractor_sends_current_metadata_turn_and_estimation(
    monkeypatch, estimation_result
):
    sent = {}

    def fake_generate(messages, response_model):
        sent.update(messages=messages, response_model=response_model)
        return KNOWN

    monkeypatch.setattr(
        metadata_extractor, "generate_structured_from_messages", fake_generate
    )
    turn = [{"type": "text", "text": "Usamos Flutter y Stripe"}]

    updated = metadata_extractor.extract_project_metadata(
        ProjectMetadata(project_name="Turnito"), turn, estimation_result
    )

    assert updated == KNOWN
    assert sent["response_model"] is ProjectMetadata
    content = sent["messages"][1]["content"]
    assert '"project_name": "Turnito"' in content[0]["text"]
    assert content[1] == turn[0]
    assert "<estimacion_devuelta>" in content[-1]["text"]


def test_extractor_failure_keeps_the_previous_metadata(monkeypatch, estimation_result):
    def failing(messages, response_model):
        raise LLMProviderError("timeout")

    monkeypatch.setattr(
        metadata_extractor, "generate_structured_from_messages", failing
    )

    updated = metadata_extractor.extract_project_metadata(
        KNOWN, [{"type": "text", "text": "hola"}], estimation_result
    )

    assert updated == KNOWN


def test_session_turns_carry_metadata_forward(monkeypatch, estimation_result):
    prompts = []

    def fake_generate(messages, response_model):
        prompts.append(messages[0]["content"])
        return estimation_result

    extracted = iter([ProjectMetadata(project_name="Turnito"), KNOWN])
    monkeypatch.setattr(
        session_estimation, "generate_structured_from_messages", fake_generate
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: next(extracted),
    )
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    form = {"transcript": REQUEST.description, "project_type": "mobile_app"}

    first = client.post(f"/api/v1/sessions/{session_id}/estimate", data=form)
    second = client.post(f"/api/v1/sessions/{session_id}/estimate", data=form)

    assert metadata_block(prompts[0]) == ""
    assert "Nombre del proyecto: Turnito" in metadata_block(prompts[1])
    assert first.json()["project_metadata"]["project_name"] == "Turnito"
    assert second.json()["project_metadata"] == KNOWN.model_dump()
    assert session_store.get(session_id).metadata == KNOWN
    session_store.delete(session_id)


@pytest.mark.parametrize("field", ["project_name", "assumed_team_size"])
def test_metadata_fields_are_optional(field):
    assert getattr(ProjectMetadata(), field) is None
