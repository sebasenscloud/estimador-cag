"""Seudonimizador consistente: mismo valor → mismo seudónimo, siempre.

Seudonimizar, no tachar: cambiar "Javier Romero" por `[PERSON]` rompe la
semántica (pruebas internas del curso: 15-25% menos de retrieval);
cambiarlo por otro nombre verosímil, y siempre el mismo, la conserva.

Flujo:

1. Presidio analiza el texto → entidades detectadas.
2. Por cada entidad, HMAC-SHA256 del valor original con el salt del
   servidor. El almacén indexa por ese hash; el texto en claro nunca sale de
   esta función.
3. `mapping_store.lookup_or_create` devuelve un seudónimo estable, generado
   la primera vez por un generador POR TIPO (un nombre por un nombre, un
   email por un email).
4. Reemplazo de derecha a izquierda: así las posiciones anteriores no se
   desplazan al cambiar el largo del texto.

Ojo: seudonimizado sigue siendo dato personal (existe un mapeo);
anonimizado es irreversible. La protección va ANTES del embedding.

Diferencias con el curso:
- Si dos detecciones se solapan (en la demo del curso, "CLI-0078" salió a la
  vez como CLIENT_CODE y como PERSON y terminó reemplazado por un nombre),
  gana la de mayor score y, a igual score, la más larga. Así el código de
  cliente se reemplaza por otro código.
- Los seudónimos de BUDGET_ID y CLIENT_CODE salen del generador de Faker
  (sembrado), no de `random`: la demo es reproducible.
- `forget(entity_type, value)`: derecho al olvido por valor.

Basado en app/ingestion/pii/pseudonymizer.py del curso.
"""

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass

from faker import Faker
from presidio_analyzer import AnalyzerEngine, RecognizerResult

from app.ingestion.pii.mapping_store import MappingStore


@dataclass(frozen=True)
class AppliedMapping:
    """Una entidad reemplazada. Lleva el hash, nunca el valor original."""

    entity_type: str
    original_hash: str
    pseudonym: str
    start: int
    end: int


@dataclass(frozen=True)
class PseudonymizationResult:
    pseudonymized_text: str
    applied: list[AppliedMapping]


class ConsistentPseudonymizer:
    def __init__(
        self,
        *,
        analyzer: AnalyzerEngine,
        mapping_store: MappingStore,
        salt: str,
        faker_locale: str = "es_ES",
        language: str = "es",
    ) -> None:
        self._analyzer = analyzer
        self._store = mapping_store
        self._salt = salt.encode("utf-8")
        self._language = language
        self._faker = Faker(faker_locale)
        # La consistencia entre llamadas la da el almacén, no la semilla; la
        # semilla solo hace reproducible la demo.
        self._faker.seed_instance(0)
        self._generators: dict[str, Callable[[], str]] = {
            "PERSON": self._faker.name,
            "EMAIL_ADDRESS": self._faker.ascii_company_email,
            "LOCATION": self._faker.city,
            "BUDGET_ID": lambda: (
                f"BUDGET-{self._faker.random_int(2020, 2029)}-"
                f"{self._faker.random_int(0, 9999):04d}"
            ),
            "CLIENT_CODE": lambda: f"CLI-{self._faker.random_int(0, 9999):04d}",
        }

    def pseudonymize(
        self, text: str, *, entities: list[str] | None = None
    ) -> PseudonymizationResult:
        detected = self._analyzer.analyze(
            text=text, language=self._language, entities=entities
        )
        output = text
        applied: list[AppliedMapping] = []
        # De derecha a izquierda: los cortes posteriores no mueven los anteriores.
        for result in sorted(
            _without_overlaps(detected), key=lambda r: r.start, reverse=True
        ):
            original_hash = self._hash(text[result.start : result.end])
            pseudonym = self._store.lookup_or_create(
                result.entity_type,
                original_hash,
                self._generators.get(result.entity_type, self._fallback_pseudonym),
            )
            output = output[: result.start] + pseudonym + output[result.end :]
            applied.append(
                AppliedMapping(
                    result.entity_type,
                    original_hash,
                    pseudonym,
                    result.start,
                    result.end,
                )
            )
        return PseudonymizationResult(output, applied)

    def forget(self, entity_type: str, original_value: str) -> bool:
        """Derecho al olvido: borra el mapeo de ese valor. Si reaparece, recibe
        un seudónimo nuevo. (En un RAG, además, hay que borrar del índice los
        chunks que contienen el seudónimo viejo: Sesión 7.)"""
        return self._store.forget(entity_type, self._hash(original_value))

    def _hash(self, value: str) -> str:
        return hmac.new(self._salt, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _fallback_pseudonym(self) -> str:
        """Tipos sin generador propio (URL, organización...)."""
        return f"REDACTED-{self._faker.uuid4()[:8]}"


def _without_overlaps(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Si dos detecciones se pisan, gana la de mayor score y después la más larga."""
    ranked = sorted(results, key=lambda r: (r.score, r.end - r.start), reverse=True)
    kept: list[RecognizerResult] = []
    for candidate in ranked:
        if all(candidate.end <= k.start or candidate.start >= k.end for k in kept):
            kept.append(candidate)
    return kept
