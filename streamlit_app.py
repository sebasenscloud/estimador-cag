import time

import httpx
import streamlit as st
from pydantic import ValidationError

from app.config import settings
from app.schemas import (
    DETAIL_LEVEL_LABELS,
    OUTPUT_FORMAT_LABELS,
    PROJECT_TYPE_LABELS,
    DetailLevel,
    EstimationRequest,
    EstimationResponse,
    EstimationResult,
    OutputFormat,
    ProjectType,
)

ESTIMATE_URL = f"{settings.estimator_api_url}/api/v1/estimate"


def request_estimation(request: EstimationRequest) -> EstimationResponse:
    """Envía el formulario al servicio IA y devuelve su respuesta tipada."""
    response = httpx.post(
        ESTIMATE_URL,
        json=request.model_dump(mode="json"),
        timeout=settings.estimator_api_timeout,
    )
    response.raise_for_status()
    return EstimationResponse.model_validate(response.json())


def error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return response.text
    if isinstance(detail, list):
        return "; ".join(item.get("msg", str(item)) for item in detail)
    return str(detail)


def euros(amount: int) -> str:
    return f"{amount:,} €".replace(",", ".")


def render_estimation(result: EstimationResult, output_format: OutputFormat) -> None:
    """Dibuja los datos estructurados según el formato elegido en el formulario."""
    st.subheader("Estimación")
    weeks_column, cost_column, confidence_column = st.columns(3)
    weeks_column.metric("Duración total", f"{result.total_duration_weeks} semanas")
    cost_column.metric("Costo total", euros(result.total_cost_eur))
    confidence_column.metric("Confianza", f"{result.confidence_pct} %")
    st.write(result.summary)

    if output_format == OutputFormat.PHASES_TABLE:
        st.dataframe(
            [
                {
                    "Fase": phase.name,
                    "Semanas": phase.duration_weeks,
                    "Costo": euros(phase.cost_eur),
                    "Confianza": f"{phase.confidence_pct} %",
                    "Descripción": phase.description,
                }
                for phase in result.phases
            ],
            hide_index=True,
        )
    elif output_format == OutputFormat.LINE_ITEMS:
        st.markdown(
            "\n".join(
                f"- **{phase.name}** ({phase.duration_weeks} sem., "
                f"{euros(phase.cost_eur)}, confianza {phase.confidence_pct} %): "
                f"{phase.description}"
                for phase in result.phases
            )
        )
    else:
        for number, phase in enumerate(result.phases, start=1):
            st.markdown(
                f"**{number}. {phase.name}.** {phase.description} Esta fase lleva "
                f"{phase.duration_weeks} semanas, cuesta {euros(phase.cost_eur)} "
                f"y la estimamos con una confianza del {phase.confidence_pct} %."
            )


st.set_page_config(page_title="Estimador CAG", page_icon="🧮")
st.title("Estimador de proyectos de software")
st.caption(
    "Describí el proyecto, elegí cómo querés la estimación y enviá el formulario."
)

if "last_result" not in st.session_state:
    st.session_state.last_result = None

with st.form("estimation_form"):
    description = st.text_area(
        "Descripción del proyecto",
        height=180,
        max_chars=2000,
        placeholder="Ej.: El cliente necesita una app para reservar turnos...",
    )
    project_type = st.selectbox(
        "Tipo de proyecto",
        options=list(ProjectType),
        format_func=lambda option: PROJECT_TYPE_LABELS[ProjectType(option)],
    )
    detail_column, format_column = st.columns(2)
    detail_level = detail_column.selectbox(
        "Nivel de detalle",
        options=list(DetailLevel),
        index=1,
        format_func=lambda option: DETAIL_LEVEL_LABELS[DetailLevel(option)],
    )
    output_format = format_column.selectbox(
        "Formato de salida",
        options=list(OutputFormat),
        format_func=lambda option: OUTPUT_FORMAT_LABELS[OutputFormat(option)],
    )
    submitted = st.form_submit_button("Estimar", type="primary")

if submitted:
    try:
        request = EstimationRequest(
            description=description,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
        )
    except ValidationError:
        st.warning("La descripción tiene que tener entre 20 y 2000 caracteres.")
    else:
        start_time = time.perf_counter()
        try:
            with st.spinner("Generando estimación..."):
                result = request_estimation(request)
        except httpx.ConnectError:
            st.error(
                "No se pudo conectar con el servicio IA en "
                f"{settings.estimator_api_url}. ¿Está corriendo? Inicialo con: "
                "`uv run uvicorn app.main:app --reload`"
            )
        except httpx.TimeoutException:
            st.error("El servicio IA tardó demasiado en responder. Probá de nuevo.")
        except httpx.HTTPStatusError as error:
            st.error(
                f"El servicio IA respondió con error {error.response.status_code}: "
                f"{error_detail(error.response)}"
            )
        else:
            st.session_state.last_result = {
                "request": request,
                "response": result,
                "elapsed_seconds": time.perf_counter() - start_time,
            }

last_result = st.session_state.last_result

if last_result is not None:
    render_estimation(
        last_result["response"].result, last_result["request"].output_format
    )

with st.sidebar:
    st.header("Última estimación")
    if last_result is None:
        st.info("Todavía no hay estimaciones. Completá el formulario y enviálo.")
    else:
        sent: EstimationRequest = last_result["request"]
        version_column, time_column = st.columns(2)
        version_column.metric(
            "Versión del prompt", last_result["response"].prompt_version
        )
        time_column.metric("Tiempo", f"{last_result['elapsed_seconds']:.1f} s")
        st.metric(
            "Caché", "Acierto" if last_result["response"].cached else "Llamada al LLM"
        )

        st.subheader("Parámetros enviados")
        st.markdown(
            f"- **Tipo de proyecto:** {PROJECT_TYPE_LABELS[sent.project_type]}\n"
            f"- **Nivel de detalle:** {DETAIL_LEVEL_LABELS[sent.detail_level]}\n"
            f"- **Formato de salida:** {OUTPUT_FORMAT_LABELS[sent.output_format]}"
        )
        with st.expander("Ver JSON enviado al servicio"):
            st.json(sent.model_dump(mode="json"))

    st.caption(f"Servicio IA: {ESTIMATE_URL}")
