"""PII y GDPR (paso 2.5). Basado en los tests del curso (con Presidio real)."""

import pytest

from app.ingestion.pii import (
    ConsistentPseudonymizer,
    InMemoryMappingStore,
    MappingStore,
    build_analyzer,
)

TEXT = (
    "Laura Fernández firmó el presupuesto BUDGET-2024-0002 del cliente CLI-0078 "
    "en Madrid. Escribile a laura@acme.com. Mañana vuelve a llamar Laura Fernández."
)


@pytest.fixture(scope="module")
def analyzer():
    return build_analyzer()  # spaCy en español: carga una sola vez


@pytest.fixture
def store():
    return InMemoryMappingStore()


@pytest.fixture
def pseudonymizer(analyzer, store):
    return ConsistentPseudonymizer(
        analyzer=analyzer, mapping_store=store, salt="test-salt"
    )


def entities(analyzer, text):
    return {
        (r.entity_type, text[r.start : r.end])
        for r in analyzer.analyze(text, language="es")
    }


def test_spanish_analyzer_detects_hispanic_names_and_domain_ids(analyzer):
    found = entities(analyzer, TEXT)

    assert ("PERSON", "Laura Fernández") in found
    assert ("BUDGET_ID", "BUDGET-2024-0002") in found
    assert ("CLIENT_CODE", "CLI-0078") in found
    assert ("EMAIL_ADDRESS", "laura@acme.com") in found


def test_no_original_value_survives(pseudonymizer):
    out = pseudonymizer.pseudonymize(TEXT).pseudonymized_text

    for original in (
        "Laura Fernández",
        "BUDGET-2024-0002",
        "CLI-0078",
        "laura@acme.com",
    ):
        assert original not in out


def test_same_value_same_pseudonym_across_texts(pseudonymizer):
    first = pseudonymizer.pseudonymize("Hablé con Laura Fernández.")
    second = pseudonymizer.pseudonymize("Mañana Laura Fernández confirma.")

    name = next(m for m in first.applied if m.entity_type == "PERSON").pseudonym
    assert name in second.pseudonymized_text


def test_pseudonyms_keep_the_shape_of_each_type(pseudonymizer):
    applied = {
        m.entity_type: m.pseudonym for m in pseudonymizer.pseudonymize(TEXT).applied
    }

    assert applied["BUDGET_ID"].startswith("BUDGET-")
    assert applied["CLIENT_CODE"].startswith("CLI-")  # código por código, no un nombre
    assert "@" in applied["EMAIL_ADDRESS"]


def test_the_store_keeps_hashes_never_original_values(pseudonymizer, store):
    result = pseudonymizer.pseudonymize(TEXT)

    stored = repr(store._mappings)
    assert "Laura Fernández" not in stored
    assert all(len(m.original_hash) == 64 for m in result.applied)  # sha256 hex


def test_forget_gives_a_new_pseudonym_next_time(pseudonymizer):
    before = pseudonymizer.pseudonymize("Firmó Laura Fernández.").pseudonymized_text

    assert pseudonymizer.forget("PERSON", "Laura Fernández")
    after = pseudonymizer.pseudonymize("Firmó Laura Fernández.").pseudonymized_text

    assert before != after
    assert not pseudonymizer.forget("PERSON", "Laura Fernández nunca vista")


def test_a_different_salt_gives_different_hashes(analyzer):
    def hashes(salt):
        p = ConsistentPseudonymizer(
            analyzer=analyzer, mapping_store=InMemoryMappingStore(), salt=salt
        )
        return {m.original_hash for m in p.pseudonymize(TEXT).applied}

    assert hashes("a").isdisjoint(hashes("b"))


def test_text_without_pii_is_unchanged(pseudonymizer):
    text = "El backend está cerrado y la integración sigue."

    result = pseudonymizer.pseudonymize(text)

    assert (result.pseudonymized_text, result.applied) == (text, [])


def test_in_memory_store_satisfies_the_protocol(store):
    assert isinstance(store, MappingStore)
    assert store.lookup_or_create("PERSON", "h", lambda: "A") == "A"
    assert store.lookup_or_create("PERSON", "h", lambda: "B") == "A"
