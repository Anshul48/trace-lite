"""LLM provider adapters."""

from typing import Protocol, Any


class LLMAdapter(Protocol):
    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        ...


class LiteLLMAdapter:
    """LiteLLM wrapper supporting Ollama, OpenAI, Anthropic, Gemini, etc."""

    def __init__(self, model: str = "ollama/llama3.1:8b", api_base: str | None = None, **kwargs):
        self.model = model
        self.api_base = api_base
        self.kwargs = kwargs

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        import litellm

        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            **self.kwargs,
        }
        if self.api_base:
            params["api_base"] = self.api_base

        response = litellm.completion(**params)
        content = response.choices[0].message.content
        return content.strip() if content else ""


class MockLLMAdapter:
    """Mock LLM adapter for offline unit testing."""

    def __init__(self, default_response: str = "This is a mock LLM summary of the text."):
        self.default_response = default_response
        self.last_prompt = ""

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        self.last_prompt = prompt
        # If prompt requests JSON array of scores (for LATTICE traversal)
        if "JSON array of scores" in prompt:
            # Count how many sections/items in prompt
            num_items = prompt.count("[") - 1
            if num_items <= 0:
                num_items = 3
            scores = [8, 5, 2][:num_items]
            while len(scores) < num_items:
                scores.append(1)
            return str(scores)
        # If prompt requests topic label (for Router)
        if "short 2-5 word topic label" in prompt:
            return "General Knowledge Topic"
        return self.default_response
