"""Telegram bot for automated Shahid VIP account registration."""

import asyncio
import io
import json
import os
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Ensure UTF-8 output on Windows consoles
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from dotenv import load_dotenv
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright_stealth import Stealth
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

# Load environment variables from .env if present
load_dotenv()

(
    EMAIL,
    PASSWORD,
    BASE_NAME,
    CODE,
    SET_PERM_PASS,
    SET_PERM_NAME,
    SET_ADMIN_CONTACT,
    INPUT_QUEUED_EMAILS,
    CHOOSE_BATCH_COUNT,
    BATCH_PASSWORD,
) = range(10)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

BLOCKED_MARKERS = (
    "VPN",
    "proxy",
    "unusual activity",
    "نشاط غير معتاد",
    "تقييد الوصول",
)

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
ADMINS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admins.json")
SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers.json")
QUEUED_EMAILS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queued_emails.json")
PRIMARY_ADMIN_ID = 1291024378


def load_admins() -> list:
    admins = [PRIMARY_ADMIN_ID]
    # 1. From .env ADMIN_IDS (comma-separated, e.g. ADMIN_IDS=123456,78910)
    env_admins = os.getenv("ADMIN_IDS", "")
    if env_admins:
        for x in env_admins.split(","):
            x = x.strip()
            if x.isdigit():
                admins.append(int(x))

    # 2. From admins.json
    if os.path.exists(ADMINS_FILE):
        try:
            with open(ADMINS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, int):
                            admins.append(item)
                        elif isinstance(item, str) and item.isdigit():
                            admins.append(int(item))
                elif isinstance(data, dict):
                    for item in data.get("admins", []):
                        if isinstance(item, int):
                            admins.append(item)
                        elif isinstance(item, str) and item.isdigit():
                            admins.append(int(item))
        except Exception as e:
            print(f"[!] Error loading admins.json: {e}", flush=True)

    result = [PRIMARY_ADMIN_ID]
    for a in admins:
        if a not in result:
            result.append(a)
    return result


def save_admins(admin_ids: list) -> None:
    dedup = [PRIMARY_ADMIN_ID]
    for x in admin_ids:
        if str(x).isdigit():
            val = int(x)
            if val not in dedup:
                dedup.append(val)
    try:
        with open(ADMINS_FILE, "w", encoding="utf-8") as f:
            json.dump(dedup, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Error saving admins.json: {e}", flush=True)


def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


def load_subscribers() -> dict:
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error loading subscribers.json: {e}", flush=True)
            return {}
    return {}


def save_subscribers(data: dict) -> None:
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Error saving subscribers.json: {e}", flush=True)


def load_queued_emails() -> dict:
    if os.path.exists(QUEUED_EMAILS_FILE):
        try:
            with open(QUEUED_EMAILS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error loading queued_emails.json: {e}", flush=True)
            return {}
    return {}


def save_queued_emails(data: dict) -> None:
    try:
        with open(QUEUED_EMAILS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Error saving queued_emails.json: {e}", flush=True)


def get_user_queued_emails(user_id: int) -> list:
    data = load_queued_emails()
    return data.get(str(user_id), [])


def add_user_queued_emails(user_id: int, new_emails: list) -> tuple[int, int, int]:
    """
    Adds emails to user's queue up to MAX 100 emails.
    Returns: (added_count, duplicate_count, total_count)
    """
    data = load_queued_emails()
    uid = str(user_id)
    current = data.get(uid, [])

    current_lower = {e.lower() for e in current}
    added = 0
    duplicates = 0

    for email in new_emails:
        clean = email.strip()
        if not clean or not EMAIL_RE.match(clean):
            continue
        if clean.lower() in current_lower:
            duplicates += 1
            continue
        if len(current) >= 100:
            break
        current.append(clean)
        current_lower.add(clean.lower())
        added += 1

    data[uid] = current
    save_queued_emails(data)
    return added, duplicates, len(current)


def pop_user_queued_email(user_id: int, email: str = "") -> str:
    """
    Removes a successfully created email from user's queue.
    If email is specified, removes that exact email. Otherwise pops the first one.
    """
    data = load_queued_emails()
    uid = str(user_id)
    current = data.get(uid, [])
    if not current:
        return ""

    if email:
        clean_target = email.strip().lower()
        for idx, e in enumerate(current):
            if e.strip().lower() == clean_target:
                removed = current.pop(idx)
                data[uid] = current
                save_queued_emails(data)
                return removed
        return ""
    else:
        removed = current.pop(0)
        data[uid] = current
        save_queued_emails(data)
        return removed


def clear_user_queued_emails(user_id: int) -> bool:
    data = load_queued_emails()
    uid = str(user_id)
    if uid in data:
        data[uid] = []
        save_queued_emails(data)
        return True
    return False


def add_or_renew_subscriber(user_id: int, days: int, note: str = "", created_by: int = 0) -> dict:
    data = load_subscribers()
    uid = str(user_id)
    now = datetime.now()

    # If already exists and not expired, extend from existing expiry date
    if uid in data and data[uid].get("expires_at"):
        try:
            cur_exp = datetime.strptime(data[uid]["expires_at"], "%Y-%m-%d %H:%M:%S")
            if cur_exp > now:
                new_exp = cur_exp + timedelta(days=days)
            else:
                new_exp = now + timedelta(days=days)
        except Exception:
            new_exp = now + timedelta(days=days)
    else:
        new_exp = now + timedelta(days=days)

    record = {
        "user_id": user_id,
        "started_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": new_exp.strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "is_active": True,
        "note": note or data.get(uid, {}).get("note", ""),
        "created_by": created_by,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data[uid] = record
    save_subscribers(data)
    return record


def remove_subscriber(user_id: int) -> bool:
    data = load_subscribers()
    uid = str(user_id)
    if uid in data:
        del data[uid]
        save_subscribers(data)
        return True
    return False


def check_user_access(user_id: int) -> tuple[bool, str, dict]:
    """
    Checks if a user has access to the bot.
    Returns: (has_access: bool, status_or_reason: str, subscriber_info: dict)
    """
    # 1. Admins have permanent unrestricted access
    if is_admin(user_id):
        return True, "admin", {"role": "admin", "status": "active", "expires_at": "غير محدود (مشرف)"}

    # 2. Check subscriber status
    data = load_subscribers()
    uid = str(user_id)
    sub = data.get(uid)
    if not sub:
        return False, "not_subscribed", {}

    if not sub.get("is_active", True):
        return False, "suspended", sub

    expires_str = sub.get("expires_at", "")
    if not expires_str:
        return False, "invalid", sub

    try:
        exp = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        if now > exp:
            return False, "expired", sub

        diff = exp - now
        days = diff.days
        hours = diff.seconds // 3600
        mins = (diff.seconds % 3600) // 60
        remaining = f"{days} يوم و {hours} ساعة" if days > 0 else f"{hours} ساعة و {mins} دقيقة"
        return True, remaining, sub
    except Exception:
        return False, "invalid", sub


async def access_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    user_id = user.id

    # Allow /id and /myid commands for everyone to easily obtain their ID
    text_content = ""
    if update.effective_message and update.effective_message.text:
        text_content = update.effective_message.text.strip().lower()

    if text_content in ["/id", "id", "/myid"]:
        await update.effective_message.reply_text(
            f"🆔 **الآيدي الخاص بك (Your Telegram ID):**\n`{user_id}`",
            parse_mode="Markdown"
        )
        raise ApplicationHandlerStop

    has_access, status, sub_info = check_user_access(user_id)

    if has_access:
        # User is either an Admin or an Active Subscriber -> grant access!
        return

    # User does NOT have access!
    contact_url = get_admin_contact_url()
    contact_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 تواصل مع الإدارة للاشتراك", url=contact_url)]
    ])

    if status == "expired":
        exp_date = sub_info.get("expires_at", "غير معروف")
        text = (
            "⏳ **عذراً، لقد انتهت فترة اشتراكك في البوت!**\n\n"
            f"📅 **تاريخ الانتهاء:** `{exp_date}`\n"
            f"🆔 **الآيدي الخاص بك:** `{user_id}`\n\n"
            "💡 لتجديد اشتراكك ومتابعة إنشاء الحسابات، يرجى الضغط على الزر أدناه للتواصل مع الإدارة وتزويدهم بالآيدي الخاص بك."
        )
    elif status == "suspended":
        text = (
            "⛔ **عذراً، اشتراكك معلّق حالياً من قبل الإدارة!**\n\n"
            f"🆔 **الآيدي الخاص بك:** `{user_id}`\n\n"
            "💡 يرجى مراجعة إدارة البوت عبر الزر أدناه لتفعيل حسابك."
        )
    else:  # not_subscribed
        text = (
            "👑 **مرحباً بك في أفضل بوت لإنشاء وتجهيز حسابات Shahid VIP!**\n\n"
            "🔒 **الخدمة مفعلة للمشتركين المعتمدين.**\n"
            "للحصول على صلاحية استخدام البوت وتجهيز 4 ملفات شخصية كاملة، يرجى تفعيل اشتراكك.\n\n"
            f"🆔 **الآيدي الخاص بك (Telegram ID):**\n`{user_id}`\n\n"
            "💡 **طريقة الاشتراك والتفعيل:**\n"
            "1️⃣ اضغط على الآيدي أعلاه لنسخه.\n"
            "2️⃣ اضغط على زر **'💬 تواصل مع الإدارة للاشتراك'** أدناه لإرسال الآيدي وتفعيل اشتراكك فوراً."
        )

    if update.callback_query:
        await update.callback_query.answer("⛔ لا يوجد اشتراك نشط!", show_alert=True)
        try:
            await update.callback_query.message.reply_text(text, reply_markup=contact_markup, parse_mode="Markdown")
        except Exception:
            pass
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=contact_markup, parse_mode="Markdown")
    raise ApplicationHandlerStop


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Error saving settings: {e}", flush=True)


def get_admin_contact() -> str:
    data = load_settings()
    contact = data.get("admin_contact", "")
    if not contact:
        contact = os.getenv("ADMIN_CONTACT", "")
    return contact


def set_admin_contact(contact: str) -> None:
    data = load_settings()
    data["admin_contact"] = contact.strip()
    save_settings(data)


def get_admin_contact_url() -> str:
    contact = get_admin_contact()
    if not contact:
        return f"tg://user?id={PRIMARY_ADMIN_ID}"
    contact = contact.strip()
    if contact.startswith("http://") or contact.startswith("https://") or contact.startswith("tg://"):
        return contact
    if contact.startswith("t.me/"):
        return f"https://{contact}"
    if contact.startswith("@"):
        clean = contact.lstrip("@")
        return f"https://t.me/{clean}"
    if contact.isdigit():
        return f"tg://user?id={contact}"
    return f"https://t.me/{contact}"


def get_user_settings(user_id: int) -> dict:
    data = load_settings()
    uid = str(user_id)
    return data.get(uid, {
        "perm_pass_enabled": False,
        "perm_password": "",
        "perm_name_enabled": False,
        "perm_name": "",
    })


def set_user_perm_password(user_id: int, password: str) -> None:
    data = load_settings()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    data[uid]["perm_password"] = password
    data[uid]["perm_pass_enabled"] = True
    save_settings(data)


def toggle_user_perm_password(user_id: int) -> bool:
    data = load_settings()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"perm_pass_enabled": False, "perm_password": "", "perm_name_enabled": False, "perm_name": ""}
    cur = data[uid].get("perm_pass_enabled", False)
    data[uid]["perm_pass_enabled"] = not cur
    save_settings(data)
    return data[uid]["perm_pass_enabled"]


def set_user_perm_name(user_id: int, name: str) -> None:
    data = load_settings()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    data[uid]["perm_name"] = name
    data[uid]["perm_name_enabled"] = True
    save_settings(data)


def toggle_user_perm_name(user_id: int) -> bool:
    data = load_settings()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"perm_pass_enabled": False, "perm_password": "", "perm_name_enabled": False, "perm_name": ""}
    cur = data[uid].get("perm_name_enabled", False)
    data[uid]["perm_name_enabled"] = not cur
    save_settings(data)
    return data[uid]["perm_name_enabled"]


