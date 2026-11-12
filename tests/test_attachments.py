import io

import docx
import pytest
from fastapi.testclient import TestClient

from app.attachments import (
    Attachment,
    AttachmentError,
    LocalTextExtractor,
    ProcessedAttachment,
    build_user_content,
    validate_attachments,
)
from app.config import settings
from app.main import app
from app.services import session_estimation
from app.sessions import session_store

client = TestClient(app)

TRANSCRIPT = "El cliente quiere una app de turnos para su cadena de peluquerías."


def make_pdf(text: str) -> bytes:
    """Arma un PDF mínimo de una página con texto, sin librerías extra."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = io.BytesIO(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = output.tell()
    output.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        output.write(b"%010d 00000 n \n" % offset)
    output.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objects) + 1, xref)
    )
    return output.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def text_of(result: ProcessedAttachment) -> str:
    return "".join(part["text"] for part in result.content_parts)


def test_extracts_text_from_pdf_as_a_text_block_with_separator():
    extractor = LocalTextExtractor(max_chars=1000)

    result = extractor.process(
        Attachment("requisitos.pdf", make_pdf("Login con Google y pagos"))
    )

    assert [part["type"] for part in result.content_parts] == ["text"]
    assert text_of(result).startswith("--- attachment: requisitos.pdf ---\n")
    assert "Login con Google y pagos" in text_of(result)
    assert result.extracted_chars == len("Login con Google y pagos")
    assert result.warnings == []


def test_extracts_text_from_docx():
    extractor = LocalTextExtractor(max_chars=1000)
    data = make_docx(["Alcance: reservas online", "Equipo: 2 desarrolladores"])

    result = extractor.process(Attachment("minuta.docx", data))

    assert "Alcance: reservas online" in text_of(result)
    assert "Equipo: 2 desarrolladores" in text_of(result)


def test_long_text_is_truncated_with_a_warning():
    extractor = LocalTextExtractor(max_chars=10)

    result = extractor.process(Attachment("largo.docx", make_docx(["x" * 50])))

    assert result.truncated is True
    assert result.extracted_chars == 10
    assert result.warnings


def test_document_without_text_returns_a_warning():
    extractor = LocalTextExtractor(max_chars=1000)

    result = extractor.process(Attachment("vacio.docx", make_docx([])))

    assert result.extracted_chars == 0
    assert "escaneado" in result.warnings[0]


def test_corrupt_pdf_raises_attachment_error():
    with pytest.raises(AttachmentError):
        LocalTextExtractor(max_chars=1000).process(Attachment("roto.pdf", b"no-pdf"))


def test_unsupported_format_is_rejected():
    with pytest.raises(AttachmentError, match="formato no soportado"):
        validate_attachments(
            [Attachment("planilla.xlsx", b"datos")], LocalTextExtractor(1000)
        )


def test_too_many_attachments_are_rejected(monkeypatch):
    monkeypatch.setattr(settings, "attachment_max_files", 1)
    files = [Attachment("a.pdf", b"x"), Attachment("b.pdf", b"x")]

    with pytest.raises(AttachmentError, match="máximo"):
        validate_attachments(files, LocalTextExtractor(1000))


def test_user_content_puts_the_prompt_first_and_then_each_attachment():
    pdf_block = {"type": "text", "text": "--- attachment: requisitos.pdf ---\nLogin"}
    file_block = {"type": "file", "file": {"file_id": "file_123"}}

    content = build_user_content(
        "prompt del usuario",
        [
            ProcessedAttachment("requisitos.pdf", [pdf_block]),
            ProcessedAttachment("planos.pdf", [file_block]),
        ],
    )

    # El armado no depende del camino: un bloque "file" (camino A) entra igual.
    assert content == [
        {"type": "text", "text": "prompt del usuario"},
        pdf_block,
        file_block,
    ]


@pytest.fixture
def prompts_sent(monkeypatch, estimation_result):
    """Guarda el `content` del mensaje user que se le manda al modelo."""
    sent = []

    def fake_generate(messages, response_model):
        assert [message["role"] for message in messages] == ["system", "user"]
        sent.append(messages[1]["content"])
        return estimation_result

    monkeypatch.setattr(
        session_estimation, "generate_structured_from_messages", fake_generate
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: current,
    )
    return sent


def all_text(content: list[dict]) -> str:
    return "\n".join(part.get("text", "") for part in content)


def new_session_id() -> str:
    return client.post("/api/v1/sessions").json()["session_id"]


def test_endpoint_accepts_transcript_and_attachments(prompts_sent):
    session_id = new_session_id()

    response = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": TRANSCRIPT, "project_type": "mobile_app"},
        files=[
            ("attachments", ("requisitos.pdf", make_pdf("Login con Google"))),
            ("attachments", ("minuta.docx", make_docx(["Pagos con Stripe"]))),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert [item["filename"] for item in body["attachments"]] == [
        "requisitos.pdf",
        "minuta.docx",
    ]
    content = prompts_sent[0]
    assert len(content) == 3
    assert TRANSCRIPT in content[0]["text"]
    assert content[1]["text"].startswith("--- attachment: requisitos.pdf ---")
    assert "Login con Google" in content[1]["text"]
    assert content[2]["text"].startswith("--- attachment: minuta.docx ---")
    assert "Pagos con Stripe" in content[2]["text"]


def test_endpoint_works_without_attachments(prompts_sent):
    session_id = new_session_id()

    response = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": TRANSCRIPT, "project_type": "web_saas"},
    )

    assert response.status_code == 200
    assert response.json()["attachments"] == []
    assert len(prompts_sent[0]) == 1
    assert "--- attachment:" not in all_text(prompts_sent[0])


def test_endpoint_rejects_unknown_session(prompts_sent):
    response = client.post(
        "/api/v1/sessions/no-existe/estimate",
        data={"transcript": TRANSCRIPT, "project_type": "web_saas"},
    )

    assert response.status_code == 404
    assert prompts_sent == []


def test_endpoint_rejects_unsupported_attachment(prompts_sent):
    session_id = new_session_id()

    response = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": TRANSCRIPT, "project_type": "web_saas"},
        files=[("attachments", ("planilla.xlsx", b"datos"))],
    )

    assert response.status_code == 422
    assert prompts_sent == []


def test_endpoint_validates_typed_fields(prompts_sent):
    session_id = new_session_id()

    short = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": "corto", "project_type": "web_saas"},
    )
    bad_enum = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={"transcript": TRANSCRIPT, "project_type": "videojuego"},
    )

    assert short.status_code == 422
    assert bad_enum.status_code == 422
    assert prompts_sent == []


def teardown_module():
    for session_id in list(session_store._sessions):
        session_store.delete(session_id)
