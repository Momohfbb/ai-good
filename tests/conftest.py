import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ultra.server import Services  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """OpenAI-compatible and Ollama endpoints answered from a script of replies."""

    def __init__(self):
        self.replies: list = []
        self.calls: list[dict] = []
        self.fail_openai = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.calls.append({"url": str(request.url), "body": body})
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.path.endswith("/chat/completions") and self.fail_openai:
            return httpx.Response(500, json={"error": "boom"})
        reply = self.replies.pop(0) if self.replies else "OK"
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}],
                                             "usage": {"prompt_tokens": 11, "completion_tokens": 7}})
        if request.url.path.endswith("/api/chat"):
            return httpx.Response(200, json={"message": {"content": reply}, "prompt_eval_count": 5, "eval_count": 3})
        return httpx.Response(404, json={})


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def make_services(tmp_path, fake_llm):
    def _make(**kw):
        http = httpx.AsyncClient(transport=httpx.MockTransport(kw.pop("handler", fake_llm.handler)))
        return Services(tmp_path / "data", http=http, **kw)
    return _make
