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


@dataclass
class Turn:
    number: int
    user: dict
    assistant: dict
    # Reglas que anclaron el turno (ver app/anchors.py); vacía si no es ancla.
    anchor_rules: list[str] = field(default_factory=list)

    @property
    def is_anchor(self) -> bool:
        return bool(self.anchor_rules)


class ConversationHistory:
    """Historial de mensajes con ventana deslizante y anclas.

    - Un turno es un par (user, assistant). Se guarda completo o no se guarda,
      así nunca queda una respuesta sin su pregunta.
    - La ventana conserva los últimos `max_turns` turnos. Cuando un turno sale:
        - si es ancla, pasa a la lista de anclas y sigue viajando al LLM;
        - si no, se descarta (y `add_turn` lo devuelve, para quien quiera
          resumirlo).
    - Las anclas también tienen un límite (`max_anchors`): si se supera, sale
      la más vieja. Un ancla de más queda en el prompt para siempre.
    - El system prompt es invariante: siempre va primero en `to_messages_list()`
      y se regenera en cada llamada a partir del `project_metadata` actual.
    """

    def __init__(self, max_turns: int = MAX_TURNS, max_anchors: int = 5):
        if max_turns < 1:
            raise ValueError("max_turns tiene que ser al menos 1.")
        self.max_turns = max_turns
        self.max_anchors = max_anchors
        self._window: deque[Turn] = deque()
        self._anchored: list[Turn] = []
        self._next_number = 1

    def add_turn(
        self,
        user_content: str,
        assistant_content: str,
        anchor_rules: list[str] | None = None,
    ) -> Turn | None:
        """Agrega un turno. Devuelve el turno no anclado que salió de la ventana."""
        self._window.append(
            Turn(
                number=self._next_number,
                user={"role": "user", "content": user_content},
                assistant={"role": "assistant", "content": assistant_content},
                anchor_rules=list(anchor_rules or []),
            )
        )
        self._next_number += 1
        if len(self._window) <= self.max_turns:
            return None

        oldest = self._window.popleft()
        if not oldest.is_anchor:
            return oldest
        self._anchored.append(oldest)
        if len(self._anchored) > self.max_anchors:
            self._anchored.pop(0)
        return None

    @property
    def turn_count(self) -> int:
        """Turnos dentro de la ventana."""
        return len(self._window)

    @property
    def anchor_count(self) -> int:
        """Turnos anclados que se conservan, dentro o fuera de la ventana."""
        in_window = sum(1 for turn in self._window if turn.is_anchor)
        return len(self._anchored) + in_window

    @property
    def anchored_turns(self) -> list[Turn]:
        """Anclas que ya salieron de la ventana y se conservan aparte."""
        return list(self._anchored)

    def to_messages_list(
        self,
        project_metadata: ProjectMetadata,
        build_system_prompt: Callable[[ProjectMetadata], str],
    ) -> list[dict]:
        """El array `messages` para la API: system regenerado con la metadata
        actual, después las anclas que salieron de la ventana y al final la
        ventana, todo en orden cronológico."""
        messages = [
            {"role": "system", "content": build_system_prompt(project_metadata)}
        ]
        for turn in [*self._anchored, *self._window]:
            messages.extend([turn.user, turn.assistant])
        return messages

    def __len__(self) -> int:
        """Mensajes que viajan al LLM (sin contar el system prompt)."""
        return (len(self._window) + len(self._anchored)) * 2


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

    # Piezas de memoria de la Sesión 6 que todavía no están implementadas.
    # Existen desde ya para que el endpoint de debug tenga un contrato estable.
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
            history=ConversationHistory(
                max_turns=self.max_turns, max_anchors=settings.max_anchors
            ),
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