def load_accounts() -> dict:
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_account(user_id: int, email: str, password: str, profiles: list) -> None:
    data = load_accounts()
    uid = str(user_id)
    if uid not in data:
        data[uid] = []
    data[uid].append({
        "email": email,
        "password": password,
        "profiles": profiles,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] Error saving account: {e}", flush=True)


def get_user_accounts(user_id: int) -> list:
    data = load_accounts()
    return data.get(str(user_id), [])


def clear_user_accounts(user_id: int) -> bool:
    data = load_accounts()
    uid = str(user_id)
    if uid in data:
        data[uid] = []
        try:
            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False
    return False


# UI Keyboards
MAIN_REPLY_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["➕ إنشاء حساب جديد", "📋 حساباتي"],
        ["🔐 كلمة المرور الدائمة", "👤 اسم الملفات المحفوظ"],
        ["⏳ اشتراكي", "👥 المشتركين"],
        ["📥 تصدير الحسابات", "ℹ️ تعليمات ومساعدة"],
        ["❌ إلغاء العملية"],
    ],
    resize_keyboard=True,
)


def get_main_inline_keyboard(user_id: int = 0) -> InlineKeyboardMarkup:
    queue_len = len(get_user_queued_emails(user_id)) if user_id else 0
    queue_label = f"📬 قائمة الإيميلات ({queue_len})" if queue_len else "📬 قائمة الإيميلات (تلقائي)"

    buttons = [
        [
            InlineKeyboardButton("➕ إنشاء حساب جديد", callback_data="act_create"),
            InlineKeyboardButton("📋 حساباتي", callback_data="act_my_accounts"),
        ],
        [
            InlineKeyboardButton(queue_label, callback_data="act_email_queue"),
        ],
        [
            InlineKeyboardButton("🔐 كلمة المرور الدائمة", callback_data="act_perm_pass"),
            InlineKeyboardButton("👤 اسم الملفات المحفوظ", callback_data="act_perm_name"),
        ],
    ]
    if user_id and is_admin(user_id):
        buttons.append([
            InlineKeyboardButton("👥 المشتركين", callback_data="act_subs"),
            InlineKeyboardButton("📞 حساب التواصل", callback_data="act_admin_contact"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton("⏳ تفاصيل اشتراكي", callback_data="act_my_sub"),
        ])

    buttons.append([
        InlineKeyboardButton("📥 تصدير الحسابات", callback_data="act_export"),
        InlineKeyboardButton("ℹ️ تعليمات ومساعدة", callback_data="act_help"),
    ])
    return InlineKeyboardMarkup(buttons)


def get_perm_pass_keyboard(enabled: bool, has_pass: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 إيقاف الميزة" if enabled else "🟢 تفعيل الميزة"
    edit_text = "✏️ تغيير كلمة المرور" if has_pass else "➕ تعيين كلمة المرور"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(toggle_text, callback_data="act_toggle_perm_pass"),
            InlineKeyboardButton(edit_text, callback_data="act_edit_perm_pass"),
        ],
        [
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])


def get_perm_name_keyboard(enabled: bool, has_name: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 إيقاف الميزة" if enabled else "🟢 تفعيل الميزة"
    edit_text = "✏️ تغيير الاسم المحفوظ" if has_name else "➕ تعيين الاسم المحفوظ"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(toggle_text, callback_data="act_toggle_perm_name"),
            InlineKeyboardButton(edit_text, callback_data="act_edit_perm_name"),
        ],
        [
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء العملية", callback_data="act_cancel")]
    ])


def get_name_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ استخدام dahoom الافتراضي", callback_data="act_default_name")],
        [InlineKeyboardButton("❌ إلغاء العملية", callback_data="act_cancel")],
    ])


def get_accounts_keyboard(has_accounts: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_accounts:
        buttons.append([
            InlineKeyboardButton("📥 تصدير كملف نصي", callback_data="act_export"),
            InlineKeyboardButton("🗑️ مسح السجل", callback_data="act_confirm_clear"),
        ])
    buttons.append([
        InlineKeyboardButton("➕ إنشاء حساب جديد", callback_data="act_create"),
        InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
    ])
    return InlineKeyboardMarkup(buttons)


WELCOME_TEXT = (
    "👑 **مرحباً بك في أفضل وأسرع بوت لإنشاء وتجهيز حسابات Shahid VIP!**\n\n"
    "✨ تم تصميم البوت بأعلى كفاءة لتسجيل الحسابات وتجهيز 4 ملفات شخصية كاملة بسرعة واحترافية فائقة.\n\n"
    "👇 اختر الإجراء المطلوب من القائمة أدناه:"
)

HELP_TEXT = (
    "ℹ️ **دليل استخدام ومميزات البوت:**\n\n"
    "1️⃣ **➕ إنشاء حساب جديد:**\n"
    "   • اضغط على '➕ إنشاء حساب جديد' ثم أرسل البريد الإلكتروني.\n"
    "   • سيتم طلب كود التحقق (OTP) المرسل إلى بريدك فوراً.\n"
    "   • بعد إدخال الكود، يكمل البوت تجهيز الحساب وضبط 4 ملفات شخصية تلقائياً وبسرعة فائقة!\n\n"
    "2️⃣ **📬 قائمة الإيميلات (الإنشاء التلقائي):**\n"
    "   • أضف حتى 100 إيميل مسبقاً، واجعل البوت ينشئ الحسابات بالتتابع ويحذف كل حساب يكتمل بنجاح تلقائياً دون تكرار.\n\n"
    "3️⃣ **🔐 كلمة المرور الدائمة:**\n"
    "   • يمكنك تعيين كلمة مرور ثابتة وتفعيلها ليتم تطبيقها تلقائياً على كل الحسابات دون سؤالك عنها.\n\n"
    "4️⃣ **👤 اسم الملفات المحفوظ:**\n"
    "   • احفظ الاسم الذي تفضله (مثل `noor` أو `dahoom`) لتجهيز وتسمية الملفات الشخصية (`noor1`...`noor4`) تلقائياً!\n\n"
    "5️⃣ **📋 حساباتي:**\n"
    "   • استعراض جميع حساباتك المنشأة مع تفاصيلها كاملة وتصديرها بملف نصي منظم.\n\n"
    "💡 يمكنك في أي وقت الضغط على **'❌ إلغاء العملية'** للعودة إلى القائمة الرئيسية."
)


@dataclass
class RegistrationSession:
    email: str = ""
    password: str = ""
    base_name: str = "dahoom"
    playwright: Optional[Playwright] = None
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None


def get_session(context: ContextTypes.DEFAULT_TYPE) -> RegistrationSession:
    return context.user_data.setdefault("session", RegistrationSession())


async def cleanup_session(context: ContextTypes.DEFAULT_TYPE, force: bool = False) -> None:
    session = context.user_data.get("session")
    if not session:
        return
    # Only close browser if force is explicitly True (e.g. /cancel)
    if force:
        try:
            if session.context:
                await session.context.close()
            if session.browser:
                await session.browser.close()
            if session.playwright:
                await session.playwright.stop()
        except Exception:
            pass
        context.user_data.pop("session", None)


async def human_type_locator(page: Page, locator, text: str) -> None:
    """Type text into a Playwright locator with human delays."""
    await locator.click()
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.04, 0.09))


async def remove_cookie_banner(page: Page) -> None:
    """Removes OneTrust cookie banner overlay."""
    try:
        await page.evaluate("""() => {
            const ids = ['onetrust-banner-sdk', 'onetrust-consent-sdk', 'onetrust-style'];
            ids.forEach(id => {
                const el = document.getElementById(id);
                if (el) el.remove();
            });
            document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-banner-sdk').forEach(el => el.remove());
        }""")
    except Exception:
        pass


async def handle_skip_onboarding(page: Page) -> None:
    """Detects and clicks 'تخطى وابدأ المشاهدة' or similar skip buttons if present."""
    await remove_cookie_banner(page)
    try:
        skip_selectors = [
            "text='تخطى وابدأ المشاهدة'",
            "text='تخطي وابدأ المشاهدة'",
            "button:has-text('تخطى')",
            "button:has-text('تخطي')",
            "a:has-text('تخطى')",
            "a:has-text('تخطي')",
            "p:has-text('تخطى')",
            "span:has-text('تخطى')",
        ]
        for s in skip_selectors:
            try:
                loc = page.locator(s).first
                if await loc.is_visible(timeout=1500):
                    await loc.click(force=True)
                    print(f"[+] Skip clicked with selector: {s}", flush=True)
                    await asyncio.sleep(2.5)
                    return
            except Exception:
                pass

        clicked = await page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('button, a, div, span, p'));
            const matches = all.filter(el => {
                const t = (el.innerText || '').trim();
                return t.includes('تخطى') || t.includes('تخطي') || t.includes('ابدأ المشاهدة');
            });
            if (matches.length > 0) {
                matches.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                matches[0].click();
                return matches[0].innerText;
            }
            return null;
        }""")
        if clicked:
            print(f"[+] Skip clicked via JS evaluate: {clicked}", flush=True)
            await asyncio.sleep(2.5)
    except Exception as e:
        print(f"[!] Skip note: {e}", flush=True)


async def open_avatar_dropdown(page: Page) -> bool:
    """Opens the user dropdown menu from the top-left header."""
    await remove_cookie_banner(page)
    opened = False
    try:
        opened = await page.evaluate("""() => {
            const promo = Array.from(document.querySelectorAll('a, button, div')).find(el => {
                const t = (el.innerText || '').trim();
                return t === 'اشترك الآن' || t.includes('اشترك');
            });
            if (promo && promo.parentElement) {
                const siblings = Array.from(promo.parentElement.children);
                const avatar = siblings.find(el => el !== promo && (el.querySelector('img, svg') || el.tagName === 'DIV' || el.tagName === 'BUTTON'));
                if (avatar) {
                    avatar.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                    avatar.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                    avatar.click();
                    return true;
                }
            }
            const headerImgs = Array.from(document.querySelectorAll('header img, nav img, div[id*="header"] img, img')).filter(img => {
                const r = img.getBoundingClientRect();
                return r.left >= 0 && r.left < 160 && r.top >= 0 && r.top < 120 && r.width > 15;
            });
            if (headerImgs.length > 0) {
                const target = headerImgs[0].closest('button, a, div[class*="cursor"]') || headerImgs[0];
                target.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                target.click();
                return true;
            }
            return false;
        }""")
    except Exception as e:
        print(f"[!] open_avatar_dropdown JS note: {e}", flush=True)

    # Mouse hover + click at (45, 58) as confirmed in live screen captures
    try:
        await page.mouse.move(45, 58)
        await asyncio.sleep(0.3)
        await page.mouse.click(45, 58)
    except Exception:
        pass

    await asyncio.sleep(2.0)
    return True


async def click_change_profile_span(page: Page) -> bool:
    """Clicks 'تغير الملف الشخصي' inside the open dropdown menu."""
    selectors = [
        "span.font-shahidMedium:has-text('تغير')",
        "span:has-text('تغير الملف الشخصي')",
        "span:has-text('تغيير الملف الشخصي')",
        "text='تغير الملف الشخصي'",
        "text='تغيير الملف الشخصي'",
        ".underline:has-text('الملف الشخصي')",
    ]
    for s in selectors:
        try:
            loc = page.locator(s).first
            if await loc.is_visible(timeout=2000):
                await loc.click(force=True)
                print(f"[+] Clicked switch profile using selector: {s}", flush=True)
                return True
        except Exception:
            pass

    # Direct JS search & click
    try:
        clicked = await page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('span, a, button, div, p'));
            const target = all.find(el => {
                const t = (el.innerText || '').trim();
                return t === 'تغير الملف الشخصي' || t === 'تغيير الملف الشخصي' || (t.includes('تغير الملف') && t.length < 25);
            });
            if (target) {
                target.click();
                const p = target.closest('a, button') || target.parentElement;
                if (p) p.click();
                return true;
            }
            return false;
        }""")
        if clicked:
            print("[+] Clicked 'تغير الملف الشخصي' via JS evaluate!", flush=True)
            return True
    except Exception as e:
        print(f"[!] click_change_profile_span JS note: {e}", flush=True)

    # Coordinate fallback at (138, 240)
    try:
        print("[*] Coordinate fallback for 'تغير الملف الشخصي' at (138, 240)...", flush=True)
        await page.mouse.click(138, 240)
        return True
    except Exception:
        pass

    return False


async def navigate_to_select_profile_screen(page: Page) -> bool:
    """Ensures page is on profile selection screen ('اختر من يشاهد')."""
    await remove_cookie_banner(page)

    # Check if already on the profile selection screen
    plus_btn = page.locator(
        "[data-testid='SelectProfilePage_profile-plus-icon'], [data-test='plus'], img[alt='اضف ملف']"
    ).first
    try:
        if await plus_btn.is_visible(timeout=2000):
            print("[+] Already on profile selection screen!", flush=True)
            return True
    except Exception:
        pass

    # Open header avatar dropdown
    print("[*] Opening profile menu from header...", flush=True)
    await open_avatar_dropdown(page)

    # Click "تغير الملف الشخصي"
    print("[*] Clicking 'تغير الملف الشخصي'...", flush=True)
    await click_change_profile_span(page)
    await asyncio.sleep(3.0)

    # Verify arrival on profile selection screen
    try:
        if await plus_btn.is_visible(timeout=5000):
            print("[+] Successfully reached profile selection screen!", flush=True)
            return True
    except Exception:
        pass

    return False


