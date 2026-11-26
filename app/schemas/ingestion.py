"""Schemas HTTP de la ingesta: un request y tres respuestas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.documents.models import Document

JobStatus = Literal["pending", "running", "completed", "failed"]


class IngestionRunRequest(BaseModel):
    """Body de POST /api/v1/ingestion/runs."""

    model_config = ConfigDict(extra="forbid")

    source_name: str = Field(
        min_length=1,
        max_length=128,
        description="Fuente `include` del catálogo. Desconocida → 404; review/exclude → 400.",
    )


class IngestionRunResponse(BaseModel):
    """Respuesta 202 de POST /api/v1/ingestion/runs."""

    job_id: uuid.UUID
    source_name: str
    status: JobStatus


class IngestionJobView(BaseModel):
    """Respuesta de GET /api/v1/ingestion/jobs/{job_id}."""

    job_id: uuid.UUID
    source_name: str
    status: JobStatus
    documents_count: int
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class IngestionJobDocuments(BaseModel):
    """Respuesta de GET /api/v1/ingestion/jobs/{job_id}/documents."""

    job_id: uuid.UUID
    documents: list[Document]
