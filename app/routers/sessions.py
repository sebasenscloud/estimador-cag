import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from app.sessions import session_store

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class SessionCreated(BaseModel):
    session_id: str


@router.post("", response_model=SessionCreated, status_code=201)
def create_session() -> SessionCreated:
    """Crea una sesión vacía. El cliente reenvía el session_id en cada petición
    posterior para reutilizar el historial y la memoria del proyecto."""
    session = session_store.create()
    logger.info("session_created", session_id=session.session_id)
    return SessionCreated(session_id=session.session_id)
