"""The central brain: understand the request, turn it into a plan of agent steps.

1. Simple everyday commands (Arabic, Darija, English, French) are recognised by rules,
   so they work offline and cost nothing.
2. Anything else is planned by the "plan" model route, constrained to the agent catalog.
3. Without any model, the brain says so and lists what it can still do.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .agents import Agent, action_catalog
from .language import LANG_NAMES, detect
from .llm import LLMError, Router
from .pc import allowed_apps
from .security import suspicious_command


@dataclass
class Plan:
    language: str
    summary: str
    steps: list[dict] = field(default_factory=list)   # {agent, action, params, description}
    clarification: str = ""
    warnings: list[str] = field(default_factory=list)
    planner: str = "rules"


STEP_LABELS = {
    "remember": ("حفظ في الذاكرة", "Save to memory"), "recall": ("البحث في الذاكرة", "Search memory"),
    "create_project": ("إنشاء مشروع", "Create project"), "add_item": ("إضافة مهمة للمشروع", "Add project item"),
    "list_projects": ("عرض المشاريع", "List projects"), "report": ("تقرير المشاريع", "Project report"),
    "write_note": ("كتابة ملاحظة", "Write note"), "list": ("عرض الملفات", "List files"),
    "organize": ("ترتيب الملفات حسب النوع", "Organize files by type"), "system_status": ("حالة النظام", "System status"),
    "time": ("الوقت والتاريخ", "Date and time"), "chat": ("الإجابة", "Answer"),
    "open_app": ("فتح البرنامج", "Open program"), "open_url": ("فتح الصفحة في المتصفح", "Open web page"),
    "screenshot": ("لقطة شاشة", "Screenshot"),
    "close_app": ("إغلاق البرنامج (النص غير المحفوظ فيه يضيع)", "Close program (unsaved text in it is lost)"),
}


def _s(agent, action, description, **params):
    return {"agent": agent, "action": action, "params": params, "description": description}


def localize(steps: list[dict], lang: str) -> list[dict]:
    i = 0 if lang.startswith("ar") else 1
    for st in steps:
        if st["action"] in STEP_LABELS:
            st["description"] = STEP_LABELS[st["action"]][i]
    return steps


# (pattern, builder) — first match wins. Patterns are matched on the stripped text.
RULES: list[tuple[re.Pattern, callable]] = []


def rule(pattern):
    def deco(fn):
        RULES.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), fn))
        return fn
    return deco


@rule(r"^(?:تذكر|تذكّر|احفظ|عقل على|عقل|تفكر|remember|retiens|souviens-toi)\s+(?:(?:أن|ان|بلي|that|que)\s+)?[:،,]?\s*(?P<c>.{3,})$")
def _r_remember(m):
    return [_s("manager", "remember", "Save to memory", content=m["c"].strip())]


@rule(r"^(?:واش تتفكر|واش تفكر|ماذا تتذكر|ماذا تعرف عني|اعرض الذاكرة|الذاكرة|what do you remember|show memory|memory)\s*(?P<q>.*)$")
def _r_recall(m):
    return [_s("manager", "recall", "Search memory", query=m["q"].strip(" ?؟"))]


@rule(r"^(?:أنشئ|انشئ|اصنع|دير|ديرلي|create|new|crée|créer)\s+(?:لي\s+)?(?:مشروع|projet|project)\s+(?P<n>[^\n]{1,80})$")
def _r_project(m):
    return [_s("business", "create_project", "Create project", name=m["n"].strip(" \"'«»"))]


@rule(r"^(?:أضف|اضف|زيد|add)\s+(?:مهمة|تاسك|task)\s+(?P<t>.+?)\s+(?:إلى|الى|في|ل|to)\s+(?:مشروع|project)\s+(?P<p>.+)$")
def _r_item(m):
    return [_s("business", "add_item", "Add item", project=m["p"].strip(" \"'«»"), title=m["t"].strip(" \"'«»"))]


@rule(r"^(?:المشاريع|مشاريعي|اعرض المشاريع|projects|my projects|list projects|mes projets)\s*[?؟]?$")
def _r_projects(m):
    return [_s("business", "list_projects", "List projects")]


@rule(r"^(?:تقرير المشاريع|تقرير الأعمال|المهام المتأخرة|business report|project report)\s*[?؟]?$")
def _r_report(m):
    return [_s("business", "report", "Project report")]


@rule(r"^(?:اكتب|سجل|سجّل|write|take)\s+(?:ملاحظة|نوط|note|a note)\s*[:،,]?\s*(?P<c>.+)$")
def _r_note(m):
    c = m["c"].strip()
    return [_s("files", "write_note", "Write note", title=c[:50], content=c)]


@rule(r"^(?:الملفات|ملفاتي|اعرض الملفات|files|list files|show files)\s*(?P<f>[\w/\-\. ]*)$")
def _r_files(m):
    return [_s("files", "list", "List files", folder=m["f"].strip())]


@rule(r"^(?:نظم|نظّم|رتب|رتّب|organize|organise)\s+(?:الملفات|ملفات|files)\s*(?:في|in)?\s*(?P<f>[\w/\-\. ]*)$")
def _r_organize(m):
    return [_s("files", "organize", "Organize files", folder=m["f"].strip() or "inbox")]


@rule(r"^(?:حالة النظام|حالة السيستام|كيفاش راه السيستام|status|system status|état du système)\s*[?؟]?$")
def _r_status(m):
    return [_s("security", "system_status", "System status")]


@rule(r"^(?:الوقت|الساعة|التاريخ|شحال الساعة|time|date|what time is it)\s*[?؟]?$")
def _r_time(m):
    return [_s("manager", "time", "Time")]


APP_ALIASES = {
    "notepad": ("notepad", "المفكرة", "المفكره", "مفكرة", "نوت باد", "النوت باد", "bloc-notes", "bloc notes"),
    "calculator": ("calculator", "calc", "الحاسبة", "الحاسبه", "الآلة الحاسبة", "الالة الحاسبة", "آلة حاسبة", "calculatrice"),
    "paint": ("paint", "الرسام", "الرسّام", "برنامج الرسم"),
    "explorer": ("explorer", "file explorer", "مستكشف الملفات", "explorateur"),
}


@rule(r"^(?:افتح|افتحلي|إفتح|حل|حلّ|حللي|شغل|شغّل|open|ouvre|lance)\s+(?:لي\s+)?(?P<a>[^\n]{2,60}?)\s*[.!]?$")
def _r_open(m):
    a = m["a"].strip(" \"'«»").lower()
    for name, words in APP_ALIASES.items():
        if a in words:
            return [_s("pc", "open_app", "Open program", app=name)]
    if re.fullmatch(r"(?:https?://)?[\w\-]+(?:\.[\w\-]+)+(?:/\S*)?", a):
        return [_s("pc", "open_url", "Open web page", url=a)]
    return None      # not a known program: let the model planner decide


@rule(r"^(?:أغلق|اغلق|إغلاق|سكر|سكّر|close|ferme)\s+(?P<a>[^\n]{2,60}?)\s*[.!]?$")
def _r_close(m):
    a = m["a"].strip(" \"'«»").lower()
    for name, words in APP_ALIASES.items():
        if a in words:
            return [_s("pc", "close_app", "Close program", app=name)]
    return None


@rule(r"^(?:خذ|خوذ|دير|ديرلي|اعمل|take)?\s*(?:لقطة شاشة|لقطة الشاشة|سكرين|سكرين شوت|صور الشاشة|صوّر الشاشة|screenshot|a screenshot|capture d'écran)\s*[.!]?$")
def _r_screenshot(m):
    return [_s("pc", "screenshot", "Screenshot")]


def rule_plan(text: str) -> list[dict] | None:
    t = text.strip()
    for pat, fn in RULES:
        m = pat.match(t)
        if m:
            steps = fn(m)
            if steps is not None:
                return steps
    return None


PLANNER_PROMPT = """You are the planner of a personal assistant system. Turn the owner's request into a short plan
of steps, using ONLY these actions:

