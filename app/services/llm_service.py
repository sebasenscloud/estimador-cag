from collections.abc import Iterator

from anthropic import Anthropic

from app.config import settings
from app.context.examples import ESTIMATION_EXAMPLES

client = Anthropic(api_key=settings.anthropic_api_key)


def build_system_prompt() -> str:
    examples_text = ""
    for number, example in enumerate(ESTIMATION_EXAMPLES, start=1):
        examples_text += f"""
                        <ejemplo numero="{number}">
                        <reunion>
                        {example["meeting_summary"]}
                        </reunion>
                        <estimacion>
                        {example["estimation"]}
                        </estimacion>
                        </ejemplo>
                        """

    return f"""Sos un estimador experto de proyectos de software. Tu trabajo es leer \
            la transcripción de una reunión con un cliente y generar una estimación de esfuerzo.

            Basate en las estimaciones previas que aparecen abajo como referencia: usá el mismo \
            formato (desglose de tareas con horas, total estimado, equipo recomendado y duración) \
            y un criterio de horas coherente con ellas. Si la transcripción no tiene información \
            suficiente para estimar alguna parte, indicalo y explicá qué supuestos tomaste.

            <ejemplos>
            {examples_text}
            </ejemplos>"""


### Armo el prompt para luego generar la estimación
def generate_estimation(transcript: str) -> dict:
    response = client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.max_tokens,
        system=build_system_prompt(),
        messages=[
            {"role": "user", "content": transcript},
        ],
    )

    if response.stop_reason == "refusal":
        raise ValueError(
            "El modelo no pudo generar la estimación sobre la transcripción enviada."
        )

    estimation = "".join(
        block.text for block in response.content if block.type == "text"
    )

    return {
        "estimation": estimation,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


def stream_estimation(transcript: str) -> Iterator[str]:
    with client.messages.stream(
        model=settings.llm_model,
        max_tokens=settings.max_tokens,
        system=build_system_prompt(),
        messages=[
            {"role": "user", "content": transcript},
        ],
    ) as stream:
        yield from stream.text_stream
        final_message = stream.get_final_message()

    if final_message.stop_reason == "refusal":
        raise ValueError(
            "El modelo no pudo generar la estimación sobre la transcripción enviada."
        )
