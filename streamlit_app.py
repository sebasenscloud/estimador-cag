import time
from textwrap import dedent

import streamlit as st

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.logging_config import configure_logging
from app.services.llm_service import build_system_prompt, stream_estimation
from app.services.providers import LLMProviderError

configure_logging(settings.log_level, json_logs=settings.app_env == "production")

st.set_page_config(page_title="Estimador CAG", page_icon="🧮")
st.title("Estimador de proyectos de software")
st.caption(
    "Pegá la transcripción de una reunión con un cliente "
    "y te devuelvo una estimación de esfuerzo."
)

if "messages" not in st.session_state:
    st.session_state.messages = []

if "last_metrics" not in st.session_state:
    st.session_state.last_metrics = None

chat_area = st.container()

with chat_area:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

with st.form("transcription_form", clear_on_submit=True):
    transcription = st.text_area(
        "Transcripción de la reunión",
        height=180,
        placeholder="Pegá acá la transcripción de la reunión...",
    )
    submitted = st.form_submit_button("Enviar", type="primary")

if submitted and not transcription.strip():
    st.warning("Escribí o pegá una transcripción antes de enviar.")

if submitted and transcription.strip():
    st.session_state.messages.append({"role": "user", "content": transcription})
    with chat_area, st.chat_message("user"):
        st.markdown(transcription)

    with chat_area, st.chat_message("assistant"):
        metrics = {}
        start_time = time.perf_counter()
        try:
            answer = st.write_stream(stream_estimation(transcription, metrics))
            metrics["elapsed_seconds"] = time.perf_counter() - start_time
            st.session_state.last_metrics = metrics
        except ValueError as error:
            answer = f"⚠️ {error}"
            st.markdown(answer)
        except LLMProviderError:
            answer = (
                "⚠️ No se pudo conectar con el proveedor de LLM. "
                "Revisá tu API key y tu conexión."
            )
            st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})

with st.sidebar:
    st.header("Contexto CAG")

    st.subheader("System prompt activo")
    with st.expander("Ver system prompt"):
        st.code(build_system_prompt(), language=None, wrap_lines=True)

    st.subheader("Contexto estático inyectado")
    st.caption(f"{len(ESTIMATION_EXAMPLES)} estimaciones de ejemplo alimentan el CAG")
    for number, example in enumerate(ESTIMATION_EXAMPLES, start=1):
        with st.expander(f"Ejemplo {number}"):
            st.markdown("**Reunión**")
            st.write(example["meeting_summary"])
            st.markdown("**Estimación**")
            st.markdown(dedent(example["estimation"]))

    st.subheader("Métricas de la última llamada")
    last_metrics = st.session_state.last_metrics
    if last_metrics is None:
        st.info(
            "Todavía no hay llamadas. Enviá una transcripción para ver las métricas."
        )
    else:
        provider_column, cache_column = st.columns(2)
        provider_column.metric("Proveedor", last_metrics["provider"])
        cache_column.metric("Caché", "Acierto" if last_metrics["cache_hit"] else "—")
        st.metric("Modelo", last_metrics["model"])
        input_column, output_column = st.columns(2)
        input_column.metric("Tokens de entrada", f"{last_metrics['input_tokens']:,}")
        output_column.metric("Tokens de salida", f"{last_metrics['output_tokens']:,}")
        st.metric("Tiempo de respuesta", f"{last_metrics['elapsed_seconds']:.1f} s")
