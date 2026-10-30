"""Model providers and the router that picks a model for each kind of task.

Providers:
  anthropic  Claude models, through the official `anthropic` SDK (needs an API key)
  openai     OpenAI or any OpenAI-compatible endpoint (OpenRouter, LM Studio, Gemini...)
  ollama     local models on this PC, free and offline (needs Ollama installed)
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field

import httpx

from .db import Database
from .security import Vault

# USD per million tokens (input, output). Unknown models are logged with cost 0.
PRICING = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
WEB_SEARCH_USD = 0.01  # per search


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    refused: bool = False
    sources: list = field(default_factory=list)


@dataclass
class Image:
    data: bytes
    media_type: str = "image/jpeg"


class Router:
    def __init__(self, db: Database, vault: Vault, http: httpx.AsyncClient | None = None,
                 local_http: httpx.AsyncClient | None = None):
        self.db = db
        self.vault = vault
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10))
        # Ollama is on this PC: never send it through a system/VPN proxy (Windows proxy settings
        # are picked up by default and break connections to 127.0.0.1).
        self.local_http = local_http or (self.http if http else
                                         httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), trust_env=False))
        self._anthropic = None
        self._anthropic_key = None
        self.anthropic_http = None   # optional custom HTTP client (tests, proxies)
        self.pulls: dict[str, dict] = {}   # Ollama model downloads started from the dashboard

    # ------------------------------------------------------------ availability
    def provider_status(self) -> dict:
        return {
            "anthropic": bool(self.vault.get("anthropic_api_key")),
            "openai": bool(self.vault.get("openai_api_key")),
            "ollama": bool(self.db.get_setting("ollama_available", False)),
        }

    async def probe_ollama(self) -> bool:
        url = self.db.get_setting("ollama_url").rstrip("/")
        alt = {"127.0.0.1": "localhost", "localhost": "127.0.0.1"}.get(httpx.URL(url).host)
        tries = [url] + ([url.replace(httpx.URL(url).host, alt, 1)] if alt else [])
        ok, models, error = False, [], ""
        for u in tries:
            try:
                r = await self.local_http.get(f"{u}/api/tags", timeout=3)
                if r.status_code != 200:
                    error = f"{u}: HTTP {r.status_code}"
                    continue
                ok, models = True, [m["name"] for m in r.json().get("models", [])]
                if u != url:
                    self.db.set_setting("ollama_url", u)   # the other spelling of this PC works
                break
            except Exception as e:
                error = f"{u}: {type(e).__name__} {str(e)[:150]}"
        self.db.set_setting("ollama_available", ok)
        self.db.set_setting("ollama_models", models)
        self.db.set_setting("ollama_error", "" if ok else error)
        return ok

    @staticmethod
    def recommended_ollama(ram_bytes: int | None = None) -> str:
        """Best general local model for this PC's memory."""
        if ram_bytes is None:
            import psutil
            ram_bytes = psutil.virtual_memory().total
        return "qwen3:8b" if ram_bytes >= 12 * 1024**3 else "qwen3:4b"

    OLLAMA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,100}$")

    async def pull_ollama(self, model: str) -> dict:
        """Download a model into the local Ollama, recording progress in self.pulls[model]."""
        p = self.pulls[model] = {"model": model, "status": "starting", "completed": 0, "total": 0,
                                 "done": False, "ok": False, "error": ""}
        url = self.db.get_setting("ollama_url")
        try:
            async with self.local_http.stream("POST", f"{url}/api/pull", json={"model": model, "name": model, "stream": True},
                                        timeout=httpx.Timeout(None, connect=10)) as r:
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {(await r.aread())[:200].decode(errors='replace')}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    j = json.loads(line)
                    if j.get("error"):
                        raise LLMError(j["error"])
                    p["status"] = j.get("status", p["status"])
                    if j.get("total"):
                        p["total"], p["completed"] = j["total"], j.get("completed", 0)
            # Verify: the model must now be listed by Ollama.
            await self.probe_ollama()
            installed = self.db.get_setting("ollama_models", [])
            if model not in installed and f"{model}:latest" not in installed:
                raise LLMError("model not listed by Ollama after download")
            p["ok"] = True
        except httpx.ConnectError:
            p["error"] = "ollama_not_running"
        except Exception as e:
            p["error"] = str(e)[:300]
        finally:
            p["done"] = True
        return p

    def _installed_ollama(self, model: str) -> str:
        """The configured model if it is installed, otherwise the first installed one (e.g. qwen3:4b
        on a PC with less memory), so a local model works whichever one the user downloaded."""
        installed = self.db.get_setting("ollama_models", []) or []
        if not installed:
            return model   # list unknown: let Ollama answer for itself
        for name in (model, f"{model}:latest"):
            if name in installed:
                return name
        general = [m for m in installed if not any(k in m for k in ("vl", "coder", "embed", "vision"))]
        return (general or installed)[0]

    def any_available(self) -> bool:
        return any(self.provider_status().values())

    def candidates(self, route: str) -> list[tuple[str, str]]:
        routes = self.db.get_setting("routes") or {}
        status = self.provider_status()
        out = []
        for c in routes.get(route) or routes.get("chat") or []:
            provider, _, model = c.partition(":")
            if provider == "ollama" and status.get(provider):
                model = self._installed_ollama(model)
                if not model:
                    continue
            if status.get(provider) and (provider, model) not in out:
                out.append((provider, model))
        return out

    # ------------------------------------------------------------ cost
    def month_spend(self) -> float:
        start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d"))
        row = self.db.one("SELECT COALESCE(SUM(cost_usd),0) s FROM usage WHERE ts>=?", (start,))
        return round(row["s"], 4)

    def _record(self, res: LLMResult | None, provider: str, model: str, route: str, ok: bool) -> None:
        self.db.execute("INSERT INTO usage(ts,provider,model,route,input_tokens,output_tokens,cost_usd,ok) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (time.time(), provider, model, route, res.input_tokens if res else 0,
                         res.output_tokens if res else 0, res.cost_usd if res else 0, int(ok)))

    # ------------------------------------------------------------ main entry
    async def complete(self, route: str, prompt: str, system: str = "", images: list[Image] | None = None,
                       history: list[dict] | None = None, max_tokens: int = 16000) -> LLMResult:
        cands = self.candidates(route)
        if not cands:
            raise LLMError("no_model_configured")
        errors = []
        for provider, model in cands:
            if provider != "ollama":
                budget = float(self.db.get_setting("monthly_budget_usd", 0) or 0)
                if budget and self.month_spend() >= budget:
                    errors.append(f"{provider}: monthly budget reached")
                    continue
            try:
                if provider == "anthropic":
                    res = await self._anthropic_call(route, model, prompt, system, images, history, max_tokens)
                elif provider == "openai":
                    res = await self._openai_call(model, prompt, system, images, history, max_tokens)
                else:
                    res = await self._ollama_call(model, prompt, system, images, history)
                self._record(res, provider, model, route, True)
                if res.refused or not res.text.strip():
                    errors.append(f"{provider}:{model}: empty or refused")
                    continue
                return res
            except Exception as e:  # try the next candidate
                self._record(None, provider, model, route, False)
                errors.append(f"{provider}:{model}: {type(e).__name__}: {str(e)[:200]}")
        if errors and all("budget" in e for e in errors):
            raise BudgetExceeded("; ".join(errors))
        raise LLMError("; ".join(errors))

    # ------------------------------------------------------------ providers
    def _anthropic_client(self):
        import anthropic
        key = self.vault.get("anthropic_api_key")
        if self._anthropic is None or key != self._anthropic_key:
            kw = {"http_client": self.anthropic_http} if self.anthropic_http else {}
            self._anthropic = anthropic.AsyncAnthropic(api_key=key, max_retries=2, **kw)
            self._anthropic_key = key
        return self._anthropic

    async def _anthropic_call(self, route, model, prompt, system, images, history, max_tokens) -> LLMResult:
        content: list[dict] = []
        for img in images or []:
            content.append({"type": "image", "source": {"type": "base64", "media_type": img.media_type,
                                                         "data": base64.standard_b64encode(img.data).decode()}})
        content.append({"type": "text", "text": prompt})
        messages = list(history or []) + [{"role": "user", "content": content}]
        effort = (self.db.get_setting("effort") or {}).get(route, "medium")
        kwargs: dict = dict(model=model, max_tokens=max_tokens, messages=messages,
                            output_config={"effort": effort})
        if system:
            kwargs["system"] = system
        searches = 0
        if route == "research" and self.db.get_setting("web_search_enabled", False):
            kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]
        client = self._anthropic_client()
        # Server-side fallback re-runs a declined request on another Claude model.
        if model.startswith(("claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")):
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
            create = client.beta.messages.create
        else:
            create = client.messages.create

        texts: list[str] = []
        sources: list = []
        tin = tout = 0
        for _ in range(4):  # pause_turn continuation for long server-tool turns
            resp = await create(**kwargs)
            tin += resp.usage.input_tokens
            tout += resp.usage.output_tokens
            for block in resp.content:
                if block.type == "text":
                    texts.append(block.text)
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                    searches += 1
                    sources += [{"title": r.title, "url": r.url} for r in block.content
                                if getattr(r, "type", "") == "web_search_result"]
            if resp.stop_reason != "pause_turn":
                break
            kwargs["messages"] = kwargs["messages"] + [{"role": "assistant", "content": resp.content}]
        pin, pout = PRICING.get(model, (0, 0))
        cost = tin / 1e6 * pin + tout / 1e6 * pout + searches * WEB_SEARCH_USD
        return LLMResult("".join(texts), "anthropic", model, tin, tout, round(cost, 6),
                         refused=resp.stop_reason == "refusal", sources=sources)

    async def _openai_call(self, model, prompt, system, images, history, max_tokens) -> LLMResult:
        base = self.db.get_setting("openai_base_url").rstrip("/")
        user: list | str = prompt
        if images:
            user = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,"
                                                           f"{base64.b64encode(i.data).decode()}"}} for i in images]
        msgs = ([{"role": "system", "content": system}] if system else []) + list(history or []) + \
               [{"role": "user", "content": user}]
        r = await self.http.post(f"{base}/chat/completions",
                                 headers={"Authorization": f"Bearer {self.vault.get('openai_api_key')}"},
                                 json={"model": model, "messages": msgs, "max_completion_tokens": max_tokens})
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        u = j.get("usage") or {}
        tin, tout = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        prices = self.db.get_setting("openai_prices") or {}
        pin, pout = prices.get(model) or prices.get("*") or (0, 0)
        if httpx.URL(base).host in ("localhost", "127.0.0.1", "::1"):
            pin = pout = 0   # a local server such as LM Studio costs nothing
        return LLMResult(j["choices"][0]["message"].get("content") or "", "openai", model,
                         tin, tout, round(tin / 1e6 * pin + tout / 1e6 * pout, 6))

    async def _ollama_call(self, model, prompt, system, images, history) -> LLMResult:
        url = self.db.get_setting("ollama_url")
        msg: dict = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [base64.b64encode(i.data).decode() for i in images]
        msgs = ([{"role": "system", "content": system}] if system else []) + list(history or []) + [msg]
        r = await self.local_http.post(f"{url}/api/chat", json={"model": model, "messages": msgs, "stream": False,
                                       "options": {"num_ctx": int(self.db.get_setting("ollama_num_ctx", 16384) or 16384)}},
                                 timeout=600)
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        return LLMResult(j.get("message", {}).get("content", ""), "ollama", model,
                         j.get("prompt_eval_count", 0), j.get("eval_count", 0), 0.0)

    # ------------------------------------------------------------ speech to text
    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str:
        """Voice notes → text with OpenAI's transcription API (needs an OpenAI key)."""
        key = self.vault.get("openai_api_key")
        if not key:
            raise LLMError("no_stt_configured")
        base = self.db.get_setting("openai_base_url").rstrip("/")
        r = await self.http.post(f"{base}/audio/transcriptions", headers={"Authorization": f"Bearer {key}"},
                                 files={"file": (filename, audio)}, data={"model": "whisper-1"})
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json().get("text", "")
