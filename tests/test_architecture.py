"""Decisión CAG / RAG / híbrido (paso 2.1). Basado en los tests del curso."""

import pytest

from app.ingestion import architecture
from app.ingestion.architecture import (
    CURRENT_MODEL,
    MODELS,
    PROYECTO_2,
    CAGViability,
    CorpusProfile,
    assess_cag_viability,
    recommend_architecture,
)


@pytest.mark.parametrize("model", MODELS.values(), ids=list(MODELS))
def test_proyecto_2_recommends_rag_with_both_models(model):
    # El resultado del vivo: no entra en la ventana útil y además pide
    # trazabilidad y control de acceso → RAG (también con los 200K de Haiku).
    assert recommend_architecture(PROYECTO_2, model) == "RAG"


def test_small_stable_unrestricted_corpus_recommends_cag():
    corpus = CorpusProfile("glosario", 20_000, 30, False, False)

    assert recommend_architecture(corpus, CURRENT_MODEL) == "CAG"


def test_fits_and_stable_but_needs_traceability_recommends_hybrid():
    corpus = CorpusProfile("plantillas", 20_000, 30, True, False)

    assert recommend_architecture(corpus, CURRENT_MODEL) == "Hybrid"


def test_daily_refresh_breaks_prefix_caching():
    corpus = CorpusProfile("precios", 20_000, 1, False, False)

    assert assess_cag_viability(corpus, CURRENT_MODEL).failing_constraints() == ["cost"]
    assert recommend_architecture(corpus, CURRENT_MODEL) == "RAG"


def test_cag_viability_is_an_and():
    assert CAGViability(True, True, True, True).viable
    one_red = CAGViability(False, True, True, True)

    assert not one_red.viable
    assert one_red.failing_constraints() == ["context_window"]


def test_proyecto_2_fails_the_volume_constraints():
    failing = assess_cag_viability(PROYECTO_2, CURRENT_MODEL).failing_constraints()

    assert failing == ["context_window", "latency", "lost_in_the_middle"]


def test_cli_prints_the_recommendation(capsys):
    assert architecture.main(["--model", "gpt-4o-mini"]) == 0

    out = capsys.readouterr().out
    assert "NO viable" in out
    assert "Recomendación arquitectónica: RAG" in out
