"""Tests de integración de las sesiones con httpx.AsyncClient.

Recorren la app completa (FastAPI, multipart, extracción de adjuntos, plantillas,
historial, extractor de metadata) y solo reemplazan el LLM por un modelo falso
determinístico: lee el texto de los mensajes que recibe y responde en función
de él. Así se verifica que la información llega al modelo sin pagar llamadas
reales.
"""

import re

import httpx
import pytest

from app.config import settings
from app.main import app
from app.schemas import EstimationResult
from app.services import metadata_extractor, session_estimation
from app.sessions import ProjectMetadata, session_store
from tests.test_attachments import make_pdf

BASE_COST = 20_000
PAYMENTS_COST = 8_000


def message_text(messages: list[dict]) -> str:
    """Todo el texto de un array messages, sea content string o lista de bloques."""
    parts = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(block.get("text", "") for block in content)
    return "\n".join(parts)


class FakeLLM:
    """Modelo falso: estima y extrae hechos a partir del texto que recibe."""

    def __init__(self):
        self.estimation_calls: list[list[dict]] = []

    def estimate(self, messages, response_model):
        assert response_model is EstimationResult
        self.estimation_calls.append(messages)
        current_user = message_text([messages[-1]]).lower()
        has_payments = "stripe" in current_user
        phases = [
            {
                "name": "Desarrollo",
                "description": "App de turnos.",
                "duration_weeks": 8,
                "cost_eur": BASE_COST,
                "confidence_pct": 80,
            }
        ]
        if has_payments:
            phases.append(
                {
                    "name": "Pagos online",
                    "description": "Integración con Stripe.",
                    "duration_weeks": 2,
                    "cost_eur": PAYMENTS_COST,
                    "confidence_pct": 75,
                }
            )
        return EstimationResult.model_validate(
            {
                "summary": "App de turnos para peluquerías.",
                "phases": phases,
                "total_duration_weeks": sum(p["duration_weeks"] for p in phases),
                "total_cost_eur": sum(p["cost_eur"] for p in phases),
                "confidence_pct": 80,
            }
        )

    def extract(self, messages, response_model):
        assert response_model is ProjectMetadata
        # Solo el mensaje user: el system del extractor nombra tecnologías de ejemplo.
        text = message_text(messages[1:])
        current = ProjectMetadata.model_validate_json(
            re.search(r"<ficha_actual>\s*(\{.*?\})\s*</ficha_actual>", text, re.DOTALL)[
                1
            ]
        )
        technologies = list(current.mentioned_technologies)
        for tech in ("Flutter", "Stripe"):
            if tech.lower() in text.lower() and tech not in technologies:
                technologies.append(tech)
        name = re.search(r"se llama (\w+)", text)
        team = re.search(r"equipo de (\d+)", text)
        return current.model_copy(
            update={
                "project_name": name[1] if name else current.project_name,
                "assumed_team_size": int(team[1])
                if team
                else current.assumed_team_size,
                "mentioned_technologies": technologies,
            }
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(
        session_estimation, "generate_structured_from_messages", llm.estimate
    )
    monkeypatch.setattr(
        metadata_extractor, "generate_structured_from_messages", llm.extract
    )
    return llm


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as async_client:
        yield async_client


async def new_session(client: httpx.AsyncClient) -> str:
    response = await client.post("/api/v1/sessions")
    assert response.status_code == 201
    return response.json()["session_id"]


async def send_turn(
    client: httpx.AsyncClient, session_id: str, transcript: str, files=None
) -> dict:
    response = await client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": transcript, "project_type": "mobile_app"},
        files=files,
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.anyio
async def test_session_links_two_requests_and_updates_metadata(client, fake_llm):
    session_id = await new_session(client)

    first = await send_turn(
        client,
        session_id,
        "Somos un equipo de 3 personas y la app se llama Turnito, hecha en Flutter.",
    )
    second = await send_turn(
        client, session_id, "Ahora agregale pagos online con Stripe al proyecto."
    )

    assert first["project_metadata"] == {
        "project_name": "Turnito",
        "assumed_team_size": 3,
        "mentioned_technologies": ["Flutter"],
        "agreed_scope": None,
    }
    # El segundo turno conserva los hechos del primero y suma los nuevos.
    assert second["project_metadata"]["project_name"] == "Turnito"
    assert second["project_metadata"]["assumed_team_size"] == 3
    assert second["project_metadata"]["mentioned_technologies"] == ["Flutter", "Stripe"]
    # Y la memoria del turno 1 llegó al modelo en el system prompt del turno 2.
    second_system = fake_llm.estimation_calls[1][0]["content"]
    assert "Nombre del proyecto: Turnito" in second_system
    session_store.delete(session_id)


@pytest.mark.anyio
async def test_pdf_attachment_content_changes_the_estimation(client, fake_llm):
    transcript = "El cliente quiere una app de turnos para su cadena de peluquerías."

    without = await send_turn(client, await new_session(client), transcript)
    with_pdf = await send_turn(
        client,
        await new_session(client),
        transcript,
        files=[
            (
                "attachments",
                (
                    "requisitos.pdf",
                    make_pdf("Requisito: cobrar las reservas con Stripe"),
                ),
            )
        ],
    )

    assert without["result"]["total_cost_eur"] == BASE_COST
    assert with_pdf["result"]["total_cost_eur"] == BASE_COST + PAYMENTS_COST
    assert "Pagos online" in [phase["name"] for phase in with_pdf["result"]["phases"]]
    assert with_pdf["attachments"][0]["extracted_chars"] > 0


@pytest.mark.anyio
async def test_history_sent_to_the_llm_never_exceeds_max_turns(client, fake_llm):
    session_id = await new_session(client)

    for number in range(1, 9):
        await send_turn(
            client, session_id, f"Turno {number}: seguimos definiendo la app de turnos."
        )

    assert len(fake_llm.estimation_calls) == 8
    for call_number, messages in enumerate(fake_llm.estimation_calls, start=1):
        # system + pares de la ventana + user actual
        history = messages[1:-1]
        assert len(history) % 2 == 0
        assert len(history) // 2 <= settings.session_max_turns
        assert len(history) // 2 == min(call_number - 1, settings.session_max_turns)
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"

    # En la llamada del turno 8, la ventana tiene los turnos 2 a 7: el 1 ya salió.
    last_history = message_text(fake_llm.estimation_calls[-1][1:-1])
    assert "Turno 1:" not in last_history
    assert "Turno 2:" in last_history
    assert "Turno 7:" in last_history
    session_store.delete(session_id)
