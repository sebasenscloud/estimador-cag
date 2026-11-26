"""Jobs de ingesta en memoria: pending → running → completed | failed.

En el curso viven en Postgres (tabla ingestion_jobs). Acá, como acordamos,
en memoria: misma interfaz (create, get, mark_running, mark_completed,
mark_failed), así que pasar a una base de datos es cambiar esta clase. Se
pierden al reiniciar el servicio, igual que las sesiones.

Diferencia propia: el job guarda también los Documents producidos, para
poder verlos (GET /ingestion/jobs/{id}/documents) mientras no haya índice.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.ingestion.documents.models import Document

JobStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class Job:
    job_id: uuid.UUID
    source_name: str
    status: JobStatus = "pending"
    documents_count: int = 0
    error_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    documents: list[Document] = field(default_factory=list)


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[uuid.UUID, Job] = {}
        # La ingesta corre en un hilo aparte (BackgroundTasks).
        self._lock = threading.Lock()

    def create(self, source_name: str) -> Job:
        job = Job(job_id=uuid.uuid4(), source_name=source_name)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: uuid.UUID) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def mark_running(self, job_id: uuid.UUID) -> None:
        self._update(job_id, status="running")

    def mark_completed(self, job_id: uuid.UUID, documents: list[Document]) -> None:
        self._update(
            job_id,
            status="completed",
            documents=documents,
            documents_count=len(documents),
            finished_at=datetime.now(UTC),
        )

    def mark_failed(self, job_id: uuid.UUID, error_message: str) -> None:
        self._update(
            job_id,
            status="failed",
            error_message=error_message[:500],
            finished_at=datetime.now(UTC),
        )

    def _update(self, job_id: uuid.UUID, **fields) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for name, value in fields.items():
                setattr(job, name, value)


job_store = InMemoryJobStore()
