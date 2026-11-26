import pytest

from scripts import preflight_s06 as preflight


def test_pending_pieces_do_not_fail_the_preflight(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(preflight, "SEED", tmp_path / "no-existe")
    monkeypatch.setattr(preflight, "CATALOG", tmp_path / "no-existe.yaml")
    monkeypatch.setattr(
        preflight,
        "CHECKS",
        [
            preflight.Check("Corpus", preflight.check_corpus_seed),
            preflight.Check("Catálogo", preflight.check_catalog),
        ],
    )

    assert preflight.main() == 0
    assert "⏳" in capsys.readouterr().out


def test_a_failing_check_returns_exit_code_1(monkeypatch):
    def broken():
        raise RuntimeError("roto")

    monkeypatch.setattr(preflight, "CHECKS", [preflight.Check("Roto", broken)])

    assert preflight.main() == 1


def test_spanish_spacy_model_detects_a_hispanic_name():
    """La comprobación más importante: sin esto, Presidio no ve nombres."""
    assert "Laura Fernández" in preflight.check_spacy_model()


def test_corpus_must_be_utf8(tmp_path, monkeypatch):
    (tmp_path / "budgets").mkdir()
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "budgets" / "a.json").write_text("{}")
    (tmp_path / "transcripts" / "t.txt").write_bytes("Reunión".encode("latin-1"))
    (tmp_path / "tarifas.xlsx").write_bytes(b"")
    monkeypatch.setattr(preflight, "SEED", tmp_path)

    with pytest.raises(RuntimeError, match="no son UTF-8"):
        preflight.check_corpus_seed()