async def click_add_profile(page: Page) -> bool:
    """Clicks '+' / 'إضافة' to open the new profile modal."""
    print("[*] Clicking '+' / 'إضافة'...", flush=True)
    selectors = [
        "[data-testid='SelectProfilePage_profile-plus-icon']",
        "[data-test='plus']",
        "img[alt='اضف ملف']",
        "div:has(> img[alt='اضف ملف'])",
        "div:has(> p:text('إضافة'))",
        "p:text('إضافة')",
        "text='إضافة'",
    ]
    for s in selectors:
        try:
            loc = page.locator(s).first
            if await loc.is_visible(timeout=2000):
                await loc.click(force=True)
                print(f"[+] Clicked '+' using selector: {s}", flush=True)
                return True
        except Exception:
            pass

    try:
        clicked = await page.evaluate("""() => {
            const plus = document.querySelector('[data-testid="SelectProfilePage_profile-plus-icon"]') ||
                         document.querySelector('[data-test="plus"]') ||
                         document.querySelector('img[alt="اضف ملف"]');
            if (plus) {
                plus.click();
                const p = plus.closest('div.cursor-pointer') || plus.parentElement;
                if (p) p.click();
                return true;
            }
            const p = Array.from(document.querySelectorAll('p, span, div')).find(el => (el.innerText || '').trim() === 'إضافة');
            if (p) {
                p.click();
                const parent = p.closest('[class*="cursor-pointer"]') || p.parentElement;
                if (parent) parent.click();
                return true;
            }
            return false;
        }""")
        if clicked:
            print("[+] Clicked '+' via JS evaluate!", flush=True)
            return True
    except Exception as e:
        print(f"[!] click_add_profile JS error: {e}", flush=True)

    # Coordinate fallback on profile selection screen (400, 410)
    try:
        print("[*] Fallback mouse click on '+' at (400, 410)...", flush=True)
        await page.mouse.click(400, 410)
        return True
    except Exception:
        pass

    return False


