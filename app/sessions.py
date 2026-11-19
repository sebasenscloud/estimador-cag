"""Estado de las sesiones conversacionales del servicio IA.

Una sesión separa dos cosas que suelen confundirse:

- **Historial** (`ConversationHistory`): el array `messages` que viaja a la API en
  cada llamada. Es texto crudo y se recorta con una ventana deslizante: los
  turnos viejos se descartan.
- **Memoria** (`ProjectMetadata`): los hechos del proyecto en curso (nombre,
  equipo, tecnologías, alcance). Es conocimiento destilado que se conserva y se
  reutiliza aunque la ventana ya haya descartado el turno donde se mencionó.

Volatilidad aceptada en esta fase: las sesiones viven en un diccionario en la
memoria del proceso, sin base de datos ni Redis. Si el servicio se reinicia, se
pierden. Lo aceptamos porque el objetivo ahora es validar el modelo de
historial + memoria, no la persistencia; un store en memoria no agrega
infraestructura y se testea sin dependencias. La contrapartida también es
conocida: no sobrevive a reinicios ni se comparte entre varias instancias del
servicio. `SessionStore` expone solo create / get / delete para poder
reemplazarlo por Redis o una base de datos sin tocar a quien lo usa.
"""

import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.config import settings

# Turnos (pares user + assistant) que conserva la ventana por defecto.
# Se ajusta con SESSION_MAX_TURNS.
MAX_TURNS = 6


class ProjectMetadata(BaseModel):
    """Hechos relevantes del proyecto en curso. Vive aparte del historial."""

    project_name: str | None = None
    assumed_team_size: int | None = Field(default=None, ge=1)
    mentioned_technologies: list[str] = Field(default_factory=list)
    agreed_scope: str | None = None


class MetadataChange(BaseModel):
    """Un cambio en la memoria, con su origen: responde "¿por qué el LLM asumió X?"."""

    turn: int
    field: str
    action: str  # set (primera vez), changed (corregido), added / removed (listas)
    value: str | int | None
    previous: str | int | None = None
    # Lo que entró en ese turno: el mensaje del usuario y los adjuntos, si hubo.
    sources: list[str]


def diff_metadata(
    before: ProjectMetadata, after: ProjectMetadata
) -> list[tuple[str, str, str | int | None, str | int | None]]:
    """Compara dos fichas y devuelve (campo, acción, valor, valor anterior)."""
    changes = []
    for name in ("project_name", "assumed_team_size", "agreed_scope"):
        old, new = getattr(before, name), getattr(after, name)
        if old == new:
            continue
        if new is None:
            changes.append((name, "removed", None, old))
        else:
            changes.append((name, "set" if old is None else "changed", new, old))

    old_items = before.mentioned_technologies
    new_items = after.mentioned_technologies
    for item in new_items:
        if item not in old_items:
            changes.append(("mentioned_technologies", "added", item, None))
    for item in old_items:
        if item not in new_items:
            changes.append(("mentioned_technologies", "removed", None, item))
    return changes


class ConversationHistory:
    """Historial de mensajes con ventana deslizante.

    - Un turno es un par (user, assistant). Se guarda completo o no se guarda,
      así nunca queda una respuesta sin su pregunta.
    - Conserva como máximo `max_turns` pares: al superar el límite, el par más
      antiguo se descarta (deque con maxlen).
    - El system prompt es invariante: siempre va primero en `to_messages_list()`.
      No se guarda en la ventana, sino que se regenera en cada llamada a partir
      del `project_metadata` actual. Así la memoria del proyecto llega al modelo
      aunque los turnos donde se mencionó ya hayan salido de la ventana.
    """

    def __init__(self, max_turns: int = MAX_TURNS):
        if max_turns < 1:
            raise ValueError("max_turns tiene que ser al menos 1.")
        self.max_turns = max_turns
        self._turns: deque[tuple[dict, dict]] = deque(maxlen=max_turns)

    def add_turn(self, user_content: str, assistant_content: str) -> None:
        self._turns.append(
            (
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": assistant_content},
            )
        )

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def to_messages_list(
        self,
        project_metadata: ProjectMetadata,
        build_system_prompt: Callable[[ProjectMetadata], str],
    ) -> list[dict]:
        """El array `messages` listo para la API: el system prompt regenerado con
        la metadata actual y, después, los pares de la ventana en orden."""
        messages = [
            {"role": "system", "content": build_system_prompt(project_metadata)}
        ]
        for user_message, assistant_message in self._turns:
            messages.extend([user_message, assistant_message])
        return messages

    def __len__(self) -> int:
        """Cantidad de mensajes en la ventana (sin contar el system prompt)."""
        return len(self._turns) * 2


@dataclass
class Session:
    session_id: str
    history: ConversationHistory
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Turnos completados en toda la sesión (la ventana solo guarda los últimos).
    total_turns: int = 0
    # Métricas de las llamadas al LLM del último turno (wrapper observable).
    last_llm_calls: list = field(default_factory=list)
    # Registro de auditoría: cada cambio de project_metadata con su turno y origen.
    metadata_log: list[MetadataChange] = field(default_factory=list)

    def record_metadata_change(
        self, before: ProjectMetadata, turn: int, sources: list[str]
    ) -> list[MetadataChange]:
        """Compara la ficha anterior con la actual y registra qué cambió."""
        changes = [
            MetadataChange(
                turn=turn,
                field=name,
                action=action,
                value=value,
                previous=previous,
                sources=sources,
            )
            for name, action, value, previous in diff_metadata(before, self.metadata)
        ]
        self.metadata_log.extend(changes)
        return changes

    # Piezas de memoria de la Sesión 6. Existen desde ya para que el endpoint de
    # debug tenga un contrato estable; se completan al implementar cada una.
    anchors: list[dict] = field(default_factory=list)
    summary: str = ""
    last_resolved_tier: str | None = None
    last_tier_rule: str | None = None


class SessionNotFoundError(LookupError):
    """No existe una sesión con ese id (o se perdió al reiniciar el servicio)."""


class SessionStore:
    """Sesiones indexadas por `session_id` en un diccionario del proceso."""

    def __init__(self, max_turns: int):
        self.max_turns = max_turns
        self._sessions: dict[str, Session] = {}
        # FastAPI atiende requests en varios hilos: el lock protege el diccionario.
        self._lock = threading.Lock()

    def create(self) -> Session:
        session = Session(
            session_id=uuid.uuid4().hex,
            history=ConversationHistory(max_turns=self.max_turns),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"No existe la sesión '{session_id}'.")
        return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


# Única instancia del proceso: todas las requests comparten el mismo diccionario.
session_store = SessionStore(max_turns=settings.session_max_turns)
