import json

import httpx2
import pytest
from conftest import run

from ultra.llm import BudgetExceeded, LLMError


def anthropic_mock(seen, text="مرحبا", stop="end_turn"):
    def handler(request):
        seen.append({"headers": dict(request.headers), "body": json.loads(request.content)})
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 500}})
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def test_no_provider_raises(make_services):
    svc = make_services()
    with pytest.raises(LLMError, match="no_model_configured"):
        run(svc.router.complete("chat", "hi"))


def test_claude_request_shape_and_cost(make_services):
    svc = make_services()
    svc.vault.set("anthropic_api_key", "sk-ant-test-000000000000")
    seen = []
    svc.router.anthropic_http = anthropic_mock(seen)
    res = run(svc.router.complete("coding", "write code"))
    assert res.text == "مرحبا" and res.provider == "anthropic" and res.model == "claude-opus-5-5"
    body = seen[0]["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"effort": "high"}          # coding route effort
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0]["headers"].get("anthropic-beta", "")
    assert res.cost_usd == pytest.approx(1000 / 1e6 * 4 + 500 / 1e6 * 20)
    assert svc.router.month_spend() == pytest.approx(res.cost_usd)


def test_refusal_falls_through_to_next_provider(make_services, fake_llm):
    svc = make_services()
    svc.vault.set("anthropic_api_key", "sk-ant-test-000000000000")
    svc.vault.set("openai_api_key", "sk-test-1234567890abcdef")
    svc.router.anthropic_http = anthropic_mock([], text="", stop="refusal")
    fake_llm.replies = ["from openai"]
    res = run(svc.router.complete("chat", "hi"))
    assert res.provider == "openai" and res.text == "from openai"
    # OpenAI spend counts toward the monthly budget (11 in, 7 out at 1.25/10 per million).
    assert res.cost_usd == round(11 / 1e6 * 1.25 + 7 / 1e6 * 10.0, 6)
    assert svc.router.month_spend() > 0


def test_fallback_on_error_then_ollama(make_services, fake_llm):
    svc = make_services()
    svc.vault.set("openai_api_key", "sk-test-1234567890abcdef")
    run(svc.router.probe_ollama())
    assert svc.router.provider_status()["ollama"]
    fake_llm.fail_openai = True
    fake_llm.replies = ["local answer"]
    res = run(svc.router.complete("chat", "hi"))
    assert res.provider == "ollama" and res.text == "local answer"
    errors = svc.db.one("SELECT COUNT(*) n FROM usage WHERE ok=0")["n"]
    assert errors == 1


def test_budget_blocks_paid_models(make_services):
    svc = make_services()
    svc.vault.set("anthropic_api_key", "sk-ant-test-000000000000")
    svc.db.set_setting("monthly_budget_usd", 0.001)
    svc.router.anthropic_http = anthropic_mock([])
    run(svc.router.complete("chat", "hi"))          # first call spends ~$0.014
    with pytest.raises(BudgetExceeded):
        run(svc.router.complete("chat", "again"))
