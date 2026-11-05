from collections.abc import Iterator

import openai

from app.services.providers.base import (
    REFUSAL_MESSAGE,
    LLMProvider,
    LLMProviderError,
    LLMResult,
)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, max_tokens: int):
        super().__init__(model, max_tokens)
        self._client = openai.OpenAI(api_key=api_key)

    def _messages(self, system_prompt: str, user_message: str) -> list[dict]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def generate(self, system_prompt: str, user_message: str) -> LLMResult:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=self.max_tokens,
                messages=self._messages(system_prompt, user_message),
            )
        except openai.APIError as error:
            raise LLMProviderError(str(error)) from error

        choice = response.choices[0]
        if choice.message.refusal or choice.finish_reason == "content_filter":
            raise ValueError(REFUSAL_MESSAGE)

        return LLMResult(
            text=choice.message.content or "",
            provider=self.name,
            model=response.model,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )

    def stream(
        self, system_prompt: str, user_message: str, usage: dict
    ) -> Iterator[str]:
        refused = False
        try:
            chunks = self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=self.max_tokens,
                messages=self._messages(system_prompt, user_message),
                stream=True,
                stream_options={"include_usage": True},
            )
            for chunk in chunks:
                if chunk.usage is not None:
                    usage["model"] = chunk.model
                    usage["input_tokens"] = chunk.usage.prompt_tokens
                    usage["output_tokens"] = chunk.usage.completion_tokens
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.delta.refusal or choice.finish_reason == "content_filter":
                    refused = True
                if choice.delta.content:
                    yield choice.delta.content
        except openai.APIError as error:
            raise LLMProviderError(str(error)) from error

        if refused:
            raise ValueError(REFUSAL_MESSAGE)
