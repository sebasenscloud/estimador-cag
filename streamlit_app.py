import anthropic
import streamlit as st

from app.services.llm_service import stream_estimation

st.set_page_config(page_title="Estimador CAG", page_icon="🧮")
st.title("Estimador de proyectos de software")
st.caption(
    "Pegá la transcripción de una reunión con un cliente "
    "y te devuelvo una estimación de esfuerzo."
)

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

transcription = st.chat_input("Pegá acá la transcripción de la reunión...")

if transcription:
    st.session_state.messages.append({"role": "user", "content": transcription})
    with st.chat_message("user"):
        st.markdown(transcription)

    with st.chat_message("assistant"):
        try:
            answer = st.write_stream(stream_estimation(transcription))
        except ValueError as error:
            answer = f"⚠️ {error}"
            st.markdown(answer)
        except anthropic.APIError:
            answer = (
                "⚠️ No se pudo conectar con el proveedor de LLM. "
                "Revisá tu API key y tu conexión."
            )
            st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
