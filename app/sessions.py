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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.config import settings


class ProjectMetadata(BaseModel):
    """Hechos relevantes del proyecto en curso. Vive aparte del historial."""

    project_name: str | None = None
    assumed_team_size: int | None = Field(default=None, ge=1)
    mentioned_technologies: list[str] = Field(default_factory=list)
    agreed_scope: str | None = None


class ConversationHistory:
    """Historial de mensajes con ventana deslizante.

    Conserva como máximo `max_turns` turnos (un turno empieza con un mensaje
    `user` y sigue con la respuesta `assistant`). Al superar el límite descarta
    los turnos más antiguos, pero el system prompt se preserva siempre: vive en
    su propio lugar, fuera de la ventana.
    """

    def __init__(self, max_turns: int, system_prompt: str = ""):
        if max_turns < 1:
            raise ValueError("max_turns tiene que ser al menos 1.")
        self.max_turns = max_turns
        self.system_prompt = system_prompt
        self._messages: list[dict] = []

    def set_system_prompt(self, system_prompt: str) -> None:
        """Reemplaza el system prompt (por ejemplo, con la metadata actualizada)."""
        self.system_prompt = system_prompt

    def add_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant_message(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    @property
    def turn_count(self) -> int:
        return sum(1 for message in self._messages if message["role"] == "user")

    def messages(self) -> list[dict]:
        """El array listo para la API: system primero y después la ventana."""
        if not self.system_prompt:
            return list(self._messages)
        return [{"role": "system", "content": self.system_prompt}, *self._messages]

    def _trim(self) -> None:
        # Descarta el turno más viejo completo (su user y lo que le siguió) hasta
        # volver al límite, así nunca queda un assistant sin su pregunta.
        while self.turn_count > self.max_turns:
            self._messages.pop(0)
            while self._messages and self._messages[0]["role"] != "user":
                self._messages.pop(0)

    def __len__(self) -> int:
        return len(self._messages)


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
