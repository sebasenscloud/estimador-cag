from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.attachments import Attachment, PdfProfile, decide_pdf, multimodal
from app.config import settings
from app.main import app
from app.services import session_estimation
from app.services.llm_wrapper import InvalidLLMOutputError
from tests.test_attachments import REQUIREMENTS, make_image_only_pdf, make_pdf

client = TestClient(app)

SIMPLE = PdfProfile(pages=3, text_chars=6_000, images=0, tables=0)
WITH_TABLES = PdfProfile(pages=3, text_chars=6_000, images=0, tables=2)
LONG = PdfProfile(pages=80, text_chars=160_000, images=2, tables=0)
SCANNED = PdfProfile(pages=5, text_chars=40, images=5, tables=0)
DIAGRAMS = PdfProfile(pages=4, text_chars=1_200, images=8, tables=0)


@pytest.mark.parametrize(
    ("profile", "strategy", "multimodal_ok", "tool"),
    [
        (SIMPLE, "auto", True, "pypdf"),
        (WITH_TABLES, "auto", True, "pymupdf"),
        (LONG, "auto", True, "pymupdf"),
        (SCANNED, "auto", True, "multimodal"),
        (DIAGRAMS, "auto", True, "multimodal"),
        (SCANNED, "auto", False, "pymupdf"),
        (SCANNED, "local_text", True, "pymupdf"),
        (DIAGRAMS, "local_text", True, "pypdf"),
        (SIMPLE, "multimodal", True, "multimodal"),
        (SIMPLE, "multimodal", False, "pymupdf"),
    ],
)
def test_router_decision(profile, strategy, multimodal_ok, tool):
    decision = decide_pdf(profile, strategy, multimodal_ok)

    assert decision.tool == tool
    assert decision.reason


def test_falling_back_from_multimodal_always_warns():
    for strategy in ("auto", "multimodal"):
        decision = decide_pdf(SCANNED, strategy, multimodal_available=False)
        assert decision.warning


def test_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "attachment_large_pdf_pages", 2)

    assert decide_pdf(SIMPLE, "auto", True).tool == "pymupdf"


@pytest.fixture
def fake_files_api(monkeypatch):
    calls = SimpleNamespace(uploaded=[], deleted=[])

    def create_file(file, purpose, custom_llm_provider, api_key):
        calls.uploaded.append((file[0], purpose, custom_llm_provider))
        return SimpleNamespace(id="file_abc123")

    def file_delete(file_id, custom_llm_provider, api_key):
        calls.deleted.append((file_id, custom_llm_provider))

    monkeypatch.setattr(multimodal.litellm, "create_file", create_file)
    monkeypatch.setattr(multimodal.litellm, "file_delete", file_delete)
    monkeypatch.setattr(settings, "attachment_multimodal_mode", "files_api")
    monkeypatch.setattr(settings, "llm_model", "anthropic/claude-haiku-4-5")
    monkeypatch.setattr(settings, "anthropic_api_key", "fake-key")
    return calls


def test_files_api_mode_uploads_and_references_by_id(fake_files_api):
    result = multimodal.process_multimodal(
        Attachment("escaneado.pdf", make_image_only_pdf()), reason="escaneado"
    )

    assert result.tool == "multimodal_files_api"
    assert fake_files_api.uploaded == [("escaneado.pdf", "messages", "anthropic")]
    assert result.content_parts[-1] == {
        "type": "file",
        "file": {"file_id": "file_abc123", "format": "application/pdf"},
    }
    result.cleanup()
    assert fake_files_api.deleted == [("file_abc123", "anthropic")]


def test_inline_mode_has_nothing_to_clean_up():
    result = multimodal.process_inline(Attachment("a.pdf", b"%PDF-1.4"), reason="x")

    assert result.cleanup is None


@pytest.fixture
def session_id():
    return client.post("/api/v1/sessions").json()["session_id"]


def post_scanned_pdf(session_id: str):
    return client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={
            "transcript": "Adjunto el relevamiento escaneado del cliente.",
            "project_type": "web_saas",
        },
        files=[("attachments", ("escaneado.pdf", make_image_only_pdf()))],
    )


def test_uploaded_files_are_deleted_after_the_turn(
    fake_files_api, session_id, monkeypatch, estimation_result
):
    sent = []

    def fake_generate(messages, response_model):
        sent.append(messages[-1]["content"])
        return estimation_result

    monkeypatch.setattr(
        session_estimation, "generate_structured_from_messages", fake_generate
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: current,
    )

    response = post_scanned_pdf(session_id)

    assert response.status_code == 200
    attachment = response.json()["attachments"][0]
    assert attachment["tool"] == "multimodal_files_api"
    assert "escaneado" in attachment["reason"]
    assert {
        "type": "file",
        "file": {"file_id": "file_abc123", "format": "application/pdf"},
    } in sent[0]
    assert fake_files_api.deleted == [("file_abc123", "anthropic")]


def test_uploaded_files_are_deleted_even_if_the_turn_fails(
    fake_files_api, session_id, monkeypatch
):
    def failing(messages, response_model):
        raise InvalidLLMOutputError("la suma no cierra")

    monkeypatch.setattr(
        session_estimation, "generate_structured_from_messages", failing
    )

    response = post_scanned_pdf(session_id)

    assert response.status_code == 502
    assert fake_files_api.deleted == [("file_abc123", "anthropic")]


def test_text_pdf_never_touches_the_files_api(
    fake_files_api, session_id, monkeypatch, estimation_result
):
    monkeypatch.setattr(
        session_estimation,
        "generate_structured_from_messages",
        lambda messages, response_model: estimation_result,
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: current,
    )

    response = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={
            "transcript": "Adjunto los requisitos del proyecto del cliente.",
            "project_type": "web_saas",
        },
        files=[("attachments", ("requisitos.pdf", make_pdf(REQUIREMENTS)))],
    )

    assert response.json()["attachments"][0]["tool"] == "pypdf"
    assert fake_files_api.uploaded == []
