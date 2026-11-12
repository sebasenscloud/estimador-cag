"""Cliente Streamlit del estimador: una conversación por sesión.

Al cargar la página se crea una sesión en el servicio IA (POST /sessions) y el
session_id se guarda en st.session_state. Cada envío va a
POST /sessions/{session_id}/estimate como multipart/form-data (transcripción,
campos tipados y adjuntos). El panel lateral muestra el project_metadata
(la memoria) separado de la conversación (el historial).
"""

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
    EstimationResult,
    OutputFormat,
    ProjectType,
    SessionEstimationResponse,
)
from app.sessions import ProjectMetadata

SESSIONS_URL = f"{settings.estimator_api_url}/api/v1/sessions"

ATTACHMENT_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def create_session() -> str:
    response = httpx.post(SESSIONS_URL, timeout=10)
    response.raise_for_status()
    return response.json()["session_id"]


def send_turn(
    session_id: str, request: EstimationRequest, files: list
) -> SessionEstimationResponse:
    """Manda un turno como multipart/form-data y valida la respuesta tipada."""
    response = httpx.post(
        f"{SESSIONS_URL}/{session_id}/estimate",
        data={
            "transcript": request.description,
            "project_type": request.project_type.value,
            "detail_level": request.detail_level.value,
            "output_format": request.output_format.value,
        },
        files=[
            (
                "attachments",
                (
                    file.name,
                    file.getvalue(),
                    ATTACHMENT_MIME_TYPES.get(file.name.rsplit(".", 1)[-1].lower(), ""),
                ),
            )
            for file in files
        ],
        timeout=settings.estimator_api_timeout,
    )
    response.raise_for_status()
    return SessionEstimationResponse.model_validate(response.json())


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


def start_new_conversation() -> None:
    """Pide una sesión nueva y resetea todo el estado de la conversación."""
    st.session_state.turns = []
    st.session_state.project_metadata = ProjectMetadata()
    st.session_state.last_attachments = []
    try:
        st.session_state.session_id = create_session()
        st.session_state.session_error = None
    except httpx.HTTPError:
        st.session_state.session_id = None
        st.session_state.session_error = (
            f"No se pudo crear la sesión en {settings.estimator_api_url}. "
            "¿Está corriendo el servicio IA? Inicialo con: "
            "`uv run uvicorn app.main:app --reload`"
        )


def render_estimation(result: EstimationResult, output_format: OutputFormat) -> None:
    """Dibuja los datos estructurados según el formato elegido."""
    if result.is_out_of_scope:
        # Respuesta degradada por el output guardrail (política FILTER).
        st.warning(result.summary)
        st.caption(f"Confianza: {result.confidence_pct} %. Siguiente paso sugerido:")
        for phase in result.phases:
            st.markdown(f"- **{phase.name}:** {phase.description}")
        return

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


def render_project_metadata(metadata: ProjectMetadata) -> None:
    if metadata == ProjectMetadata():
        st.caption("Vacía: se completa a medida que avanza la conversación.")
        return
    team = (
        f"{metadata.assumed_team_size} personas" if metadata.assumed_team_size else "—"
    )
    st.markdown(
        f"- **Proyecto:** {metadata.project_name or '—'}\n"
        f"- **Equipo asumido:** {team}\n"
        f"- **Tecnologías:** {', '.join(metadata.mentioned_technologies) or '—'}\n"
        f"- **Alcance acordado:** {metadata.agreed_scope or '—'}"
    )
    with st.expander("Ver JSON"):
        st.json(metadata.model_dump())


st.set_page_config(page_title="Estimador CAG", page_icon="🧮")
st.title("Estimador de proyectos de software")
st.caption(
    "Conversá sobre un proyecto: cada mensaje refina la estimación con la memoria "
    "de lo que ya se acordó."
)

# Al cargar la página (primera ejecución del script) se crea la sesión.
if "session_id" not in st.session_state:
    start_new_conversation()

if st.session_state.session_error:
    st.error(st.session_state.session_error)
    if st.button("Reintentar"):
        start_new_conversation()
        st.rerun()
    st.stop()

for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(turn["transcript"])
        if turn["attachments"]:
            st.caption("📎 " + ", ".join(turn["attachments"]))
    with st.chat_message("assistant"):
        render_estimation(turn["result"], turn["output_format"])

with st.form("turn_form", clear_on_submit=True):
    transcript = st.text_area(
        "Transcripción o mensaje",
        height=150,
        max_chars=2000,
        placeholder="Ej.: El cliente necesita una app para reservar turnos...",
    )
    files = st.file_uploader(
        "Adjuntos (PDF o Word)",
        type=["pdf", "docx"],
        accept_multiple_files=True,
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
    submitted = st.form_submit_button("Enviar", type="primary")

if submitted:
    try:
        request = EstimationRequest(
            description=transcript,
            project_type=project_type,
            detail_level=detail_level,
            output_format=output_format,
        )
    except ValidationError:
        st.warning("La transcripción tiene que tener entre 20 y 2000 caracteres.")
    else:
        try:
            with st.spinner("Estimando y actualizando la memoria del proyecto..."):
                response = send_turn(st.session_state.session_id, request, files or [])
        except httpx.ConnectError:
            st.error(
                "No se pudo conectar con el servicio IA en "
                f"{settings.estimator_api_url}. ¿Está corriendo?"
            )
        except httpx.TimeoutException:
            st.error("El servicio IA tardó demasiado en responder. Probá de nuevo.")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                st.error(
                    "La sesión ya no existe (el servicio se reinició y la memoria "
                    "vive en el proceso). Tocá «Nueva conversación» para empezar otra."
                )
            else:
                st.error(
                    f"El servicio IA respondió con error "
                    f"{error.response.status_code}: {error_detail(error.response)}"
                )
        else:
            st.session_state.turns.append(
                {
                    "transcript": request.description,
                    "attachments": [item.filename for item in response.attachments],
                    "result": response.result,
                    "output_format": request.output_format,
                }
            )
            st.session_state.project_metadata = response.project_metadata
            st.session_state.last_attachments = response.attachments
            st.rerun()

with st.sidebar:
    st.header("Sesión")
    st.caption(f"`{st.session_state.session_id}`")
    st.metric("Turnos en la conversación", len(st.session_state.turns))
    if st.button("Nueva conversación"):
        start_new_conversation()
        st.rerun()

    st.subheader("Memoria del proyecto")
    st.caption(
        "project_metadata: hechos que se conservan aunque la ventana del "
        "historial descarte turnos viejos."
    )
    render_project_metadata(st.session_state.project_metadata)

    if st.session_state.last_attachments:
        st.subheader("Adjuntos del último turno")
        for item in st.session_state.last_attachments:
            st.markdown(f"- **{item.filename}**: {item.extracted_chars:,} caracteres")
            if item.warning:
                st.warning(item.warning)

    st.caption(f"Servicio IA: {SESSIONS_URL}")
