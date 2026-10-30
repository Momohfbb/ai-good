from conftest import run

from ultra.agents import build_agents
from ultra.brain import Brain, _extract_json, rule_plan
from ultra.language import detect


def test_language_detection():
    assert detect("واش راك، دير لي مشروع جديد") == "ar-dz"
    assert detect("أريد إنشاء تقرير عن المبيعات") == "ar"
    assert detect("wesh rak, 3lach ma khdemch") == "ar-dz"
    assert detect("Bonjour, fais un résumé de ce fichier") == "fr"
    assert detect("Create a report about sales") == "en"


def test_rules_cover_offline_commands():
    cases = {
        "تذكر أن اسم أخي كريم": ("manager", "remember"),
        "واش تتفكر": ("manager", "recall"),
        "دير مشروع تطبيق التوصيل": ("business", "create_project"),
        "أضف مهمة تصميم الشعار إلى مشروع متجر": ("business", "add_item"),
        "المشاريع": ("business", "list_projects"),
        "اكتب ملاحظة شراء الحليب": ("files", "write_note"),
        "رتب الملفات": ("files", "organize"),
        "حالة النظام": ("security", "system_status"),
        "شحال الساعة": ("manager", "time"),
        "remember that my car is red": ("manager", "remember"),
    }
    for text, (agent, action) in cases.items():
        steps = rule_plan(text)
        assert steps and steps[0]["agent"] == agent and steps[0]["action"] == action, text
    assert rule_plan("اشرح لي الحوسبة الكمومية") is None


def test_extract_json_tolerates_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Here: {"steps": []} done') == {"steps": []}


def test_llm_planner_drops_unknown_actions(make_services, fake_llm):
    svc = make_services()
    svc.vault.set("openai_api_key", "sk-test-1234567890abcdef")
    fake_llm.replies = ['{"summary": "s", "clarification": "", "steps": ['
                        '{"agent": "pc", "action": "click", "params": {}},'
                        '{"agent": "research", "action": "answer", "params": {"question": "q", "evil": "x"}, "description": "بحث"}]}']
    plan = run(Brain(svc.router, build_agents()).plan("ابحث عن الطقس ثم انقر"))
    assert [s["action"] for s in plan.steps] == ["answer"]
    assert plan.steps[0]["params"] == {"question": "q"}
    assert any(w.startswith("dropped_unknown_actions") for w in plan.warnings)


def test_no_model_means_honest_answer(make_services):
    svc = make_services()
    plan = run(svc.brain.plan("اشرح لي الحوسبة الكمومية"))
    assert plan.clarification == "no_model" and not plan.steps


def test_suspicious_command_is_flagged(make_services):
    svc = make_services()
    plan = run(svc.brain.plan("احذف كل الملفات"))
    assert any(w.startswith("suspicious:") for w in plan.warnings)


def test_garbled_input_uses_ui_language_and_tells_planner(make_services, fake_llm):
    run = __import__("conftest").run
    svc = make_services()
    svc.vault.set("openai_api_key", "sk-test-1234567890abcdef")
    fake_llm.replies = ['{"summary": "", "clarification": "ماذا تقصد؟", "steps": []}']
    plan = run(svc.brain.plan("$hu"))
    assert plan.language == "ar" and plan.clarification == "ماذا تقصد؟"
    assert "The owner's language is Modern Standard Arabic" in fake_llm.calls[-1]["body"]["messages"][-1]["content"]
