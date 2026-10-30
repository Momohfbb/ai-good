"""Short reply strings used by agents, the engine and the Telegram bot."""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "memory_saved": {"ar": "✅ حفظت في الذاكرة: {content}", "en": "✅ Saved to memory: {content}"},
    "memory_empty": {"ar": "الذاكرة فارغة أو لا يوجد ما يطابق.", "en": "Nothing in memory matches."},
    "memory_deleted": {"ar": "حذفت {n} عنصر من الذاكرة.", "en": "Deleted {n} memory item(s)."},
    "refused_secret": {"ar": "لا أحفظ كلمات السر أو المفاتيح أو أرقام البطاقات في الذاكرة. ضع المفاتيح في الإعدادات ← المفاتيح (مشفرة).",
                       "en": "I don't store passwords, keys or card numbers in memory. Put API keys in Settings → Keys (encrypted)."},
    "describe_image": {"ar": "صف هذه الصورة بالتفصيل.", "en": "Describe this image in detail."},
    "summarize": {"ar": "لخّص هذا الملف بالنقاط المهمة.", "en": "Summarise the key points of this file."},
    "file_saved": {"ar": "📄 حفظت الملف: {path}", "en": "📄 Saved file: {path}"},
    "folder_empty": {"ar": "المجلد فارغ.", "en": "The folder is empty."},
    "folder_created": {"ar": "📁 أنشأت المجلد: {path}", "en": "📁 Created folder: {path}"},
    "moved": {"ar": "نقلت {src} إلى {dest}", "en": "Moved {src} to {dest}"},
    "deleted": {"ar": "🗑️ نقلت {path} إلى سلة المحذوفات (.recycle).", "en": "🗑️ Moved {path} to the recycle folder (.recycle)."},
    "organized": {"ar": "رتبت {n} ملف حسب النوع.", "en": "Sorted {n} file(s) by type."},
    "project_created": {"ar": "📋 أنشأت المشروع: {name}", "en": "📋 Created project: {name}"},
    "item_added": {"ar": "أضفت «{title}» إلى مشروع {project}", "en": "Added “{title}” to project {project}"},
    "no_projects": {"ar": "لا توجد مشاريع بعد.", "en": "No projects yet."},
    "open_items": {"ar": "المهام المفتوحة: {n}", "en": "Open items: {n}"},
    "late_items": {"ar": "المتأخرة: {n}", "en": "Late: {n}"},
    "no_security_events": {"ar": "لا توجد أحداث أمنية مهمة.", "en": "No notable security events."},
    "telegram_sent": {"ar": "أرسلت الرسالة إلى {n} مستخدم على Telegram.", "en": "Sent to {n} Telegram user(s)."},
    "no_model": {"ar": "لم يتم إعداد أي نموذج ذكاء اصطناعي بعد. أضف مفتاح API أو شغّل Ollama من الإعدادات. "
                       "بدون نموذج أفهم هذه الأوامر فقط: تذكر …، واش تتفكر، أنشئ مشروع …، المشاريع، اكتب ملاحظة …، الملفات، رتب الملفات، حالة النظام، الوقت.",
                 "en": "No AI model is configured yet. Add an API key or start Ollama in Settings. "
                       "Without a model I understand only: remember …, what do you remember, create project …, projects, write note …, files, organize files, system status, time."},
    "budget": {"ar": "تم بلوغ حد الميزانية الشهرية للنماذج المدفوعة. غيّره من الإعدادات أو استعمل نموذجاً محلياً.",
               "en": "The monthly budget for paid models is used up. Raise it in Settings or use a local model."},
    "emergency_on": {"ar": "🛑 وضع الطوارئ مفعل: تم إيقاف جميع المهام ولن تبدأ مهام جديدة حتى تستأنف النظام.",
                     "en": "🛑 Emergency stop is on: all tasks stopped; no new task starts until you resume the system."},
    "needs_approval": {"ar": "⚠️ المهمة «{title}» تحتاج موافقتك: {reason}", "en": "⚠️ Task “{title}” needs your approval: {reason}"},
    "task_done": {"ar": "✅ اكتملت المهمة «{title}»", "en": "✅ Task “{title}” completed"},
    "task_failed": {"ar": "❌ فشلت المهمة «{title}»: {error}", "en": "❌ Task “{title}” failed: {error}"},
    "verify_failed": {"ar": "لم ينجح التحقق من النتيجة", "en": "Result verification failed"},
    "suspicious": {"ar": "الأمر يحتوي على عبارات حساسة ({what})، يلزم تأكيدك قبل التنفيذ.",
                   "en": "The command contains sensitive phrases ({what}); confirm before it runs."},
    "risky_step": {"ar": "الخطوة «{step}» عملية حساسة.", "en": "Step “{step}” is a sensitive operation."},
    "clarify": {"ar": "أحتاج توضيحاً: {q}", "en": "I need clarification: {q}"},
}

ERRORS = {
    "no_model_configured": {"ar": "لا يوجد نموذج ذكاء اصطناعي مُعد.", "en": "No AI model configured."},
    "path_outside_workspace": {"ar": "المسار خارج مجلد مساحة العمل المسموح.", "en": "Path is outside the allowed workspace."},
    "file_not_found": {"ar": "الملف غير موجود.", "en": "File not found."},
    "folder_not_found": {"ar": "المجلد غير موجود.", "en": "Folder not found."},
    "destination_exists": {"ar": "يوجد ملف بنفس الاسم في الوجهة.", "en": "A file with that name already exists."},
    "project_exists": {"ar": "يوجد مشروع بهذا الاسم.", "en": "A project with this name exists."},
    "project_not_found": {"ar": "المشروع غير موجود.", "en": "Project not found."},
    "refused_secret": STRINGS["refused_secret"],
    "unsupported_image_type": {"ar": "نوع الصورة غير مدعوم.", "en": "Unsupported image type."},
    "pdf_support_missing": {"ar": "قراءة PDF تحتاج حزمة pypdf.", "en": "PDF reading needs the pypdf package."},
    "docx_support_missing": {"ar": "قراءة DOCX تحتاج حزمة python-docx.", "en": "DOCX reading needs python-docx."},
    "no_stt_configured": {"ar": "تحويل الصوت إلى نص يحتاج مفتاح OpenAI.", "en": "Speech-to-text needs an OpenAI key."},
}


def t(key: str, lang: str = "ar", **kw) -> str:
    lang = "ar" if lang.startswith("ar") else "en"
    entry = STRINGS.get(key) or ERRORS.get(key) or {}
    s = entry.get(lang) or entry.get("en") or key
    try:
        return s.format(**kw)
    except (KeyError, IndexError):
        return s


def err(code: str, lang: str = "ar") -> str:
    """Human message for an error code; unknown codes pass through."""
    if code in ERRORS:
        return t(code, lang)
    return code
