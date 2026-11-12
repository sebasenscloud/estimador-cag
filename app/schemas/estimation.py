from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ProjectType(str, Enum):
    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(str, Enum):
    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(str, Enum):
    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class EstimationRequest(BaseModel):
    description: str = Field(min_length=20, max_length=2000)
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat


class Phase(BaseModel):
    """Una fase del proyecto. Las fases son secuenciales."""

    name: str = Field(description="Nombre corto de la fase, ej. 'Discovery'.")
    description: str = Field(
        description="Qué se hace en esta fase, en una o dos frases."
    )
    duration_weeks: int = Field(ge=1, description="Duración de la fase en semanas.")
    cost_eur: int = Field(ge=0, description="Costo de la fase en euros.")
    confidence_pct: int = Field(
        ge=0, le=100, description="Confianza en la estimación de esta fase (0-100)."
    )


class EstimationResult(BaseModel):
    """Estimación estructurada que devuelve el LLM.

    El orden de los campos importa: el modelo genera primero el resumen y las
    fases, y recién después los totales, que tienen que cerrar con las fases.
    """

    summary: str = Field(description="Resumen del proyecto y del enfoque, 2-3 frases.")
    phases: list[Phase] = Field(min_length=1, description="Fases en orden.")
    total_duration_weeks: int = Field(
        ge=1, description="Suma de duration_weeks de todas las fases."
    )
    total_cost_eur: int = Field(
        ge=0, description="Suma de cost_eur de todas las fases."
    )
    confidence_pct: int = Field(
        ge=0, le=100, description="Confianza global en la estimación (0-100)."
    )

    @model_validator(mode="after")
    def phases_sum_matches_total(self) -> "EstimationResult":
        weeks = sum(phase.duration_weeks for phase in self.phases)
        cost = sum(phase.cost_eur for phase in self.phases)
        if weeks != self.total_duration_weeks:
            raise ValueError(
                f"total_duration_weeks es {self.total_duration_weeks}, "
                f"pero la suma de las fases da {weeks}."
            )
        if cost != self.total_cost_eur:
            raise ValueError(
                f"total_cost_eur es {self.total_cost_eur}, "
                f"pero la suma de las fases da {cost}."
            )
        return self


class EstimationResponse(BaseModel):
    result: EstimationResult
    prompt_version: str
    cached: bool


# Nombres legibles para mostrar en el formulario y usar en el prompt.
PROJECT_TYPE_LABELS = {
    ProjectType.MOBILE_APP: "Aplicación móvil",
    ProjectType.WEB_SAAS: "SaaS web",
    ProjectType.INTERNAL_TOOL: "Herramienta interna",
    ProjectType.DATA_PIPELINE: "Pipeline de datos",
}

DETAIL_LEVEL_LABELS = {
    DetailLevel.SUMMARY: "Resumen",
    DetailLevel.MEDIUM: "Intermedio",
    DetailLevel.DETAILED: "Detallado",
}

OUTPUT_FORMAT_LABELS = {
    OutputFormat.PHASES_TABLE: "Tabla por fases",
    OutputFormat.LINE_ITEMS: "Lista de ítems",
    OutputFormat.NARRATIVE: "Narrativa",
}
