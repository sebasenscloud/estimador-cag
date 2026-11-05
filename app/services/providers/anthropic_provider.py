from collections.abc import Iterator

import anthropic

from app.services.providers.base import (
    REFUSAL_MESSAGE,
    LLMProvider,
    LLMProviderError,
    LLMResult,
)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int):
        super().__init__(model, max_tokens)
        self._client = anthropic.Anthropic(api_key=api_key)

    def generate(self, system_prompt: str, user_message: str) -> LLMResult:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIError as error:
            raise LLMProviderError(str(error)) from error

        if response.stop_reason == "refusal":
            raise ValueError(REFUSAL_MESSAGE)

        return LLMResult(
            text="".join(
                block.text for block in response.content if block.type == "text"
            ),
            provider=self.name,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def stream(
        self, system_prompt: str, user_message: str, usage: dict
    ) -> Iterator[str]:
        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            ) as stream:
                yield from stream.text_stream
                final_message = stream.get_final_message()
        except anthropic.APIError as error:
            raise LLMProviderError(str(error)) from error

        if final_message.stop_reason == "refusal":
            raise ValueError(REFUSAL_MESSAGE)

        usage["model"] = final_message.model
        usage["input_tokens"] = final_message.usage.input_tokens
        usage["output_tokens"] = final_message.usage.output_tokens
