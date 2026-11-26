"""Catálogo de fuentes (paso 2.2). Basado en tests/test_catalog_loader.py del curso."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.ingestion.catalog import CatalogDecision, DataCatalog, load_catalog
from app.ingestion.catalog.inspect import inspect_root

REPO_CATALOG = Path(__file__).resolve().parent.parent / "data/catalog/catalog.yaml"

SOURCE = {
    "name": "presupuestos_json",
    "location": "budgets",
    "format": "json",
    "quality": {"completeness": 4, "consistency": 3, "actuality": 5, "reliability": 4},
    "sensitivity": {"has_pii": True, "pii_flags": ["PERSON"]},
    "decision": "include",
}


def catalog(*sources: dict) -> dict:
    return {"version": "1.0.0", "sources": list(sources)}


def test_repo_catalog_decisions_match_the_course():
    loaded = load_catalog(REPO_CATALOG)
    decisions = {source.name: source.decision for source in loaded.sources}

    assert decisions == {
        "presupuestos_json": CatalogDecision.INCLUDE,
        "transcripciones_txt": CatalogDecision.REVIEW,
        "rate_card_xlsx": CatalogDecision.EXCLUDE,
    }
    assert [s.name for s in loaded.included_sources()] == ["presupuestos_json"]
    assert loaded.find("rate_card_xlsx").quality.actuality == 1


def test_load_catalog_from_yaml(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(catalog(SOURCE)), encoding="utf-8")

    assert load_catalog(path).find("presupuestos_json") is not None
    assert load_catalog(path).find("no_existe") is None


@pytest.mark.parametrize("decision", ["review", "exclude"])
def test_review_or_exclude_requires_a_reason(decision):
    with pytest.raises(ValidationError, match="decision_reason"):
        DataCatalog.model_validate(catalog({**SOURCE, "decision": decision}))

    with_reason = {**SOURCE, "decision": decision, "decision_reason": "tarifas de 2024"}
    assert DataCatalog.model_validate(catalog(with_reason))


def test_duplicate_names_are_rejected():
    with pytest.raises(ValidationError, match="repetidas"):
        DataCatalog.model_validate(catalog(SOURCE, SOURCE))


@pytest.mark.parametrize(
    "name", ["Presupuestos", "presupuestos-json", "con espacio", ""]
)
def test_name_must_be_snake_case(name):
    with pytest.raises(ValidationError, match="snake_case"):
        DataCatalog.model_validate(catalog({**SOURCE, "name": name}))


@pytest.mark.parametrize(
    "change",
    [
        {"quality": {**SOURCE["quality"], "actuality": 6}},
        {"format": "parquet"},
        {"campo_inventado": True},  # extra="forbid": un error fuerte, no silencioso
    ],
)
def test_invalid_sources_are_rejected(change):
    with pytest.raises(ValidationError):
        DataCatalog.model_validate(catalog({**SOURCE, **change}))


def test_inspect_reports_facts_per_folder_and_loose_files(tmp_path):
    (tmp_path / "budgets").mkdir()
    (tmp_path / "budgets" / "a.json").write_text("{}")
    (tmp_path / "budgets" / "b.json").write_text("{}")
    rate_card = tmp_path / "tarifas.xlsx"
    rate_card.write_bytes(b"x")
    old = datetime(2024, 1, 12, tzinfo=UTC).timestamp()
    os.utime(rate_card, (old, old))

    budgets, loose = inspect_root(tmp_path)

    assert (budgets.file_count, budgets.formats_detected) == (2, {"json"})
    assert (loose.file_count, loose.formats_detected) == (1, {"xlsx"})
    assert loose.latest_modified == datetime(2024, 1, 12, tzinfo=UTC)
