"""POST /api/v1/ingestion/runs + GET /api/v1/ingestion/jobs/{job_id}.

La ingesta es offline: indexar tarda y la petición no espera. El router es
fino a propósito: valida la fuente contra el catálogo, crea el job, lanza el
orquestador como BackgroundTask y responde 202 en el acto. Nunca importa
parsers: toda la lógica vive en app/ingestion/orchestrator.py.

El catálogo manda: una fuente `review` o `exclude` responde 400 con el
motivo escrito en el YAML.

Portado de app/routers/ingestion.py del curso (jobs en memoria).
"""

import uuid
from functools import lru_cache
from typing import Annotated

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.config import settings
from app.ingestion.catalog import DataCatalog, load_catalog
from app.ingestion.jobs import InMemoryJobStore, job_store
from app.ingestion.loaders.filesystem import FileSystemLoader
from app.ingestion.orchestrator import (
    IngestionRejected,
    check_ingestable,
    ingest_source,
)
from app.ingestion.parsers.registry import ParserRegistry, default_registry
from app.schemas.ingestion import (
    IngestionJobDocuments,
    IngestionJobView,
    IngestionRunRequest,
    IngestionRunResponse,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


@lru_cache
def get_catalog() -> DataCatalog:
    """El catálogo se carga y valida una sola vez (es código versionado)."""
    return load_catalog(settings.catalog_path)


def get_loader() -> FileSystemLoader:
    return FileSystemLoader(settings.ingestion_data_root)


@lru_cache
def get_registry() -> ParserRegistry:
    return default_registry()


def get_jobs() -> InMemoryJobStore:
    return job_store


CatalogDep = Annotated[DataCatalog, Depends(get_catalog)]
LoaderDep = Annotated[FileSystemLoader, Depends(get_loader)]
RegistryDep = Annotated[ParserRegistry, Depends(get_registry)]
JobsDep = Annotated[InMemoryJobStore, Depends(get_jobs)]


def _run_in_background(job_id: uuid.UUID, source_name: str, **deps) -> None:
    try:
        ingest_source(job_id=job_id, source_name=source_name, **deps)
    except Exception as error:  # noqa: BLE001 — el orquestador ya marcó el job
        logger.error(
            "ingestion_background_failed",
            job_id=str(job_id),
            source_name=source_name,
            error=str(error)[:300],
        )


@router.post("/runs", response_model=IngestionRunResponse, status_code=202)
def create_ingestion_run(
    request: IngestionRunRequest,
    background: BackgroundTasks,
    catalog: CatalogDep,
    loader: LoaderDep,
    registry: RegistryDep,
    jobs: JobsDep,
) -> IngestionRunResponse:
    try:
        source = check_ingestable(catalog, request.source_name)
    except IngestionRejected as rejected:
        if rejected.reason == "unknown_source":
            raise HTTPException(
                status_code=404,
                detail={"reason": rejected.reason, "source_name": request.source_name},
            ) from rejected
        source = catalog.find(request.source_name)
        raise HTTPException(
            status_code=400,
            detail={
                "reason": rejected.reason,
                "source_name": request.source_name,
                "decision": source.decision.value,
                "decision_reason": " ".join(source.decision_reason.split()),
            },
        ) from rejected

    job = jobs.create(source.name)
    background.add_task(
        _run_in_background,
        job.job_id,
        source.name,
        catalog=catalog,
        loader=loader,
        registry=registry,
        jobs=jobs,
    )
    return IngestionRunResponse(
        job_id=job.job_id, source_name=job.source_name, status=job.status
    )


def _get_job(job_id: uuid.UUID, jobs: InMemoryJobStore):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    return job


@router.get("/jobs/{job_id}", response_model=IngestionJobView)
def get_ingestion_job(job_id: uuid.UUID, jobs: JobsDep) -> IngestionJobView:
    job = _get_job(job_id, jobs)
    return IngestionJobView(
        job_id=job.job_id,
        source_name=job.source_name,
        status=job.status,
        documents_count=job.documents_count,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/jobs/{job_id}/documents", response_model=IngestionJobDocuments)
def get_ingestion_job_documents(
    job_id: uuid.UUID, jobs: JobsDep
) -> IngestionJobDocuments:
    """Propio (no está en el curso): ver los Documents mientras no hay índice."""
    job = _get_job(job_id, jobs)
    return IngestionJobDocuments(job_id=job.job_id, documents=job.documents)
