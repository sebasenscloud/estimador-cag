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
