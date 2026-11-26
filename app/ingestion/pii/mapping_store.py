"""Almacén de mapeos: lo que hace consistente (y reversible) la seudonimización.

Guarda (tipo de entidad, HMAC del valor original) → seudónimo. Nunca guarda
el valor original: solo su hash. Esa asimetría hace auditable el derecho al
olvido (GDPR art. 17): borrar la fila por hash elimina la reversibilidad sin
haber guardado jamás el texto en claro. Si la persona reaparece, recibe un
seudónimo nuevo.

La tabla de mapeo vive SEPARADA del corpus, con su propio control de acceso.

En el curso hay dos implementaciones (Postgres y memoria). Como acordamos,
usamos la de memoria; MappingStore es un Protocol, así que pasar a Postgres
es escribir otra clase con los mismos dos métodos. Se pierde al reiniciar.

Basado en app/ingestion/pii/mapping_store.py del curso.
"""

import threading
from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class MappingStore(Protocol):
    def lookup_or_create(
        self,
        entity_type: str,
        original_hash: str,
        new_pseudonym_factory: Callable[[], str],
    ) -> str: ...

    def forget(self, entity_type: str, original_hash: str) -> bool: ...


class InMemoryMappingStore:
    """Idempotente: el mismo (tipo, hash) devuelve siempre el mismo seudónimo."""

    def __init__(self) -> None:
        self._mappings: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def lookup_or_create(
        self,
        entity_type: str,
        original_hash: str,
        new_pseudonym_factory: Callable[[], str],
    ) -> str:
        key = (entity_type, original_hash)
        with self._lock:
            if key not in self._mappings:
                self._mappings[key] = new_pseudonym_factory()
            return self._mappings[key]

    def forget(self, entity_type: str, original_hash: str) -> bool:
        with self._lock:
            return self._mappings.pop((entity_type, original_hash), None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._mappings)