async def create_single_profile(page: Page, profile_name: str) -> None:
    """Full lifecycle to add a new profile: navigate to screen -> click '+' -> type name -> click 'انشاء' -> skip onboarding."""
    print(f"\n==========================================", flush=True)
    print(f"[*] Starting creation for: {profile_name}", flush=True)
    print(f"==========================================", flush=True)

    # 1. Skip onboarding / interests if present from previous step
    await handle_skip_onboarding(page)

    # 2. Ensure we are on profile selection screen ('اختر من يشاهد')
    await navigate_to_select_profile_screen(page)
    await asyncio.sleep(2.0)

    # 3. Click "+" / "إضافة"
    await click_add_profile(page)
    await asyncio.sleep(2.5)

    # 4. Enter profile name in modal
    print(f"[*] Typing '{profile_name}' into modal...", flush=True)
    name_locators = [
        "input[placeholder*='الاسم']",
        "input[placeholder*='اسم']",
        "form input[type='text']:visible",
        "input[type='text']:visible",
        "input:not([type='hidden']):visible",
    ]
    name_box = None
    for sel in name_locators:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2500):
                name_box = loc
                break
        except Exception:
            pass

    if not name_box:
        name_box = page.locator("input[type='text']:visible, form input:visible").first
        await name_box.wait_for(state="visible", timeout=8000)

    await name_box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    await human_type_locator(page, name_box, profile_name)
    await asyncio.sleep(1.0)

    # 5. Click "انشاء"
    print(f"[*] Clicking 'انشاء' for {profile_name}...", flush=True)
    btn_selectors = [
        "button:has-text('انشاء')",
        "button:has-text('إنشاء')",
        "button[type='submit']",
    ]
    created = False
    for s in btn_selectors:
        try:
            loc = page.locator(s).last
            if await loc.is_visible(timeout=2000):
                await loc.click(force=True)
                print(f"[+] Clicked 'انشاء' using selector: {s}", flush=True)
                created = True
                break
        except Exception:
            pass

    if not created:
        try:
            created = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(el => {
                    const t = (el.innerText || '').trim();
                    return t === 'انشاء' || t === 'إنشاء';
                });
                if (b) {
                    b.click();
                    return true;
                }
                return false;
            }""")
        except Exception:
            pass

    if not created:
        print("[*] Coordinate fallback for 'انشاء' at (640, 710)...", flush=True)
        await page.mouse.click(640, 710)

    await asyncio.sleep(4.0)

    # 6. Skip onboarding / interests if shown after profile creation
    await handle_skip_onboarding(page)
    await asyncio.sleep(2.0)
    print(f"[+] Profile {profile_name} created successfully!\n", flush=True)


async def automate_all_profiles(page: Page, status_msg, base_name: str = "dahoom") -> None:
    """Automates creation of dahoom1, dahoom2, dahoom3, and dahoom4."""
    print("[*] Starting automated profile onboarding and creation...", flush=True)

    # Step 1: First Profile (dahoom1) on onboarding screen
    try:
        await remove_cookie_banner(page)
        name_input = page.locator("input#profileName, input[placeholder*='اسم الملف'], input[type='text']:visible").first
        await name_input.wait_for(state="visible", timeout=12000)
        await human_type_locator(page, name_input, f"{base_name}1")
        await asyncio.sleep(1.0)

        next_btn = page.locator("button:has-text('متابعة'), button[type='submit']").first
        await next_btn.click()
        await asyncio.sleep(3.0)
    except Exception as e:
        print(f"[!] dahoom1 initial step note: {e}", flush=True)

    # Skip interests if shown
    await handle_skip_onboarding(page)
    await asyncio.sleep(2.0)

    # Step 2: Loop for dahoom2, dahoom3, dahoom4
    for i in range(2, 5):
        prof = f"{base_name}{i}"
        try:
            await status_msg.edit_text(f"⏳ جاري إنشاء الملف الشخصي ({prof})...")
        except Exception:
            pass

        try:
            await create_single_profile(page, prof)
        except Exception as err:
            print(f"[!] Error creating profile {prof}: {err}", flush=True)
            await handle_skip_onboarding(page)


async def human_type(page: Page, selector: str, text: str) -> None:
    """Type characters with random human-like intervals to evade heuristic detection."""
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.03, 0.09))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows the main menu in a single clean message with interactive buttons."""
    user_id = update.effective_user.id
    await update.message.reply_text(
        WELCOME_TEXT,
        reply_markup=get_main_inline_keyboard(user_id),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def start_registration_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the account registration flow."""
    await cleanup_session(context, force=True)
    user_id = update.effective_user.id
    context.user_data["session"] = RegistrationSession()
    context.user_data["batch_info"] = None

    queue = get_user_queued_emails(user_id)
    if queue:
        prompt_text = (
            "➕ **بدء إنشاء حساب جديد في Shahid:**\n\n"
            f"📬 **لديك `{len(queue)}` إيميل محفوظ في قائمتك التلقائية!**\n\n"
            "👇 **اختر طريقة الإنشاء التي تفضلها:**\n"
            "1️⃣ **إنشاء تلقائي من إيميلاتي:** تشغيل متتابع للإيميلات المضافة وحذف كل حساب يُنشأ فوراً.\n"
            "2️⃣ **إدخال إيميل يدوي مختلف:** كتابة إيميل جديد الآن بشكل مستقل."
        )
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🚀 إنشاء من إيميلاتي المضافة ({len(queue)})", callback_data="act_choose_queue"),
            ],
            [
                InlineKeyboardButton("✍️ إدخال إيميل يدوي مختلف", callback_data="act_choose_manual"),
            ],
            [
                InlineKeyboardButton("❌ إلغاء العملية", callback_data="act_cancel"),
            ]
        ])
        if update.callback_query:
            await update.callback_query.answer()
            try:
                await update.callback_query.message.edit_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                await update.callback_query.message.reply_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
        return CHOOSE_BATCH_COUNT

    prompt_text = (
        "📧 **أرسل الآن البريد الإلكتروني الذي تريد التسجيل به:**\n\n"
        "💡 يمكنك الضغط على زر '❌ إلغاء العملية' في أي وقت للتراجع."
    )

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(
                prompt_text,
                reply_markup=get_cancel_keyboard(),
                parse_mode="Markdown",
            )
        except Exception:
            await update.callback_query.message.reply_text(
                prompt_text,
                reply_markup=get_cancel_keyboard(),
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            prompt_text,
            reply_markup=get_cancel_keyboard(),
            parse_mode="Markdown",
        )
    return EMAIL


async def choose_manual_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["batch_info"] = None
    prompt_text = (
        "📧 **أرسل الآن البريد الإلكتروني الذي تريد التسجيل به:**\n\n"
        "💡 يمكنك الضغط على زر '❌ إلغاء العملية' في أي وقت للتراجع."
    )
    try:
        await query.message.edit_text(prompt_text, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(prompt_text, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    return EMAIL


async def choose_queue_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    queue = get_user_queued_emails(user_id)
    if not queue:
        msg = "📭 لا توجد إيميلات في قائمتك حالياً! أضف إيميلات أولاً عبر زر '📬 قائمة الإيميلات'."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.effective_message.reply_text(msg)
        return ConversationHandler.END

    total = len(queue)
    prompt_text = (
        "🔢 **تحديد عدد الحسابات للإنشاء التلقائي المتتابع:**\n\n"
        f"• **الإيميلات المتوفرة في قائمتك:** `{total}` إيميل\n"
        "• سيقوم البوت بإنشائها بالتتابع، وحذف كل حساب يُنشأ بنجاح فوراً من القائمة!\n\n"
        "👇 **اختر من الأزرار أو اكتب أي عدد تريده في المحادثة** (مثال: `5`, `10`, `66`):"
    )

    quick_row = []
    for count in [5, 10, 20, 50]:
        if total >= count:
            quick_row.append(InlineKeyboardButton(f"{count}", callback_data=f"act_batch_{count}"))

    buttons = [
        [InlineKeyboardButton(f"🌟 كل الإيميلات المضافة ({total})", callback_data=f"act_batch_{total}")],
    ]
    if quick_row:
        buttons.append(quick_row)
    buttons.append([
        InlineKeyboardButton("❌ إلغاء العملية", callback_data="act_cancel"),
    ])

    markup = InlineKeyboardMarkup(buttons)
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await update.callback_query.message.reply_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
    return CHOOSE_BATCH_COUNT


async def handle_batch_count_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    queue = get_user_queued_emails(user_id)
    if not queue:
        await update.effective_message.reply_text("📭 قائمتك فارغة!")
        return ConversationHandler.END

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data
        if data == "act_choose_manual":
            return await choose_manual_callback(update, context)
        elif data == "act_choose_queue":
            return await choose_queue_prompt(update, context)
        elif data.startswith("act_batch_"):
            target_count = int(data.replace("act_batch_", ""))
        else:
            return CHOOSE_BATCH_COUNT
        msg_target = query.message
    else:
        raw = update.message.text.strip()
        if not raw.isdigit():
            await update.message.reply_text(
                f"⚠️ يرجى إدخال رقم صحيح بين 1 و {len(queue)}:\n(مثال: 5 أو 10 أو 66)",
                reply_markup=get_cancel_keyboard(),
            )
            return CHOOSE_BATCH_COUNT
        target_count = int(raw)
        if target_count < 1 or target_count > len(queue):
            await update.message.reply_text(
                f"⚠️ العدد يجب أن يكون بين 1 و {len(queue)} (عدد إيميلاتك المتوفرة).\nأعد إدخال الرقم:",
                reply_markup=get_cancel_keyboard(),
            )
            return CHOOSE_BATCH_COUNT
        msg_target = update.message

    batch_emails = queue[:target_count]
    context.user_data["batch_info"] = {
        "user_id": user_id,
        "total": target_count,
        "current_index": 0,
        "emails": batch_emails,
        "password": "",
        "base_name": "dahoom",
        "success_count": 0,
        "failed_emails": [],
    }

    user_settings = get_user_settings(user_id)
    has_perm_pass = user_settings.get("perm_pass_enabled") and user_settings.get("perm_password")
    has_perm_name = user_settings.get("perm_name_enabled") and user_settings.get("perm_name")

    if has_perm_name:
        context.user_data["batch_info"]["base_name"] = user_settings["perm_name"]

    if has_perm_pass:
        context.user_data["batch_info"]["password"] = user_settings["perm_password"]
        return await start_batch_next_account(msg_target, context)
    else:
        prompt = (
            f"🔐 **تم تحديد `{target_count}` حساب للإنشاء التلقائي.**\n\n"
            "أدخل الآن كلمة المرور التي تريد استخدامها لجميع هذه الحسابات:\n"
            "(يجب أن تكون 8 خانات على الأقل وتتضمن حروفاً وأرقاماً):\n\n"
            "💡 نصيحة: يمكنك تفعيل 'كلمة المرور الدائمة' لتطبيقها تلقائياً دائماً دون الحاجة لكتابتها!"
        )
        if update.callback_query:
            await msg_target.reply_text(prompt, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
        else:
            await msg_target.reply_text(prompt, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
        return BATCH_PASSWORD


async def batch_password_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    val = update.message.text.strip()
    if len(val) < 8 or not re.search(r"[A-Za-z]", val) or not re.search(r"\d", val):
        await update.message.reply_text(
            "⚠️ كلمة المرور ضعيفة. يجب أن تحتوي على 8 خانات على الأقل وتتضمن حروفاً وأرقاماً:\n"
            "أعد إرسال كلمة المرور:",
            reply_markup=get_cancel_keyboard(),
        )
        return BATCH_PASSWORD

    context.user_data["batch_info"]["password"] = val
    return await start_batch_next_account(update.message, context)


async def start_batch_next_account(msg_target, context: ContextTypes.DEFAULT_TYPE) -> int:
    batch_info = context.user_data.get("batch_info")
    if not batch_info:
        return ConversationHandler.END

    idx = batch_info["current_index"]
    total = batch_info["total"]
    emails = batch_info["emails"]

    if idx >= total or idx >= len(emails):
        return await finish_batch(msg_target, context)

    current_email = emails[idx]
    pwd = batch_info["password"]
    base = batch_info["base_name"]

    session = RegistrationSession()
    context.user_data["session"] = session
    session.email = current_email
    session.password = pwd
    session.base_name = base

    return await launch_browser_and_request_otp(msg_target, session, base, context)


async def finish_batch(msg_target, context: ContextTypes.DEFAULT_TYPE) -> int:
    batch_info = context.user_data.get("batch_info", {})
    total = batch_info.get("total", 0)
    success = batch_info.get("success_count", 0)
    failed = batch_info.get("failed_emails", [])
    user_id = batch_info.get("user_id", 0)
    remaining_queue = len(get_user_queued_emails(user_id)) if user_id else 0

    await cleanup_session(context, force=True)
    context.user_data["batch_info"] = None

    summary_text = (
        "🎉 **اكتملت عملية الإنشاء التلقائي المتتابع بنجاح!**\n"
        "═════════════════════════════\n"
        f"✅ **الحسابات التي تم إنشاؤها وتجهيزها بنجاح:** `{success}` من `{total}`\n"
        "🗑️ **حالة القائمة:** تم حذف الحسابات الناجحة تلقائياً من قائمتك لعدم التكرار.\n"
        f"📊 **الإيميلات المتبقية في قائمتك:** `{remaining_queue}` إيميل\n\n"
    )
    if failed:
        summary_text += f"⚠️ إيميلات مسجلة مسبقاً في شاهد: `{len(failed)}`\n\n"

    summary_text += (
        "💾 جميع الحسابات أصبحت جاهزة في قسم **'📋 حساباتي'**.\n"
        "📥 يمكنك تصديرها كملف نصي بضغطة زر واحدة أدناه."
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 عرض حساباتي", callback_data="act_my_accounts"),
            InlineKeyboardButton("📥 تصدير الحسابات", callback_data="act_export"),
        ],
        [
            InlineKeyboardButton("📬 قائمة الإيميلات", callback_data="act_email_queue"),
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])

    await msg_target.reply_text(summary_text, reply_markup=markup, parse_mode="Markdown")
    return ConversationHandler.END


async def email_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    val = update.message.text.strip()
    if not EMAIL_RE.match(val):
        await update.message.reply_text(
            "❌ صيغة البريد غير صحيحة. يرجى إرسال بريد إلكتروني صالح:",
            reply_markup=get_cancel_keyboard(),
        )
        return EMAIL

    session = get_session(context)
    session.email = val

    # Check settings for permanent password and permanent name
    user_settings = get_user_settings(update.effective_user.id)
    has_perm_pass = user_settings.get("perm_pass_enabled") and user_settings.get("perm_password")
    has_perm_name = user_settings.get("perm_name_enabled") and user_settings.get("perm_name")

    if has_perm_pass:
        perm_pwd = user_settings["perm_password"]
        session.password = perm_pwd

        if has_perm_name:
            perm_name = user_settings["perm_name"]
            session.base_name = perm_name
            await update.message.reply_text(
                f"✅ تم حفظ البريد: `{val}`\n"
                f"🔐 **تم تطبيق كلمة المرور الدائمة:** `{perm_pwd}`\n"
                f"👤 **تم تطبيق اسم الملفات المحفوظ:** `{perm_name}`\n\n"
                f"🚀 جاري الانطلاق مباشرة لطلب كود التحقق وتجهيز `{perm_name}1` إلى `{perm_name}4`...",
                parse_mode="Markdown"
            )
            return await launch_browser_and_request_otp(update.message, session, perm_name, context)

        await update.message.reply_text(
            f"✅ تم حفظ البريد: `{val}`\n"
            f"🔐 **تم تطبيق كلمة المرور الدائمة تلقائياً:** `{perm_pwd}`\n\n"
            "👤 **أدخل الآن الاسم الذي تريده للملفات الشخصية:**\n"
            "*(مثال: إذا أدخلت `dahoom` ستكون الملفات `dahoom1`, `dahoom2`...)*\n"
            "*(أو إذا أدخلت `noor` ستكون الملفات `noor1`, `noor2`...)*\n\n"
            "💡 أو اضغط على الزر أدناه لاعتماد الاسم الافتراضي (`dahoom`):",
            reply_markup=get_name_keyboard(),
            parse_mode="Markdown"
        )
        return BASE_NAME

    await update.message.reply_text(
        f"✅ تم حفظ البريد: `{val}`\n\n"
        "🔐 **أدخل الآن كلمة المرور التي تريد تعيينها للحساب:**\n"
        "(يجب أن تكون 8 رموز على الأقل وتحتوي على حروف وأرقام):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown"
    )
    return PASSWORD


async def password_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    val = update.message.text.strip()
    if len(val) < 8 or not re.search(r"[A-Za-z]", val) or not re.search(r"\d", val):
        await update.message.reply_text(
            "⚠️ كلمة المرور ضعيفة. يجب أن تحتوي على 8 خانات على الأقل وتتضمن حروفاً وأرقاماً:\n"
            "أعد إرسال كلمة المرور:",
            reply_markup=get_cancel_keyboard(),
        )
        return PASSWORD

    session = get_session(context)
    session.password = val

    user_settings = get_user_settings(update.effective_user.id)
    has_perm_name = user_settings.get("perm_name_enabled") and user_settings.get("perm_name")

    if has_perm_name:
        perm_name = user_settings["perm_name"]
        session.base_name = perm_name
        await update.message.reply_text(
            "✅ **تم حفظ كلمة المرور بنجاح.**\n"
            f"👤 **تم تطبيق اسم الملفات المحفوظ تلقائياً:** `{perm_name}`\n\n"
            f"🚀 جاري الانطلاق مباشرة لطلب كود التحقق وتجهيز `{perm_name}1` إلى `{perm_name}4`...",
            parse_mode="Markdown"
        )
        return await launch_browser_and_request_otp(update.message, session, perm_name, context)

    await update.message.reply_text(
        "✅ **تم حفظ كلمة المرور بنجاح.**\n\n"
        "👤 **أدخل الآن الاسم الذي تريده للملفات الشخصية:**\n"
        "*(مثال: إذا أدخلت `dahoom` ستكون الملفات `dahoom1` و `dahoom2` و `dahoom3` و `dahoom4`)*\n"
        "*(أو إذا أدخلت `noor` ستكون الملفات `noor1` و `noor2` و `noor3` و `noor4`)*\n\n"
        "💡 أو اضغط على الزر أدناه لاعتماد الاسم الافتراضي (`dahoom`):",
        reply_markup=get_name_keyboard(),
        parse_mode="Markdown"
    )
    return BASE_NAME


async def launch_browser_and_request_otp(msg_target, session: RegistrationSession, base: str, context: ContextTypes.DEFAULT_TYPE = None) -> int:
    batch_prefix = ""
    batch_hint = "💡 يرجى إدخال كود التحقق لإكمال إعداد الحساب فوراً."
    if context and context.user_data.get("batch_info"):
        bi = context.user_data["batch_info"]
        c_num = bi.get("current_index", 0) + 1
        b_tot = bi.get("total", 0)
        batch_prefix = f"🔄 **[الإنشاء التلقائي: حساب {c_num} من {b_tot}]**\n\n"
        batch_hint = "💡 فور إدخال الرمز، سينتهي إعداد هذا الحساب وينتقل البوت تلقائياً للإيميل التالي!"

    status_msg = await msg_target.reply_text(
        batch_prefix +
        f"✅ **تم اعتماد اسم الملفات:** `{base}1`, `{base}2`, `{base}3`, `{base}4`\n\n"
        "⚡ جاري التواصل مع Shahid وطلب كود التحقق... انتظر لحظات.",
        parse_mode="Markdown"
    )

    try:
        # Launch stealth browser session
        p = await async_playwright().start()
        session.playwright = p

        # Determine headless mode:
        # 1. If explicit HEADLESS env var is set, honor it.
        # 2. On Linux/Unix VPS, if no DISPLAY or WAYLAND_DISPLAY is found, automatically run headless to prevent crash.
        env_headless = os.getenv("HEADLESS")
        if env_headless is not None:
            is_headless = env_headless.strip().lower() in ("1", "true", "yes", "y")
        else:
            if os.name != "nt" and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
                logger.info("No DISPLAY detected on Linux VPS. Running Chromium in headless mode.")
                is_headless = True
            else:
                is_headless = False

        chromium_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if not is_headless:
            chromium_args.append("--start-maximized")

        proxy_server = os.getenv("PROXY_SERVER")
        proxy_config = None
        if proxy_server:
            proxy_config = {"server": proxy_server}
            if os.getenv("PROXY_USERNAME"):
                proxy_config["username"] = os.getenv("PROXY_USERNAME")
                proxy_config["password"] = os.getenv("PROXY_PASSWORD", "")

        browser = await p.chromium.launch(
            headless=is_headless,
            args=chromium_args,
            proxy=proxy_config,
        )
        session.browser = browser

        bcontext = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ar-SA",
            timezone_id="Asia/Riyadh",
            permissions=["geolocation"],
        )
        session.context = bcontext

        page = await bcontext.new_page()
        session.page = page

        # Apply stealth evasions
        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        # Navigate to registration hub
        await page.goto("https://shahid.mbc.net/ar/hub/registration", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Enter email with human-like typing
        user_field = page.locator("#userName")
        if not await user_field.is_visible():
            await user_field.wait_for(timeout=5000)

        await human_type(page, "#userName", session.email)
        await asyncio.sleep(0.5)

        # Click submit
        submit_btn = page.locator("button[type='submit']")
        await submit_btn.click()

        # Wait for transition
        await asyncio.sleep(4)

        # First: Check if we successfully reached the OTP verification page
        otp_field = page.locator("#otpCode")
        is_otp = False
        try:
            if await otp_field.is_visible(timeout=5000) or "/hub/verification" in page.url:
                is_otp = True
        except Exception:
            pass

        if is_otp:
            await status_msg.edit_text(
                batch_prefix +
                f"📨 تم إرسال رمز التحقق بنجاح إلى بريدك الإلكتروني:\n`{session.email}`\n\n"
                "🔢 **أدخل رمز التحقق (OTP) المكوّن من 6 أرقام هنا لإكمال التسجيل:**\n\n"
                f"👥 سيتم تجهيز الملفات: `{base}1` إلى `{base}4`\n" +
                batch_hint,
                reply_markup=get_cancel_keyboard(),
                parse_mode="Markdown"
            )
            return CODE

        # Only if NOT on OTP screen, check for redirects or block banners:
        # 1. Check if email is already registered (redirected to login)
        if "/hub/login" in page.url:
            if context and context.user_data.get("batch_info"):
                bi = context.user_data["batch_info"]
                pop_user_queued_email(bi["user_id"], session.email)
                bi["failed_emails"].append((session.email, "مسجل مسبقاً"))
                bi["current_index"] += 1
                curr = bi["current_index"]
                tot = bi["total"]
                if curr < tot:
                    await status_msg.edit_text(
                        f"⚠️ البريد `{session.email}` مسجل مسبقاً في شاهد!\n"
                        "🗑️ تم حذفه تلقائياً من قائمتك لتفادي التكرار.\n\n"
                        f"🚀 **جاري الانتقال تلقائياً للحساب التالي [{curr + 1} من {tot}]...**",
                        parse_mode="Markdown"
                    )
                    await cleanup_session(context, force=True)
                    await asyncio.sleep(2.0)
                    return await start_batch_next_account(msg_target, context)
                else:
                    return await finish_batch(msg_target, context)

            await status_msg.edit_text(
                "⚠️ هذا البريد مسجل مسبقاً في Shahid! يرجى استخدام بريد إلكتروني جديد.",
                reply_markup=get_main_inline_keyboard(),
            )
            return ConversationHandler.END

        # 2. Check for actual block banner
        body_text = await page.locator("body").inner_text()
        if "تقييد الوصول" in body_text or "نشاط غير معتاد" in body_text:
            await status_msg.edit_text(
                "⚠️ تعذر استكمال الطلب حالياً من موقع شاهد بسبب ضغط في الشبكة. يرجى المحاولة بعد قليل.",
                reply_markup=get_main_inline_keyboard(),
            )
            return ConversationHandler.END

        # Fallback: prompt for OTP
        await status_msg.edit_text(
            batch_prefix +
            f"📨 تم إرسال رمز التحقق إلى بريدك الإلكتروني:\n`{session.email}`\n\n"
            "🔢 أدخل رمز التحقق (OTP) المكوّن من 6 أرقام هنا لإكمال التسجيل:\n" +
            batch_hint,
            reply_markup=get_cancel_keyboard(),
            parse_mode="Markdown"
        )
        return CODE

    except Exception as exc:
        await status_msg.edit_text(
            "⚠️ تعذر الاتصال بموقع شاهد حالياً، يرجى المحاولة مجدداً.",
            reply_markup=get_cancel_keyboard(),
        )
        return CODE


async def base_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    session = get_session(context)
    base = "dahoom"

    if update.callback_query:
        await update.callback_query.answer()
        base = "dahoom"
        msg = update.callback_query.message
    else:
        raw = update.message.text.strip()
        cleaned = re.sub(r"[^\w\s-]", "", raw).strip()
        if cleaned:
            base = cleaned[:18]
        else:
            base = "dahoom"
        msg = update.message

    session.base_name = base
    return await launch_browser_and_request_otp(msg, session, base, context)


async def code_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    val = update.message.text.strip()
    if not re.fullmatch(r"\d{6}", val):
        await update.message.reply_text(
            "⚠️ يرجى إدخال رمز صحيح مكوّن من 6 أرقام فقط:",
            reply_markup=get_cancel_keyboard(),
        )
        return CODE

    session = get_session(context)
    page = session.page

    if not page or page.is_closed():
        await update.message.reply_text(
            "❌ انتهت صلاحية الجلسة، اختر من القائمة للبدء مجدداً:",
            reply_markup=get_main_inline_keyboard(),
        )
        return ConversationHandler.END

    status_msg = await update.message.reply_text("⏳ جاري تأكيد الرمز وإكمال التسجيل...")

    try:
        otp_field = page.locator("#otpCode")
        if not await otp_field.is_visible():
            await otp_field.wait_for(timeout=5000)

        # Type OTP
        await human_type(page, "#otpCode", val)
        await asyncio.sleep(0.5)

        # Click submit
        submit_btn = page.locator("button[type='submit']")
        await submit_btn.click()

        # Wait for processing
        await asyncio.sleep(3)

        # Check for incorrect code error
        body_text = await page.locator("body").inner_text()
        if "رمز غير صحيح" in body_text or "كود غير صحيح" in body_text or "invalid" in body_text.lower():
            await status_msg.edit_text(
                "❌ رمز التحقق غير صحيح. يرجى التأكد وإعادة إرسال الرمز الصحيح:",
                reply_markup=get_cancel_keyboard(),
            )
            return CODE

        # Wait for password page to appear
        pwd_field = page.locator("input[type='password']")
        try:
            await pwd_field.wait_for(state="visible", timeout=15000)
        except Exception:
            pass

        if not await pwd_field.is_visible():
            await status_msg.edit_text(
                "⚠️ لم يتم قبول الرمز أو قد يكون منتهي الصلاحية.\nيرجى التأكد وإعادة إرسال رمز التحقق الصحيح:",
                reply_markup=get_cancel_keyboard(),
            )
            return CODE

        # 1. Type the password with comfortable human delay
        await human_type(page, "input[type='password']", session.password)
        await asyncio.sleep(1.5)

        # 2. Close cookie banner if present to avoid interference
        try:
            cookie_btn = page.locator("button:has-text('حسناً'), #onetrust-accept-btn-handler")
            if await cookie_btn.is_visible():
                await asyncio.sleep(0.8)
                await cookie_btn.click()
                await asyncio.sleep(0.8)
        except Exception:
            pass

        # 3. Defuse <a> links to prevent any accidental navigation away
        await page.evaluate("""() => {
            document.querySelectorAll('a').forEach(a => {
                a.onclick = (e) => { e.preventDefault(); e.stopPropagation(); return false; };
                a.removeAttribute('href');
            });
        }""")
        await asyncio.sleep(1.0)

        # 4. Check the first checkbox (Promotions) with visible pause
        await page.evaluate("""() => {
            const el = document.getElementById('promotionsNews');
            if (el && !el.checked) {
                el.click();
            }
        }""")
        await asyncio.sleep(1.2)

        # 5. Check the second checkbox (Terms & Privacy) with visible pause
        await page.evaluate("""() => {
            const el = document.getElementById('termsConditions');
            if (el && !el.checked) {
                el.click();
            }
        }""")
        await asyncio.sleep(1.8)

        # 6. Click submit / create account button with pause
        submit_btn = page.locator("button:has-text('إنشاء حساب'), button[type='submit']")
        try:
            await submit_btn.click(timeout=5000)
        except Exception:
            await submit_btn.evaluate("btn => btn.click()")

        # 7. Verify URL transition (Shahid redirects to /hub/onboarding or profile on success)
        await asyncio.sleep(5)
        current_url = page.url

        if "set-password" in current_url:
            body = await page.locator("body").inner_text()
            error_line = "لم يتم تأكيد الحساب بعد أو هناك خطأ بكلمة المرور."
            for line in body.split("\n"):
                clean = line.strip()
                if any(w in clean for w in ["خطأ", "كلمة السر يجب", "غير صحيح", "مطلوب"]):
                    error_line = clean
                    break
            await status_msg.edit_text(
                f"⚠️ تنبيه: لم يكتمل إنشاء الحساب بعد.\nالسبب: {error_line}\n\nيرجى المحاولة مجدداً.",
                reply_markup=get_main_inline_keyboard(),
            )
            return ConversationHandler.END

        # Account created successfully! Now automate setting up all 4 profiles
        base = getattr(session, "base_name", "dahoom") or "dahoom"
        await status_msg.edit_text(
            "🎉 **تم تأكيد الحساب بنجاح!**\n\n"
            f"⏳ جاري الآن تجهيز كافة الملفات الشخصية تلقائياً (`{base}1` إلى `{base}4`)...\n"
            "يرجى الانتظار لحظات..."
        )

        # Run profile automation sequence
        await automate_all_profiles(page, status_msg, base_name=base)

        # Save account locally
        user_id = update.effective_user.id
        profiles_list = [f"{base}1", f"{base}2", f"{base}3", f"{base}4"]
        save_account(user_id, session.email, session.password, profiles_list)

        # Remove successfully created email from user's queue immediately so it never repeats!
        pop_user_queued_email(user_id, session.email)

        # Check if batch mode is active
        batch_info = context.user_data.get("batch_info")
        if batch_info:
            batch_info["success_count"] += 1
            batch_info["current_index"] += 1
            curr_done = batch_info["current_index"]
            tot = batch_info["total"]

            if curr_done < tot:
                next_email = batch_info["emails"][curr_done]
                await status_msg.edit_text(
                    f"✅ **[تم إنجاز الحساب {curr_done} من {tot} بنجاح!]**\n\n"
                    f"📧 البريد: `{session.email}`\n"
                    f"👤 الملفات: `{base}1` إلى `{base}4`\n"
                    "🗑️ تم حذف الإيميل تلقائياً من قائمتك لعدم التكرار.\n"
                    "──────────────────────────\n"
                    f"🚀 **جاري الانتقال تلقائياً للحساب التالي [{curr_done + 1} من {tot}]...**\n"
                    f"📧 البريد: `{next_email}`",
                    parse_mode="Markdown"
                )
                await cleanup_session(context, force=True)
                await asyncio.sleep(2.0)
                return await start_batch_next_account(update.effective_message, context)
            else:
                return await finish_batch(update.effective_message, context)

        success_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 عرض حساباتي", callback_data="act_my_accounts"),
                InlineKeyboardButton("➕ إنشاء حساب آخر", callback_data="act_create"),
            ],
            [
                InlineKeyboardButton("📥 تصدير كملف نصي", callback_data="act_export"),
                InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
            ]
        ])

        # Final success message with all 4 profiles confirmed
        await status_msg.edit_text(
            "🎉 **تهانينا! تم إنشاء وتجهيز الحساب بالكامل في Shahid!**\n\n"
            f"📧 البريد: `{session.email}`\n"
            f"🔑 كلمة المرور: `{session.password}`\n\n"
            "👤 **الملفات الشخصية المجهزة:**\n"
            f"1️⃣ `{base}1`\n"
            f"2️⃣ `{base}2`\n"
            f"3️⃣ `{base}3`\n"
            f"4️⃣ `{base}4`\n\n"
            "💾 *تم حفظ الحساب تلقائياً في قائمة حساباتك!*\n"
            "✨ الحساب جاهز الآن للاستخدام والمشاهدة بأعلى جودة!",
            reply_markup=success_markup,
            parse_mode="Markdown"
        )

    except Exception as exc:
        batch_info = context.user_data.get("batch_info")
        if batch_info:
            curr_idx = batch_info.get("current_index", 0)
            tot = batch_info.get("total", 0)
            batch_info["failed_emails"].append((session.email, str(exc)))
            batch_info["current_index"] += 1
            if batch_info["current_index"] < batch_info["total"]:
                await status_msg.edit_text(
                    f"⚠️ حدث تنبيه في الحساب [{curr_idx + 1} من {tot}]: {exc}\n\n"
                    "⏭️ جاري الانتقال تلقائياً إلى الإيميل التالي في القائمة...",
                    parse_mode="Markdown"
                )
                await cleanup_session(context, force=True)
                await asyncio.sleep(2.0)
                return await start_batch_next_account(update.effective_message, context)
            else:
                return await finish_batch(update.effective_message, context)

        await status_msg.edit_text(
            "⚠️ تعذر استكمال إنشاء الحساب حالياً، يرجى المحاولة مجدداً.",
            reply_markup=get_main_inline_keyboard(update.effective_user.id if update.effective_user else 0),
        )

    # Note: We intentionally do NOT close the browser here so it remains open on screen!
    return ConversationHandler.END


async def show_accounts(user_id: int, send_fn, edit: bool = False) -> None:
    """Renders the list of saved accounts with management buttons."""
    accounts = get_user_accounts(user_id)
    if not accounts:
        text = (
            "📭 **لا توجد لديك حسابات مسجلة حتى الآن.**\n\n"
            "اضغط على زر **'➕ إنشاء حساب جديد'** لإنشاء وتجهيز أول حساب لك!"
        )
        markup = get_accounts_keyboard(has_accounts=False)
    else:
        text = f"📋 **قائمة حساباتك المسجلة في Shahid** (الإجمالي: {len(accounts)}):\n\n"
        for idx, acc in enumerate(accounts, 1):
            profiles_str = " | ".join(acc.get("profiles", []))
            text += (
                f"**{idx}️⃣ الحساب #{idx}:**\n"
                f"📧 البريد: `{acc['email']}`\n"
                f"🔑 كلمة المرور: `{acc['password']}`\n"
                f"👤 الملفات: `{profiles_str}`\n"
                f"📅 التاريخ: `{acc.get('created_at', 'غير متوفر')}`\n"
                "──────────────────\n"
            )
        text += "💡 *اضغط على البريد أو كلمة المرور لنسخها فوراً.*"
        markup = get_accounts_keyboard(has_accounts=True)

    if edit:
        try:
            await send_fn(text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    await send_fn(text, reply_markup=markup, parse_mode="Markdown")


async def my_accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_accounts(update.effective_user.id, update.effective_message.reply_text)


async def my_accounts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await show_accounts(update.effective_user.id, query.message.edit_text, edit=True)


async def export_accounts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)
    if update.callback_query:
        await update.callback_query.answer()

    if not accounts:
        msg = "📭 لا توجد حسابات مسجلة لديك لتصديرها."
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    buf = io.StringIO()
    buf.write(f"=== SHAHID ACCOUNTS LIST ({len(accounts)}) ===\n")
    buf.write(f"Exported At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    for idx, acc in enumerate(accounts, 1):
        buf.write(f"[{idx}] {acc['email']}:{acc['password']}\n")
        buf.write(f"    Profiles: {', '.join(acc.get('profiles', []))}\n")
        buf.write(f"    Date: {acc.get('created_at', '')}\n\n")

    bytes_file = io.BytesIO(buf.getvalue().encode("utf-8"))
    bytes_file.name = f"shahid_accounts_{user_id}.txt"

    chat_id = update.effective_chat.id
    await context.bot.send_document(
        chat_id=chat_id,
        document=bytes_file,
        caption=f"📁 تم تصدير {len(accounts)} حساب في ملف نصي.",
        reply_markup=get_main_inline_keyboard(),
    )


async def confirm_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    confirm_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ نعم، احذف الكل", callback_data="act_do_clear"),
            InlineKeyboardButton("❌ تراجع", callback_data="act_my_accounts"),
        ]
    ])
    await query.message.edit_text(
        "⚠️ **هل أنت متأكد من رغبتك في حذف جميع حساباتك المحفوظة؟**\n\n"
        "لا يمكن التراجع عن هذه الخطوة بعد التأكيد.",
        reply_markup=confirm_markup,
        parse_mode="Markdown"
    )


async def do_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    clear_user_accounts(user_id)
    back_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu")]
    ])
    await query.message.edit_text(
        "✅ **تم حذف جميع حساباتك المحفوظة بنجاح.**",
        reply_markup=back_markup,
        parse_mode="Markdown"
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(
                HELP_TEXT,
                reply_markup=get_main_inline_keyboard(user_id),
                parse_mode="Markdown"
            )
            return
        except Exception:
            pass
    await update.effective_message.reply_text(
        HELP_TEXT,
        reply_markup=get_main_inline_keyboard(user_id),
        parse_mode="Markdown"
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    try:
        await query.message.edit_text(
            WELCOME_TEXT,
            reply_markup=get_main_inline_keyboard(user_id),
            parse_mode="Markdown"
        )
    except Exception:
        await query.message.reply_text(
            WELCOME_TEXT,
            reply_markup=get_main_inline_keyboard(user_id),
            parse_mode="Markdown"
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await cleanup_session(context, force=True)
    user_id = update.effective_user.id

    batch_info = context.user_data.get("batch_info")
    if batch_info:
        succ = batch_info.get("success_count", 0)
        tot = batch_info.get("total", 0)
        rem = len(get_user_queued_emails(user_id))
        msg_text = (
            "🚫 **تم إيقاف عملية الإنشاء التلقائي.**\n\n"
            f"• الحسابات التي تم إنجازها بنجاح: `{succ}` من `{tot}`\n"
            f"• تم حفظ الحسابات الناجحة وحذفها من القائمة تلقائياً.\n"
            f"• الإيميلات المتبقية في قائمتك: `{rem}` إيميل محفوظة بأمان."
        )
        context.user_data["batch_info"] = None
    else:
        msg_text = "🚫 **تم إلغاء العملية.**\n\nاختر من الأزرار أدناه للبدء من جديد:"
    if update.callback_query:
        await update.callback_query.answer("تم الإلغاء")
        try:
            await update.callback_query.message.edit_text(
                msg_text,
                reply_markup=get_main_inline_keyboard(user_id),
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        except Exception:
            pass

    await update.effective_message.reply_text(
        msg_text,
        reply_markup=get_main_inline_keyboard(user_id),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def show_perm_pass_settings(user_id: int, send_or_edit_fn, is_edit: bool = False) -> None:
    settings = get_user_settings(user_id)
    enabled = settings.get("perm_pass_enabled", False)
    pwd = settings.get("perm_password", "")

    status_icon = "🟢 **مفعلة**" if enabled else "🔴 **معطلة**"
    pwd_display = f"`{pwd}`" if pwd else "*لم تُحدد بعد*"

    text = (
        "🔐 **إعدادات كلمة المرور الدائمة:**\n\n"
        f"• **الحالة:** {status_icon}\n"
        f"• **كلمة المرور الحالية:** {pwd_display}\n\n"
        "💡 **كيف تعمل هذه الميزة؟**\n"
        "عند تفعيلها، لن يسألك البوت عن كلمة المرور في كل مرة تنشئ فيها حساباً، "
        "بل سيقوم بوضع هذه الكلمة تلقائياً في جميع الحسابات الجديدة!\n\n"
        "👇 اختر الإجراء المطلوب أدناه:"
    )
    markup = get_perm_pass_keyboard(enabled=enabled, has_pass=bool(pwd))

    if is_edit:
        try:
            await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")


async def perm_pass_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_perm_pass_settings(update.effective_user.id, update.effective_message.reply_text, is_edit=False)


async def perm_pass_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await show_perm_pass_settings(update.effective_user.id, query.message.edit_text, is_edit=True)


async def toggle_perm_pass_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)

    if not settings.get("perm_password"):
        await query.message.reply_text(
            "⚠️ **يجب تحديد كلمة المرور أولاً قبل التفعيل!**\n"
            "اضغط على زر '➕ تعيين كلمة المرور' لكتابتها.",
            parse_mode="Markdown"
        )
        return

    toggle_user_perm_password(user_id)
    await show_perm_pass_settings(user_id, query.message.edit_text, is_edit=True)


async def edit_perm_pass_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prompt = (
        "🔐 **أدخل كلمة المرور الدائمة الجديدة التي تريد استخدامها لكل الحسابات:**\n"
        "(يجب أن تكون 8 خانات على الأقل وتتضمن حروفاً وأرقاماً)\n\n"
        "💡 للإلغاء والتراجع اضغط الزر أدناه:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء", callback_data="act_perm_pass")]
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    return SET_PERM_PASS


async def save_perm_pass_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    val = update.message.text.strip()
    if len(val) < 8 or not re.search(r"[A-Za-z]", val) or not re.search(r"\d", val):
        await update.message.reply_text(
            "⚠️ كلمة المرور ضعيفة. يجب أن تحتوي على 8 خانات على الأقل وتتضمن حروفاً وأرقاماً:\n"
            "أعد إرسال كلمة المرور:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="act_perm_pass")]
            ]),
        )
        return SET_PERM_PASS

    user_id = update.effective_user.id
    set_user_perm_password(user_id, val)

    await update.message.reply_text(
        f"✅ **تم تعيين وتفعيل كلمة المرور الدائمة بنجاح!**\n\n"
        f"🔑 كلمة المرور: `{val}`\n"
        "🟢 أصبحت الميزة **مفعلة** وستُطبّق تلقائياً على كل الحسابات الجديدة دون أن يسألك البوت عنها.",
        reply_markup=get_perm_pass_keyboard(enabled=True, has_pass=True),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def show_perm_name_settings(user_id: int, send_or_edit_fn, is_edit: bool = False) -> None:
    settings = get_user_settings(user_id)
    enabled = settings.get("perm_name_enabled", False)
    name = settings.get("perm_name", "")

    status_icon = "🟢 **مفعلة**" if enabled else "🔴 **معطلة**"
    name_display = f"`{name}`" if name else "*لم يُحدد بعد*"

    text = (
        "👤 **إعدادات اسم الملفات المحفوظ مسبقاً:**\n\n"
        f"• **الحالة:** {status_icon}\n"
        f"• **الاسم المحفوظ حالياً:** {name_display}\n"
    )
    if name:
        text += f"• **أسماء الملفات الناتجة:** `{name}1` | `{name}2` | `{name}3` | `{name}4`\n\n"
    else:
        text += "\n"

    text += (
        "💡 **كيف تعمل هذه الميزة؟**\n"
        "عند تفعيل هذه الميزة، لن يطلب منك البوت كتابة اسم للملفات في كل مرة تنشئ فيها حساباً، "
        "بل سيقوم بتسمية وتجهيز الملفات تلقائياً بالاسم المحفوظ لديك!\n\n"
        "👇 اختر الإجراء المطلوب أدناه:"
    )
    markup = get_perm_name_keyboard(enabled=enabled, has_name=bool(name))

    if is_edit:
        try:
            await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")


async def perm_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_perm_name_settings(update.effective_user.id, update.effective_message.reply_text, is_edit=False)


async def perm_name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await show_perm_name_settings(update.effective_user.id, query.message.edit_text, is_edit=True)


async def toggle_perm_name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    settings = get_user_settings(user_id)

    if not settings.get("perm_name"):
        await query.message.reply_text(
            "⚠️ **يجب تحديد اسم للملفات أولاً قبل التفعيل!**\n"
            "اضغط على زر '➕ تعيين الاسم المحفوظ' لكتابته.",
            parse_mode="Markdown"
        )
        return

    toggle_user_perm_name(user_id)
    await show_perm_name_settings(user_id, query.message.edit_text, is_edit=True)


async def edit_perm_name_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prompt = (
        "👤 **أدخل الآن الاسم الدائم الذي تريده لملفات الحسابات:**\n"
        "*(مثال: إذا أدخلت `noor` فسيتم دائماً إنشاء `noor1`, `noor2`, `noor3`, `noor4`)*\n"
        "*(أو إذا أدخلت `dahoom` فسيتم إنشاء `dahoom1`, `dahoom2`...)*\n\n"
        "💡 للإلغاء والتراجع اضغط الزر أدناه:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء", callback_data="act_perm_name")]
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    return SET_PERM_NAME


async def save_perm_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    cleaned = re.sub(r"[^\w\s-]", "", raw).strip()
    if not cleaned:
        await update.message.reply_text(
            "⚠️ الاسم غير صالح. يرجى إدخال اسم مكون من أحرف أو أرقام:\n"
            "أعد إرسال الاسم:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="act_perm_name")]
            ]),
        )
        return SET_PERM_NAME

    base = cleaned[:18]
    user_id = update.effective_user.id
    set_user_perm_name(user_id, base)

    await update.message.reply_text(
        f"✅ **تم حفظ وتفعيل اسم الملفات الدائم بنجاح!**\n\n"
        f"👤 الاسم المحفوظ: `{base}`\n"
        f"👥 الملفات التي ستُنشأ تلقائياً: `{base}1` | `{base}2` | `{base}3` | `{base}4`\n\n"
        "🟢 أصبحت الميزة **مفعلة** وستُطبّق تلقائياً على كل الحسابات الجديدة.",
        reply_markup=get_perm_name_keyboard(enabled=True, has_name=True),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        await update.effective_message.reply_text(
            f"🆔 **الآيدي الخاص بك (Your Telegram ID):**\n`{user.id}`",
            parse_mode="Markdown"
        )


async def my_sub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    has_access, status, sub_info = check_user_access(user_id)

    if is_admin(user_id):
        text = (
            "👑 **نوع الحساب:** مشرف (Admin)\n\n"
            "✨ تمتلك صلاحيات إدارية كاملة غير مقيدة وغير منتهية الصلاحية.\n"
            "👥 يمكنك إدارة المشتركين عبر زر '👥 المشتركين' أو أمر `/subs`."
        )
    elif has_access:
        started = sub_info.get("started_at", "غير محدد")
        expires = sub_info.get("expires_at", "غير محدد")
        days = sub_info.get("days", "")
        text = (
            "⏳ **تفاصيل اشتراكك في بوت Shahid:**\n\n"
            f"• **الحالة:** 🟢 **نشط ومفعل**\n"
            f"• **مدة الباقة:** {days} يوم\n"
            f"• **تاريخ البدء:** `{started}`\n"
            f"• **تاريخ الانتهاء:** `{expires}`\n"
            f"• **الوقت المتبقي:** ⏱️ **{status}**\n\n"
            "💡 يمكنك إنشاء الحسابات واستخدام كافة مميزات البوت طوال فترة اشتراكك."
        )
    else:
        text = (
            "⚠️ **ليس لديك اشتراك نشط حالياً.**\n\n"
            f"🆔 **الآيدي الخاص بك:** `{user_id}`\n"
            "💡 يرجى التواصل مع الإدارة لتفعيل اشتراكك."
        )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ إنشاء حساب جديد", callback_data="act_create"),
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    await update.effective_message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def show_subscribers_panel(send_fn, edit: bool = False) -> None:
    subs = load_subscribers()
    now = datetime.now()

    active_count = 0
    expired_count = 0
    lines = []

    for idx, (uid, info) in enumerate(subs.items(), 1):
        exp_str = info.get("expires_at", "")
        is_active = False
        remaining_str = ""
        try:
            exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
            if exp_dt > now and info.get("is_active", True):
                is_active = True
                diff = exp_dt - now
                remaining_str = f"{diff.days} يوم و {diff.seconds // 3600} ساعة"
            else:
                remaining_str = "منتهي الصلاحية"
        except Exception:
            remaining_str = "غير صالح"

        if is_active:
            active_count += 1
            status_tag = f"🟢 نشط (متبقي: {remaining_str})"
        else:
            expired_count += 1
            status_tag = f"🔴 منتهي ({exp_str})"

        note = f" ({info.get('note')})" if info.get("note") else ""
        lines.append(f"**{idx}️⃣** `{uid}`{note}\n   └ {status_tag}")

    text = (
        "👥 **نظام نقطة البيع وإدارة المشتركين (POS Subscribers):**\n\n"
        f"📊 **الإحصائيات:** النشطين ({active_count}) | المنتهين ({expired_count}) | الإجمالي ({len(subs)})\n"
        "─────────────────────────\n"
    )
    if lines:
        text += "\n".join(lines) + "\n\n"
    else:
        text += "📭 لا يوجد أي مشتركين مسجلين حالياً.\n\n"

    text += (
        "💡 **أوامر الإدارة السريعة:**\n"
        "• **إضافة / تجديد اشتراك:**\n"
        "  `/addsub <الآيدي> <عدد الأيام>`\n"
        "  *(مثال: `/addsub 987654321 30` لتفعيل 30 يوم)*\n\n"
        "• **إلغاء / حذف مشترك:**\n"
        "  `/delsub <الآيدي>`\n"
        "  *(مثال: `/delsub 987654321`)*"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ إضافة / تجديد مشترك", callback_data="act_prompt_addsub"),
            InlineKeyboardButton("🔄 تحديث القائمة", callback_data="act_subs"),
        ],
        [
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])

    if edit:
        try:
            await send_fn(text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    await send_fn(text, reply_markup=markup, parse_mode="Markdown")


async def subs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ عذراً، هذا الخيار مخصص للمشرفين فقط.")
        return
    await show_subscribers_panel(update.effective_message.reply_text)


async def subs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.message.reply_text("⛔ عذراً، هذا الخيار مخصص للمشرفين فقط.")
        return
    await show_subscribers_panel(query.message.edit_text, edit=True)


async def prompt_addsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    text = (
        "➕ **طريقة إضافة أو تجديد اشتراك لمستخدم:**\n\n"
        "أرسل الأمر في المحادثة بالصيغة التالية:\n"
        "`/addsub <الآيدي> <عدد الأيام>`\n\n"
        "📌 **أمثلة سريعة:**\n"
        "• لتفعيل اشتراك تجريبي يوم واحد:\n"
        "`/addsub 987654321 1`\n"
        "• لتفعيل اشتراك شهر (30 يوم):\n"
        "`/addsub 987654321 30`\n"
        "• لتفعيل اشتراك 3 أشهر (90 يوم):\n"
        "`/addsub 987654321 90`\n"
        "• لتفعيل اشتراك سنة (365 يوم):\n"
        "`/addsub 987654321 365`"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع لقائمة المشتركين", callback_data="act_subs")]
    ])
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def add_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ عذراً، هذا الأمر مخصص للمشرفين فقط.")
        return

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.effective_message.reply_text(
            "⚠️ **صيغة غير صحيحة!**\n"
            "الاستخدام الصحيح:\n"
            "`/addsub <الآيدي> <الأيام>`\n\n"
            "مثال لتفعيل 30 يوم:\n"
            "`/addsub 987654321 30`",
            parse_mode="Markdown"
        )
        return

    sub_id = int(context.args[0])
    days = int(context.args[1])
    if days <= 0:
        await update.effective_message.reply_text("⚠️ يجب أن يكون عدد الأيام أكبر من صفر.")
        return

    admin_id = update.effective_user.id
    record = add_or_renew_subscriber(sub_id, days, created_by=admin_id)

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 عرض قائمة المشتركين", callback_data="act_subs"),
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])

    await update.effective_message.reply_text(
        f"✅ **تم تفعيل / تجديد الاشتراك بنجاح!**\n\n"
        f"👤 **المشترك:** `{sub_id}`\n"
        f"⏱️ **المدة المضافة:** {days} يوم\n"
        f"📅 **تاريخ الانتهاء الجديد:** `{record['expires_at']}`\n\n"
        "✨ تم منح المشترك الصلاحيات الكاملة للبدء في استخدام البوت فوراً!",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def del_sub_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ عذراً، هذا الأمر مخصص للمشرفين فقط.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "⚠️ **صيغة غير صحيحة!**\n"
            "الاستخدام: `/delsub <الآيدي>`\n"
            "مثال: `/delsub 987654321`",
            parse_mode="Markdown"
        )
        return

    sub_id = int(context.args[0])
    removed = remove_subscriber(sub_id)
    if removed:
        await update.effective_message.reply_text(
            f"✅ **تم حذف وإلغاء اشتراك المستخدم (`{sub_id}`) بنجاح.**",
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            f"⚠️ المستخدم `{sub_id}` غير موجود في قائمة المشتركين!",
            parse_mode="Markdown"
        )


async def show_admin_contact_settings(user_id: int, send_or_edit_fn, is_edit: bool = False) -> None:
    if not is_admin(user_id):
        return

    contact = get_admin_contact()
    contact_url = get_admin_contact_url()

    if contact:
        contact_display = f"@{contact.lstrip('@')}" if not contact.startswith("http") else contact
        status_line = f"🟢 **معرّف:** `{contact_display}`"
    else:
        status_line = f"⚠️ *لم يُحدد بعد (يتم التوجيه للآيدي الأساسي: `{PRIMARY_ADMIN_ID}`)*"

    text = (
        "📞 **إعدادات حساب التواصل مع الإدارة (Admin Contact):**\n\n"
        f"• **الحساب الحالي:** {status_line}\n"
        f"• **رابط الزر للمستخدمين:** `{contact_url}`\n\n"
        "💡 **كيف تعمل هذه الميزة؟**\n"
        "عندما يدخل أي مستخدم غير مشترك أو انتهى اشتراكه، يرسل له البوت تنبيهاً ومعه زر تفاعلي:\n"
        "👉 `[ 💬 تواصل مع الإدارة للاشتراك ]`\n"
        "بالضغط عليه يتم تحويل العميل مباشرة إلى حسابك لتفعيل اشتراكه واستلام المدفوعات.\n\n"
        "👇 اختر الإجراء المطلوب أدناه:"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ تحديد / تعديل الحساب", callback_data="act_edit_admin_contact"),
            InlineKeyboardButton("🔗 تجربة الرابط", url=contact_url),
        ],
        [
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])

    if is_edit:
        try:
            await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")


async def admin_contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text("⛔ عذراً، هذا الخيار مخصص للمشرفين فقط.")
        return
    await show_admin_contact_settings(user_id, update.effective_message.reply_text, is_edit=False)


async def admin_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.message.reply_text("⛔ عذراً، هذا الخيار مخصص للمشرفين فقط.")
        return
    await show_admin_contact_settings(user_id, query.message.edit_text, is_edit=True)


async def edit_admin_contact_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer("⛔ هذا الخيار مخصص للمشرفين فقط.", show_alert=True)
        else:
            await update.effective_message.reply_text("⛔ هذا الخيار مخصص للمشرفين فقط.")
        return ConversationHandler.END

    # If admin provided argument directly: /setcontact @username
    if context.args:
        raw = context.args[0].strip()
        clean = raw
        if "t.me/" in clean:
            clean = clean.split("t.me/")[-1].strip("/").strip()
        if clean.startswith("@"):
            clean = clean.lstrip("@").strip()
        if clean:
            set_admin_contact(clean)
            new_url = get_admin_contact_url()
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 تجربة الرابط", url=new_url)],
                [
                    InlineKeyboardButton("📞 إعدادات التواصل", callback_data="act_admin_contact"),
                    InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
                ]
            ])
            await update.effective_message.reply_text(
                f"✅ **تم حفظ وتحديث حساب التواصل للإدارة بنجاح!**\n\n"
                f"👤 **المعرف:** `@{clean}`\n"
                f"🔗 **رابط التواصل المباشر:** `{new_url}`\n\n"
                "✨ سيظهر الآن للمستخدمين غير المشتركين زر مباشر للتواصل معك.",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return ConversationHandler.END

    prompt = (
        "📞 **أرسل الآن يوزرنيم (معرف) أو رابط الحساب الذي تريد أن يتواصل معه المشتركون:**\n\n"
        "📌 **الصيغ المقبولة:**\n"
        "• معرف تيليجرام: `@your_username` أو `your_username`\n"
        "• رابط تيليجرام: `https://t.me/your_username`\n"
        "• أو آيدي حساب تيليجرام رقمي\n\n"
        "💡 للإلغاء والتراجع اضغط الزر أدناه:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء", callback_data="act_admin_contact")]
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    return SET_ADMIN_CONTACT


async def save_admin_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return ConversationHandler.END

    raw = update.message.text.strip()
    clean = raw
    if "t.me/" in clean:
        clean = clean.split("t.me/")[-1].strip("/").strip()
    if clean.startswith("@"):
        clean = clean.lstrip("@").strip()

    if not clean:
        await update.message.reply_text(
            "⚠️ المعرف غير صالح. يرجى إرسال يوزرنيم صحيح مثل `@username`:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="act_admin_contact")]
            ]),
        )
        return SET_ADMIN_CONTACT

    set_admin_contact(clean)
    new_url = get_admin_contact_url()

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 تجربة الرابط الآن", url=new_url)],
        [
            InlineKeyboardButton("📞 إعدادات التواصل", callback_data="act_admin_contact"),
            InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])

    await update.message.reply_text(
        f"✅ **تم حفظ وتحديث حساب التواصل بنجاح!**\n\n"
        f"👤 **المعرف:** `@{clean}`\n"
        f"🔗 **رابط التواصل المباشر:** `{new_url}`\n\n"
        "✨ سيتم الآن عرض زر مدمج `[ 💬 تواصل مع الإدارة للاشتراك ]` يوجه المستخدمين غير المشتركين إلى هذا الحساب مباشرة.",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def show_email_queue_panel(user_id: int, send_or_edit_fn, is_edit: bool = False) -> None:
    queue = get_user_queued_emails(user_id)
    count = len(queue)

    text = (
        "📬 **قائمة الإيميلات المحفوظة للإنشاء التلقائي (Email Queue):**\n\n"
        f"• **عدد الإيميلات المضافة:** `{count} / 100` إيميل كحد أقصى\n\n"
        "💡 **كيف تعمل هذه الميزة؟**\n"
        "1️⃣ أضف حتى 100 إيميل دفعة واحدة (بالنص أو كملف `.txt`).\n"
        "2️⃣ عند الضغط على **'➕ إنشاء حساب جديد'**، يتيح لك البوت خيارين:\n"
        "   - **إنشاء تلقائي من إيميلاتي:** وتحديد عدد الحسابات (مثال: 5 أو 10 أو 66 أو الكل).\n"
        "   - **إدخال إيميل يدوي مختلف:** لتسجيل حساب واحد مخصص.\n"
        "3️⃣ يستمر البوت بالإنشاء المتتابع تلقائياً بدون توقف.\n"
        "4️⃣ **حذف تلقائي:** أي إيميل يتم إنشاؤه بنجاح يُحذف فوراً من قائمتك حتى لا يتكرر أبداً!\n\n"
    )

    if queue:
        preview_items = queue[:10]
        preview_lines = "\n".join([f"• `{e}`" for e in preview_items])
        if len(queue) > 10:
            preview_lines += f"\n*... والمزيد ({len(queue) - 10} إيميل آخر)*"
        text += f"📋 **معاينة الإيميلات المضافة:**\n{preview_lines}\n\n"
    else:
        text += "📭 *قائمتك فارغة حالياً. اضغط على زر '➕ إضافة إيميلات' بالأسفل لبدء إضافتها.*\n\n"

    buttons = [
        [
            InlineKeyboardButton("➕ إضافة إيميلات للقائمة", callback_data="act_prompt_add_emails"),
        ]
    ]
    if queue:
        buttons.append([
            InlineKeyboardButton(f"🚀 بدء إنشاء تلقائي ({count})", callback_data="act_start_queue_create"),
            InlineKeyboardButton("🗑️ تفريغ القائمة", callback_data="act_confirm_clear_queue"),
        ])
    buttons.append([
        InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="act_menu"),
    ])

    markup = InlineKeyboardMarkup(buttons)
    if is_edit:
        try:
            await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    await send_or_edit_fn(text, reply_markup=markup, parse_mode="Markdown")


async def email_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_email_queue_panel(update.effective_user.id, update.effective_message.reply_text, is_edit=False)


async def email_queue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await show_email_queue_panel(update.effective_user.id, query.message.edit_text, is_edit=True)


async def confirm_clear_queue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ نعم، افرغ القائمة", callback_data="act_do_clear_queue"),
            InlineKeyboardButton("❌ تراجع", callback_data="act_email_queue"),
        ]
    ])
    await query.message.edit_text(
        "⚠️ **هل أنت متأكد من رغبتك في تفريغ ومسح جميع الإيميلات من قائمتك؟**\n\n"
        "لا يمكن التراجع عن هذه الخطوة بعد التأكيد.",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def do_clear_queue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    clear_user_queued_emails(user_id)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع لقائمة الإيميلات", callback_data="act_email_queue")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu")],
    ])
    await query.message.edit_text(
        "✅ **تم تفريغ قائمة الإيميلات بنجاح.**",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def prompt_add_emails_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    current_count = len(get_user_queued_emails(user_id))

    prompt = (
        "📥 **أرسل الآن الإيميلات التي تريد إضافتها لقائمتك التلقائية:**\n\n"
        f"• **سعة قائمتك الحالية:** `{current_count} / 100` إيميل\n\n"
        "📌 **طرق الإرسال المدعومة:**\n"
        "1️⃣ أرسل الإيميلات في رسالة نصية (كل إيميل في سطر، أو مفصولة بمسافات أو فواصل).\n"
        "2️⃣ أو أرسل ملف نصي `.txt` يحتوي على قائمة الإيميلات مباشرة!\n\n"
        "💡 سيتم استخراج الإيميلات الصحيحة تلقائياً وفلترة المكرر منها.\n"
        "للتراجع اضغط الزر أدناه:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء", callback_data="act_email_queue")]
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    return INPUT_QUEUED_EMAILS


async def save_queued_emails_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    raw_text = ""

    if update.message.document:
        doc = update.message.document
        doc_name = (doc.file_name or "").lower()
        if doc_name.endswith((".txt", ".csv", ".log")):
            file = await context.bot.get_file(doc.file_id)
            content = await file.download_as_bytearray()
            raw_text = content.decode("utf-8", errors="ignore")
        else:
            await update.message.reply_text(
                "⚠️ يرجى إرسال ملف نصي بصيغة `.txt` أو إرسال الإيميلات كنص عادي:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ إلغاء", callback_data="act_email_queue")]
                ]),
            )
            return INPUT_QUEUED_EMAILS
    elif update.message.text:
        raw_text = update.message.text

    found = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", raw_text)
    if not found:
        await update.message.reply_text(
            "⚠️ لم يتم العثور على أي إيميلات صالحة في رسالتك! يرجى إرسال إيميلات صحيحة مثل:\n"
            "`name@example.com`\n\n"
            "أعد المحاولة الآن:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="act_email_queue")]
            ]),
        )
        return INPUT_QUEUED_EMAILS

    added, duplicates, total = add_user_queued_emails(user_id, found)

    msg = (
        "✅ **تم تحديث قائمة إيميلاتك بنجاح!**\n\n"
        f"• **الإيميلات المضافة حديثاً:** `{added}` إيميل\n"
        f"• **المكررة أو المتجاوزة للحد:** `{duplicates}`\n"
        f"• **إجمالي الإيميلات في قائمتك:** `{total} / 100` إيميل\n\n"
    )
    if total >= 100:
        msg += "⚠️ تم الوصول للحد الأقصى المسموح (100 إيميل).\n\n"

    msg += "👇 اختر الإجراء التالي الذي تريده:"

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🚀 بدء إنشاء تلقائي ({total})", callback_data="act_start_queue_create"),
            InlineKeyboardButton("📬 عرض القائمة", callback_data="act_email_queue"),
        ],
        [
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu"),
        ]
    ])
    await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")
    return ConversationHandler.END


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Error: TELEGRAM_BOT_TOKEN is missing. Set it in .env file.")

    app = Application.builder().token(token).build()

    # Security: Global Access Guard (Runs before all other handlers at group -1)
    app.add_handler(TypeHandler(Update, access_guard), group=-1)

    flow = ConversationHandler(
        entry_points=[
            CommandHandler("new", start_registration_entry),
            CommandHandler("register", start_registration_entry),
            CommandHandler("setpassword", edit_perm_pass_entry),
            CommandHandler("setname", edit_perm_name_entry),
            CommandHandler("setcontact", edit_admin_contact_entry),
            CommandHandler("addemails", prompt_add_emails_entry),
            MessageHandler(filters.Regex(r"^(\+|➕)?\s*إنشاء حساب"), start_registration_entry),
            CallbackQueryHandler(start_registration_entry, pattern="^act_create$"),
            CallbackQueryHandler(choose_queue_prompt, pattern="^act_start_queue_create$"),
            CallbackQueryHandler(handle_batch_count_choice, pattern="^act_choose_manual$"),
            CallbackQueryHandler(handle_batch_count_choice, pattern="^act_choose_queue$"),
            CallbackQueryHandler(prompt_add_emails_entry, pattern="^act_prompt_add_emails$"),
            CallbackQueryHandler(edit_perm_pass_entry, pattern="^act_edit_perm_pass$"),
            CallbackQueryHandler(edit_perm_name_entry, pattern="^act_edit_perm_name$"),
            CallbackQueryHandler(edit_admin_contact_entry, pattern="^act_edit_admin_contact$"),
        ],
        states={
            CHOOSE_BATCH_COUNT: [
                CallbackQueryHandler(choose_manual_callback, pattern="^act_choose_manual$"),
                CallbackQueryHandler(choose_queue_prompt, pattern="^act_choose_queue$"),
                CallbackQueryHandler(handle_batch_count_choice, pattern="^act_batch_"),
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_batch_count_choice),
            ],
            BATCH_PASSWORD: [
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, batch_password_step),
            ],
            INPUT_QUEUED_EMAILS: [
                CallbackQueryHandler(email_queue_callback, pattern="^act_email_queue$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, save_queued_emails_step),
            ],
            EMAIL: [
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, email_step),
            ],
            PASSWORD: [
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, password_step),
            ],
            BASE_NAME: [
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                CallbackQueryHandler(base_name_step, pattern="^act_default_name$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, base_name_step),
            ],
            CODE: [
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, code_step),
            ],
            SET_PERM_PASS: [
                CallbackQueryHandler(perm_pass_callback, pattern="^act_perm_pass$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_perm_pass_step),
            ],
            SET_PERM_NAME: [
                CallbackQueryHandler(perm_name_callback, pattern="^act_perm_name$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_perm_name_step),
            ],
            SET_ADMIN_CONTACT: [
                CallbackQueryHandler(admin_contact_callback, pattern="^act_admin_contact$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_admin_contact_step),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start_command),
            MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel),
            CallbackQueryHandler(cancel, pattern="^act_cancel$"),
            CallbackQueryHandler(email_queue_callback, pattern="^act_email_queue$"),
            CallbackQueryHandler(perm_pass_callback, pattern="^act_perm_pass$"),
            CallbackQueryHandler(perm_name_callback, pattern="^act_perm_name$"),
            CallbackQueryHandler(admin_contact_callback, pattern="^act_admin_contact$"),
        ],
        allow_reentry=True,
    )

    app.add_handler(flow)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("accounts", my_accounts_command))
    app.add_handler(CommandHandler("emails", email_queue_command))
    app.add_handler(CommandHandler("queue", email_queue_command))
    app.add_handler(CommandHandler("addemails", prompt_add_emails_entry))
    app.add_handler(CommandHandler("password", perm_pass_command))
    app.add_handler(CommandHandler("name", perm_name_command))
    app.add_handler(CommandHandler("profilename", perm_name_command))
    app.add_handler(CommandHandler("subs", subs_command))
    app.add_handler(CommandHandler("subscribers", subs_command))
    app.add_handler(CommandHandler("addsub", add_sub_command))
    app.add_handler(CommandHandler("delsub", del_sub_command))
    app.add_handler(CommandHandler("contact", admin_contact_command))
    app.add_handler(CommandHandler("admincontact", admin_contact_command))
    app.add_handler(CommandHandler("setcontact", edit_admin_contact_entry))
    app.add_handler(CommandHandler("myplan", my_sub_handler))
    app.add_handler(CommandHandler("mysub", my_sub_handler))
    app.add_handler(CommandHandler("plan", my_sub_handler))
    app.add_handler(CommandHandler("id", my_id_command))
    app.add_handler(CommandHandler("myid", my_id_command))
    app.add_handler(CommandHandler("settings", perm_pass_command))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📋\s*حساباتي"), my_accounts_command))
    app.add_handler(MessageHandler(filters.Regex(r"^📬?\s*(قائمة )?الإيميلات"), email_queue_command))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐\s*كلمة المرور الدائمة"), perm_pass_command))
    app.add_handler(MessageHandler(filters.Regex(r"^👤\s*اسم الملفات المحفوظ"), perm_name_command))
    app.add_handler(MessageHandler(filters.Regex(r"^👥\s*(المشتركين|إدارة المشتركين)"), subs_command))
    app.add_handler(MessageHandler(filters.Regex(r"^(📞|👤)?\s*حساب (التواصل|الدعم|الإدارة)"), admin_contact_command))
    app.add_handler(MessageHandler(filters.Regex(r"^⏳\s*اشتراكي"), my_sub_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^ℹ️\s*تعليمات"), help_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^(❌|إلغاء)"), cancel))
    app.add_handler(CallbackQueryHandler(my_accounts_callback, pattern="^act_my_accounts$"))
    app.add_handler(CallbackQueryHandler(email_queue_callback, pattern="^act_email_queue$"))
    app.add_handler(CallbackQueryHandler(confirm_clear_queue_callback, pattern="^act_confirm_clear_queue$"))
    app.add_handler(CallbackQueryHandler(do_clear_queue_callback, pattern="^act_do_clear_queue$"))
    app.add_handler(CallbackQueryHandler(perm_pass_callback, pattern="^act_perm_pass$"))
    app.add_handler(CallbackQueryHandler(toggle_perm_pass_callback, pattern="^act_toggle_perm_pass$"))
    app.add_handler(CallbackQueryHandler(edit_perm_pass_entry, pattern="^act_edit_perm_pass$"))
    app.add_handler(CallbackQueryHandler(perm_name_callback, pattern="^act_perm_name$"))
    app.add_handler(CallbackQueryHandler(toggle_perm_name_callback, pattern="^act_toggle_perm_name$"))
    app.add_handler(CallbackQueryHandler(edit_perm_name_entry, pattern="^act_edit_perm_name$"))
    app.add_handler(CallbackQueryHandler(subs_callback, pattern="^act_subs$"))
    app.add_handler(CallbackQueryHandler(admin_contact_callback, pattern="^act_admin_contact$"))
    app.add_handler(CallbackQueryHandler(edit_admin_contact_entry, pattern="^act_edit_admin_contact$"))
    app.add_handler(CallbackQueryHandler(my_sub_handler, pattern="^act_my_sub$"))
    app.add_handler(CallbackQueryHandler(prompt_addsub_callback, pattern="^act_prompt_addsub$"))
    app.add_handler(CallbackQueryHandler(help_handler, pattern="^act_help$"))
    app.add_handler(CallbackQueryHandler(export_accounts_handler, pattern="^act_export$"))
    app.add_handler(CallbackQueryHandler(confirm_clear_callback, pattern="^act_confirm_clear$"))
    app.add_handler(CallbackQueryHandler(do_clear_callback, pattern="^act_do_clear$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^act_menu$"))
    app.add_handler(CallbackQueryHandler(cancel, pattern="^act_cancel$"))

    print("[+] Shahid VIP Account Creator Bot is now running...")
    app.run_polling()


if __name__ == "__main__":
    main()
