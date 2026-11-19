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


SUMMARY_HEADER = "[Earlier conversation summary]"


class ConversationHistory:
    """Historial con compresión híbrida: resumen + anclas + ventana.

    - Un turno es un par (user, assistant). Se guarda completo o no se guarda.
    - `append()` solo agrega: no recorta. El servicio llama justo después a
      `apply_compression()` (app/compression.py), así quien lee el código ve
      cuándo se paga la llamada al LLM que resume.
    - Al comprimir, los turnos que exceden la ventana (`max_turns`):
        - si son ancla, pasan a la lista de anclas y siguen literales;
        - si no, se condensan en el resumen acumulado.
    - Las anclas tienen su propio límite (`max_anchors`): el exceso también va
      al resumen, nunca se pierde en silencio.
    - El system prompt es invariante: siempre va primero en `to_messages_list()`
      y se regenera en cada llamada a partir del `project_metadata` actual.
    """

    def __init__(self, max_turns: int = MAX_TURNS, max_anchors: int = 5):
        if max_turns < 1:
            raise ValueError("max_turns tiene que ser al menos 1.")
        self.max_turns = max_turns
        self.max_anchors = max_anchors
        self.summary = ""
        self._window: list[Turn] = []
        self._anchored: list[Turn] = []
        self._next_number = 1

    def append(
        self,
        user_content: str,
        assistant_content: str,
        anchor_rules: list[str] | None = None,
    ) -> Turn:
        """Agrega un turno al final de la ventana. No recorta nada."""
        turn = Turn(
            number=self._next_number,
            user={"role": "user", "content": user_content},
            assistant={"role": "assistant", "content": assistant_content},
            anchor_rules=list(anchor_rules or []),
        )
        self._window.append(turn)
        self._next_number += 1
        return turn

    def _plan(self) -> tuple[list[Turn], list[Turn], list[Turn]]:
        """(ventana que queda, anclas que quedan, turnos que van al resumen)."""
        excess = max(0, len(self._window) - self.max_turns)
        overflow, window = self._window[:excess], self._window[excess:]
        anchors = self._anchored + [turn for turn in overflow if turn.is_anchor]
        kept_anchors = anchors[-self.max_anchors :] if self.max_anchors else []
        dropped_anchors = anchors[: len(anchors) - len(kept_anchors)]
        to_summarize = sorted(
            [turn for turn in overflow if not turn.is_anchor] + dropped_anchors,
            key=lambda turn: turn.number,
        )
        return window, kept_anchors, to_summarize

    def turns_to_summarize(self) -> list[Turn]:
        """Lo que la próxima compresión va a condensar (sin modificar nada)."""
        return self._plan()[2]

    def needs_compression(self) -> bool:
        return len(self._window) > self.max_turns

    def compact(self, new_summary: str | None = None) -> list[Turn]:
        """Aplica la compresión: recorta la ventana, mueve anclas y reemplaza el
        resumen. Devuelve los turnos que quedaron condensados en el resumen."""
        window, kept_anchors, to_summarize = self._plan()
        self._window, self._anchored = window, kept_anchors
        if new_summary is not None:
            self.summary = new_summary
        return to_summarize

    @property
    def turn_count(self) -> int:
        """Turnos dentro de la ventana."""
        return len(self._window)

    @property
    def anchor_count(self) -> int:
        """Mensajes anclados que ya salieron de la ventana y se conservan aparte."""
        return len(self._anchored) * 2

    @property
    def anchored_turns(self) -> list[Turn]:
        return list(self._anchored)

    def to_messages_list(
        self,
        project_metadata: ProjectMetadata,
        build_system_prompt: Callable[[ProjectMetadata], str],
    ) -> list[dict]:
        """El array `messages` para la API, en este orden:

        1. system, regenerado con la metadata actual;
        2. el resumen acumulado, como mensaje user (un segundo system rompería el
           contrato; el encabezado entre corchetes le indica al modelo que es
           contexto, no un pedido);
        3. las anclas que salieron de la ventana, literales;
        4. la ventana.
        """
        messages = [
            {"role": "system", "content": build_system_prompt(project_metadata)}
        ]
        if self.summary:
            messages.append(
                {"role": "user", "content": f"{SUMMARY_HEADER}\n{self.summary}"}
            )
        for turn in [*self._anchored, *self._window]:
            messages.extend([turn.user, turn.assistant])
        return messages

    def __len__(self) -> int:
        """Mensajes de la ventana (sin system, resumen ni anclas)."""
        return len(self._window) * 2


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

    # Pieza de la Sesión 6 que todavía no está implementada. Existe desde ya
    # para que el endpoint de debug tenga un contrato estable.
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
