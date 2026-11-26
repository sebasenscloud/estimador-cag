"""Demo del paso 2.5: seudonimizar la transcripción de BetaNorte.

    uv run python -m scripts.demo_pii_s06

Muestra el texto antes y después, y los mapeos aplicados. Mismo valor
original → mismo seudónimo en todo el documento.

Basado en scripts/demo_pii_s06.py del curso.
"""

from pathlib import Path

from app.config import settings
from app.ingestion.pii import (
    ConsistentPseudonymizer,
    InMemoryMappingStore,
    build_analyzer,
)

TRANSCRIPT = (
    Path(__file__).resolve().parent.parent
    / "data/seed/transcripts/transcripcion_2025-02-03_betanorte.txt"
)


def main() -> int:
    if not TRANSCRIPT.exists():
        print(f"ERROR: no existe {TRANSCRIPT}")
        return 1
    text = TRANSCRIPT.read_text(encoding="utf-8")
    pseudonymizer = ConsistentPseudonymizer(
        analyzer=build_analyzer(),
        mapping_store=InMemoryMappingStore(),
        salt=settings.pseudonym_hash_salt,
        faker_locale=settings.pseudonym_faker_locale,
    )
    result = pseudonymizer.pseudonymize(text)

    print(f"Transcripción: {TRANSCRIPT.name}\n")
    print("--- Antes (primeros 300 caracteres) ---")
    print(text[:300])
    print("\n--- Después ---")
    print(result.pseudonymized_text[:300])
    print(f"\n--- Mapeos aplicados ({len(result.applied)}) ---")
    for mapping in sorted(result.applied, key=lambda m: m.start):
        original = text[mapping.start : mapping.end]
        print(f"  {mapping.entity_type:<14} {original!r:<28} → {mapping.pseudonym}")
    print(
        "\nEn el almacén se guarda el hash (HMAC-SHA256), nunca el valor original. "
        "Acá se muestra el original solo para la demo."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