{catalog}

Rules:
- Reply with one JSON object only, no prose: {{"summary": str, "clarification": str, "steps": [{{"agent": str, "action": str, "params": {{}}, "description": str}}]}}
- Use as few steps as possible. A plain question or conversation is a single manager.chat step whose "message" is the full request.
- A param value may be "$prev" to use the previous step's text result.
- Files live in the workspace; paths are relative to it. Received files are in "inbox/".
- If the request is ambiguous, or asks for something dangerous or that no action can do, return no steps and put a short question or explanation in "clarification" (in the owner's language).
- Never invent actions. Controlling the phone, games, browser pages and smart-home devices is not available yet.
- PC control (pc.*) is for the Windows PC the brain runs on. pc.open_app only accepts these app names: {apps}.
  To write in a program: open it with pc.open_app, then pc.type_text, then pc.press_keys if needed.
- "description" is a short label in the owner's language.
- The owner's language is {language}: write "summary", "clarification" and descriptions in it.
{attachments}
Owner's request:
<request>
{request}
</request>"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no json")
    return json.loads(text[start:end + 1])


class Brain:
    def __init__(self, router: Router, agents: dict[str, Agent]):
        self.router = router
        self.agents = agents

    def validate(self, steps: list[dict]) -> tuple[list[dict], list[str]]:
        ok, dropped = [], []
        for s in steps or []:
            agent = self.agents.get(str(s.get("agent")))
            action = agent.actions.get(str(s.get("action"))) if agent else None
            if not action:
                dropped.append(f"{s.get('agent')}.{s.get('action')}")
                continue
            params = {k: v for k, v in (s.get("params") or {}).items() if k in action.params}
            ok.append({"agent": agent.key, "action": action.name, "params": params,
                       "description": str(s.get("description") or action.description)[:120]})
        return ok, dropped

    async def plan(self, text: str, attachments: list[str] | None = None) -> Plan:
        lang = detect(text)
        if len(re.findall(r"[^\W\d_]", text)) < 4:
            # Too short or garbled to tell (e.g. "$hu" typed on the wrong keyboard): use the owner's UI language.
            lang = "ar" if (self.router.db.get_setting("ui_language") or "ar") == "ar" else "en"
        warnings = []
        sus = suspicious_command(text)
        if sus:
            warnings.append("suspicious:" + ", ".join(sus))

        steps = None if attachments else rule_plan(text)
        if steps:
            return Plan(lang, text[:80], localize(steps, lang), warnings=warnings, planner="rules")

        if not self.router.any_available():
            return Plan(lang, text[:80], [], clarification="no_model", warnings=warnings, planner="none")

        att = ("\nFiles attached to this request (workspace paths): " + ", ".join(attachments) + "\n") if attachments else ""
        apps = ", ".join(a["name"] for a in allowed_apps(self.router.db)) or "none"
        prompt = PLANNER_PROMPT.format(catalog=action_catalog(self.agents), request=text, attachments=att, apps=apps,
                                       language=LANG_NAMES.get(lang, "Arabic"))
        try:
            res = await self.router.complete("plan", prompt, max_tokens=4000)
            data = _extract_json(res.text)
            steps, dropped = self.validate(data.get("steps"))
            if dropped:
                warnings.append("dropped_unknown_actions:" + ", ".join(dropped))
            clar = str(data.get("clarification") or "").strip()
            if not steps and not clar:
                steps = [_s("manager", "chat", text[:80], message=text)]
            return Plan(lang, str(data.get("summary") or text[:80])[:120], steps, clar if not steps else "",
                        warnings, planner=f"{res.provider}:{res.model}")
        except (LLMError, ValueError, json.JSONDecodeError) as e:
            # Planner failed: still try to help with a direct answer.
            warnings.append(f"planner_failed:{type(e).__name__}")
            return Plan(lang, text[:80], localize([_s("manager", "chat", text[:80], message=text)], lang), warnings=warnings,
                        planner="fallback")
