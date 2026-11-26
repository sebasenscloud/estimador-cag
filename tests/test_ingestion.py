"""Ingesta y Document canónico (paso 2.3). Basado en los tests del curso."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ingestion.catalog import load_catalog
from app.ingestion.documents import Document
from app.ingestion.jobs import InMemoryJobStore
from app.ingestion.loaders import FileSystemLoader, LoadedBlob
from app.ingestion.orchestrator import IngestionRejected, ingest_source
from app.ingestion.parsers import ParseContext, Parser, ParserRegistry, default_registry
from app.ingestion.parsers.budget_json import BudgetJsonParser
from app.ingestion.parsers.transcript_txt import TranscriptTxtParser
from app.main import app

client = TestClient(app)
CATALOG = load_catalog("data/catalog/catalog.yaml")


def context(source_name: str) -> ParseContext:
    return ParseContext(CATALOG.find(source_name), CATALOG.version, datetime.now(UTC))


# --- parsers -------------------------------------------------------------------


def test_budget_json_becomes_markdown_not_a_json_dump():
    payload = {
        "budget_id": "BUDGET-2024-0009",
        "client_name": "BetaNorte",
        "currency": "EUR",
        "total_amount": 1000,
        "phases": [{"name": "Discovery", "weeks": 2, "amount": 1000}],
    }
    blob = LoadedBlob("budgets/b.json", json.dumps(payload).encode())

    (doc,) = BudgetJsonParser().parse(blob, context("presupuestos_json"))

    assert doc.text.startswith("# Presupuesto BUDGET-2024-0009")
    assert "## Cliente\n- Nombre: BetaNorte" in doc.text
    assert "**Discovery** — 2 semanas · 1000 EUR" in doc.text
    assert "{" not in doc.text
    assert doc.id == "presupuestos_json:BUDGET-2024-0009:budgets/b.json"
    assert doc.metadata.extra["total_amount"] == 1000
    assert "PERSON" in doc.metadata.sensitivity_pii_flags


TAGGED = (
    "[10:00:12] Laura Fernández: Hola.\n"
    "[10:00:20] Diego Marín: Buenas.\n"
    "[10:01:00] Laura Fernández: Arrancamos.\n"
)


def test_tagged_transcript_gives_one_document_per_turn():
    docs = list(
        TranscriptTxtParser().parse(
            LoadedBlob("t/a.txt", TAGGED.encode()), context("transcripciones_txt")
        )
    )

    assert [d.text for d in docs] == ["Hola.", "Buenas.", "Arrancamos."]
    assert docs[1].metadata.extra == {
        "speaker": "Diego Marín",
        "timestamp": "10:00:20",
        "format_mode": "tagged",
    }


def test_legacy_transcript_gives_blocks_without_inventing_a_speaker():
    raw = "Primer bloque\nsigue.\n\nSegundo bloque.\n"
    docs = list(
        TranscriptTxtParser().parse(
            LoadedBlob("t/old.txt", raw.encode()), context("transcripciones_txt")
        )
    )

    assert [d.text for d in docs] == ["Primer bloque\nsigue.", "Segundo bloque."]
    assert "speaker" not in docs[0].metadata.extra


def test_latin1_transcript_is_decoded_without_garbage():
    raw = "Reunión con el área de compras.".encode("latin-1")

    (doc,) = TranscriptTxtParser().parse(
        LoadedBlob("t/l1.txt", raw), context("transcripciones_txt")
    )

    assert doc.text == "Reunión con el área de compras."


def test_document_forbids_undeclared_fields():
    with pytest.raises(ValidationError):
        Document(id="x", text="y", metadata={}, inventado=True)


# --- registro, loader y orquestador ---------------------------------------------


def test_parsers_satisfy_the_protocol_and_one_parser_per_format():
    registry = default_registry()

    assert registry.formats() == {"json", "txt"}
    assert isinstance(registry.get("json"), Parser)
    with pytest.raises(ValueError, match="ya tiene parser"):
        registry.register(BudgetJsonParser())
    with pytest.raises(KeyError):
        ParserRegistry().get("xlsx")


def test_loader_fails_loudly_on_a_missing_location(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(FileSystemLoader(tmp_path).iter_blobs("no_existe", {"json"}))


def test_orchestrator_rejects_non_included_sources():
    jobs = InMemoryJobStore()
    with pytest.raises(IngestionRejected) as rejected:
        ingest_source(
            catalog=CATALOG,
            source_name="rate_card_xlsx",
            loader=FileSystemLoader("data/seed"),
            registry=default_registry(),
            jobs=jobs,
            job_id=uuid.uuid4(),
        )
    assert rejected.value.reason == "source_not_included"


def test_a_broken_file_marks_the_job_as_failed(tmp_path):
    (tmp_path / "budgets").mkdir()
    (tmp_path / "budgets" / "roto.json").write_text("{no es json")
    jobs = InMemoryJobStore()
    job = jobs.create("presupuestos_json")

    with pytest.raises(json.JSONDecodeError):
        ingest_source(
            catalog=CATALOG,
            source_name="presupuestos_json",
            loader=FileSystemLoader(tmp_path),
            registry=default_registry(),
            jobs=jobs,
            job_id=job.job_id,
        )
    assert jobs.get(job.job_id).status == "failed"


# --- endpoint ----------------------------------------------------------------------


def test_ingestion_run_returns_202_and_the_job_completes():
    created = client.post(
        "/api/v1/ingestion/runs", json={"source_name": "presupuestos_json"}
    )
    job_id = created.json()["job_id"]

    job = client.get(f"/api/v1/ingestion/jobs/{job_id}").json()
    documents = client.get(f"/api/v1/ingestion/jobs/{job_id}/documents").json()

    assert (created.status_code, created.json()["status"]) == (202, "pending")
    assert (job["status"], job["documents_count"]) == ("completed", 6)
    assert len(documents["documents"]) == 6


def test_the_catalog_decides_what_can_be_ingested():
    excluded = client.post(
        "/api/v1/ingestion/runs", json={"source_name": "rate_card_xlsx"}
    )
    unknown = client.post("/api/v1/ingestion/runs", json={"source_name": "no_existe"})

    assert excluded.status_code == 400
    assert excluded.json()["detail"]["reason"] == "source_not_included"
    assert "actuality=1" in excluded.json()["detail"]["decision_reason"]
    assert unknown.status_code == 404
    assert client.get(f"/api/v1/ingestion/jobs/{uuid.uuid4()}").status_code == 404
