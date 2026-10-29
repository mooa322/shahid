"""Telegram bot for automated Shahid VIP account registration."""

import asyncio
import threading
import io
import json
import logging
import os
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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
    INPUT_PROXIES,
    CONFIRM_PROXY_MODE,
) = range(12)
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
PROXIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxies.json")

# Thread-safe lock for multi-user concurrent database writes
_DB_LOCK = threading.Lock()
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
    with _DB_LOCK:
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
    with _DB_LOCK:
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
    with _DB_LOCK:
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


def load_proxies() -> dict:
    if os.path.exists(PROXIES_FILE):
        try:
            with open(PROXIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error loading proxies.json: {e}", flush=True)
            return {}
    return {}


def save_proxies(data: dict) -> None:
    with _DB_LOCK:
        try:
            with open(PROXIES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] Error saving proxies.json: {e}", flush=True)


def parse_proxy_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    proto = "http"
    if "://" in line:
        parts_scheme = line.split("://", 1)
        proto = parts_scheme[0].lower()
        line = parts_scheme[1]

    if "@" in line:
        auth_part, host_part = line.split("@", 1)
        if ":" in auth_part:
            username, password = auth_part.split(":", 1)
        else:
            username, password = auth_part, ""
        if ":" in host_part:
            host, port = host_part.split(":", 1)
        else:
            host, port = host_part, "80"
    else:
        parts = line.split(":")
        if len(parts) >= 4:
            host, port, username, password = parts[0], parts[1], parts[2], ":".join(parts[3:])
        elif len(parts) == 2:
            host, port = parts[0], parts[1]
            username, password = "", ""
        else:
            return None

    host = host.strip()
    port = port.strip()
    username = username.strip()
    password = password.strip()

    if not host or not port.isdigit():
        return None

    return {
        "server": f"{proto}://{host}:{port}",
        "username": username,
        "password": password,
        "host": host,
        "port": port,
        "raw": f"{host}:{port}:{username}:{password}" if username else f"{host}:{port}",
        "is_egypt": True,
    }


def parse_proxies_text(raw_text: str) -> List[dict]:
    parsed = []
    seen = set()
    for line in raw_text.splitlines():
        p = parse_proxy_line(line)
        if not p:
            continue
        key = f"{p['host']}:{p['port']}"
        if key not in seen:
            seen.add(key)
            parsed.append(p)
    return parsed


def get_user_proxy_info(user_id: int) -> dict:
    data = load_proxies()
    uid = str(user_id)
    return data.get(uid, {"proxies": [], "current_index": 0, "rotation_enabled": True, "proxy_mode": "auto"})


def get_global_proxy_info() -> dict:
    data = load_proxies()
    return data.get("global", {"proxies": [], "current_index": 0, "rotation_enabled": True})


def get_user_proxies(user_id: int) -> List[dict]:
    return get_user_proxy_info(user_id).get("proxies", [])


def get_global_proxies() -> List[dict]:
    return get_global_proxy_info().get("proxies", [])


def get_user_proxy_mode(user_id: int) -> str:
    """Returns 'auto' (use private then global), 'private' (private only), or 'global' (global shared only)."""
    return get_user_proxy_info(user_id).get("proxy_mode", "auto")


def cycle_user_proxy_mode(user_id: int) -> str:
    """Cycles mode: auto -> private -> global -> auto."""
    data = load_proxies()
    uid = str(user_id)
    entry = data.setdefault(uid, {
        "proxies": [],
        "current_index": 0,
        "rotation_enabled": True,
        "proxy_mode": "auto",
    })
    current = entry.get("proxy_mode", "auto")
    next_modes = {
        "auto": "private",
        "private": "global",
        "global": "auto",
    }
    new_mode = next_modes.get(current, "auto")
    entry["proxy_mode"] = new_mode
    save_proxies(data)
    return new_mode


def add_proxies_to_target(target_key: str, new_proxies: List[dict], replace: bool = False) -> tuple[int, int, int]:
    """Adds proxies to either a user ID string or 'global'."""
    data = load_proxies()
    entry = data.setdefault(target_key, {
        "proxies": [],
        "current_index": 0,
        "rotation_enabled": True,
    })
    if target_key != "global" and "proxy_mode" not in entry:
        entry["proxy_mode"] = "auto"

    if replace:
        entry["proxies"] = new_proxies
        entry["current_index"] = 0
        save_proxies(data)
        return len(new_proxies), 0, len(new_proxies)

    current_proxies = entry.get("proxies", [])
    seen = {f"{p.get('host')}:{p.get('port')}" for p in current_proxies}
    added = 0
    duplicates = 0

    for p in new_proxies:
        key = f"{p.get('host')}:{p.get('port')}"
        if key in seen:
            duplicates += 1
            continue
        current_proxies.append(p)
        seen.add(key)
        added += 1

    entry["proxies"] = current_proxies
    save_proxies(data)
    return added, duplicates, len(current_proxies)


def add_user_proxies(user_id: int, new_proxies: List[dict], replace: bool = False) -> tuple[int, int, int]:
    return add_proxies_to_target(str(user_id), new_proxies, replace=replace)


def add_global_proxies(new_proxies: List[dict], replace: bool = False) -> tuple[int, int, int]:
    return add_proxies_to_target("global", new_proxies, replace=replace)


def clear_user_proxies(user_id: int) -> bool:
    data = load_proxies()
    uid = str(user_id)
    if uid in data:
        data[uid]["proxies"] = []
        data[uid]["current_index"] = 0
        save_proxies(data)
        return True
    return False


def clear_global_proxies() -> bool:
    data = load_proxies()
    if "global" in data:
        data["global"]["proxies"] = []
        data["global"]["current_index"] = 0
        save_proxies(data)
        return True
    return False


def toggle_user_proxy_rotation(user_id: int) -> bool:
    data = load_proxies()
    uid = str(user_id)
    entry = data.setdefault(uid, {
        "proxies": [],
        "current_index": 0,
        "rotation_enabled": True,
        "proxy_mode": "auto",
    })
    new_state = not entry.get("rotation_enabled", True)
    entry["rotation_enabled"] = new_state
    save_proxies(data)
    return new_state


def toggle_global_proxy_rotation() -> bool:
    data = load_proxies()
    entry = data.setdefault("global", {
        "proxies": [],
        "current_index": 0,
        "rotation_enabled": True,
    })
    new_state = not entry.get("rotation_enabled", True)
    entry["rotation_enabled"] = new_state
    save_proxies(data)
    return new_state


def get_next_proxy_for_user(user_id: int) -> Optional[dict]:
    data = load_proxies()
    uid = str(user_id)
    user_entry = data.get(uid, {})
    global_entry = data.get("global", {})
    mode = user_entry.get("proxy_mode", "auto")

    user_proxies = user_entry.get("proxies", [])
    global_proxies = global_entry.get("proxies", [])

    target_entry = None
    target_type = "private"

    if mode == "private":
        if user_proxies:
            target_entry = user_entry
            target_type = "private"
    elif mode == "global":
        if global_proxies:
            target_entry = global_entry
            target_type = "global"
    else:  # "auto"
        if user_proxies:
            target_entry = user_entry
            target_type = "private"
        elif global_proxies:
            target_entry = global_entry
            target_type = "global"

    if target_entry and target_entry.get("proxies"):
        proxies_list = target_entry["proxies"]
        total = len(proxies_list)
        cur_idx = target_entry.get("current_index", 0)
        rotation_enabled = target_entry.get("rotation_enabled", True)

        selected_proxy = proxies_list[cur_idx % total]

        if rotation_enabled and total > 1:
            target_entry["current_index"] = (cur_idx + 1) % total
            save_proxies(data)

        cfg = {
            "server": selected_proxy["server"],
            "host": selected_proxy.get("host", ""),
            "port": selected_proxy.get("port", ""),
            "raw": selected_proxy.get("raw", ""),
            "proxy_index": (cur_idx % total) + 1,
            "total_proxies": total,
            "proxy_type": target_type,
            "is_egypt": selected_proxy.get("is_egypt", True),
        }
        if selected_proxy.get("username"):
            cfg["username"] = selected_proxy["username"]
            cfg["password"] = selected_proxy.get("password", "")
        return cfg

    # Fallback to PROXY_SERVER from .env if present
    env_server = os.getenv("PROXY_SERVER")
    if env_server:
        return {
            "server": env_server,
            "host": env_server.replace("http://", "").replace("https://", ""),
            "port": "",
            "raw": env_server,
            "proxy_index": 1,
            "total_proxies": 1,
            "proxy_type": "env",
            "is_egypt": True,
        }

    return None

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
        "create_profiles_enabled": True,
    })


def toggle_user_create_profiles(user_id: int) -> bool:
    data = load_settings()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "perm_pass_enabled": False,
            "perm_password": "",
            "perm_name_enabled": False,
            "perm_name": "",
            "create_profiles_enabled": True,
        }
    cur = data[uid].get("create_profiles_enabled", True)
    data[uid]["create_profiles_enabled"] = not cur
    save_settings(data)
    return data[uid]["create_profiles_enabled"]


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
                d = json.load(f)
                if isinstance(d, dict):
                    return d
                return {}
        except Exception:
            return {}
    return {}


def save_account(user_id: int, email: str, password: str, profiles: list, proxy: str = "") -> None:
    with _DB_LOCK:
        data = load_accounts()
    if not isinstance(data, dict):
        data = {}
    uid = str(user_id)
    if uid not in data or not isinstance(data[uid], list):
        data[uid] = []
    account_entry = {
        "email": email,
        "password": password,
        "profiles": profiles,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if proxy:
        account_entry["proxy"] = proxy
    data[uid].append(account_entry)
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

    proxies_len = len(get_user_proxies(user_id)) if user_id else 0
    proxies_label = f"🌐 إدارة البروكسيات ({proxies_len})" if proxies_len else "🌐 إدارة البروكسيات (تلقائي)"

    create_profiles = get_user_settings(user_id).get("create_profiles_enabled", True) if user_id else True
    profiles_toggle_label = "👥 تجهيز الملفات: 🟢 مفعّل" if create_profiles else "👥 تجهيز الملفات: 🔴 معطّل"

    buttons = [
        [
            InlineKeyboardButton("➕ إنشاء حساب جديد", callback_data="act_create"),
            InlineKeyboardButton("📋 حساباتي", callback_data="act_my_accounts"),
        ],
        [
            InlineKeyboardButton(queue_label, callback_data="act_email_queue"),
            InlineKeyboardButton(proxies_label, callback_data="act_proxies"),
        ],
        [
            InlineKeyboardButton("🔐 كلمة المرور الدائمة", callback_data="act_perm_pass"),
            InlineKeyboardButton("👤 اسم الملفات المحفوظ", callback_data="act_perm_name"),
        ],
        [
            InlineKeyboardButton(profiles_toggle_label, callback_data="act_toggle_create_profiles"),
        ],
    ]
    if user_id and is_admin(user_id):
        buttons.append([
            InlineKeyboardButton("👥 المشتركين", callback_data="act_subs"),
            InlineKeyboardButton("📞 حساب التواصل", callback_data="act_admin_contact"),
        ])
        buttons.append([
            InlineKeyboardButton("💾 نسخة احتياطية شاملة", callback_data="act_admin_backup"),
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


def get_otp_keyboard(show_resend: bool = False) -> InlineKeyboardMarkup:
    """Returns the inline keyboard for the OTP step.
    Resend button is preserved in code but hidden from the user by default (show_resend=False).
    """
    buttons = []
    if show_resend:
        buttons.append([InlineKeyboardButton("🔄 إعادة إرسال الكود", callback_data="act_resend_otp")])
    buttons.append([InlineKeyboardButton("❌ إلغاء العملية", callback_data="act_cancel")])
    return InlineKeyboardMarkup(buttons)


def get_name_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ استخدام dahoom الافتراضي", callback_data="act_default_name")],
        [InlineKeyboardButton("✍️ تسمية الـ 4 ملفات بنفسي (4 أسماء)", callback_data="act_custom_4_names")],
        [InlineKeyboardButton("🚫 تخطي تسمية الملفات (حساب فقط)", callback_data="act_skip_profiles")],
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
    user_id: int = 0
    email: str = ""
    password: str = ""
    base_name: str = "dahoom"
    custom_names: Optional[List[str]] = None
    skip_profiles: bool = False
    proxy_config: Optional[dict] = None
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
            if hasattr(session, "page") and session.page and not session.page.is_closed():
                await session.page.close()
        except Exception:
            pass
        try:
            if hasattr(session, "context") and session.context:
                await session.context.close()
        except Exception:
            pass
        try:
            if hasattr(session, "browser") and session.browser:
                await session.browser.close()
        except Exception:
            pass
        try:
            if hasattr(session, "playwright") and session.playwright:
                await session.playwright.stop()
        except Exception:
            pass
    context.user_data.pop("session", None)


async def remove_cookie_banner(page: Page) -> bool:
    """Accepts cookie / privacy consent banners ('موافق') and clears overlays immediately."""
    clicked = False
    accept_selectors = [
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        "button:text-is('موافق')",
        "button:has-text('موافق')",
        "#accept-recommended-btn-handler",
        "#onetrust-reject-all-handler",
        "button:has-text('الضرورية فقط')",
        "button:has-text('قبول الكل')",
        "button:has-text('قبول')",
    ]
    for sel in accept_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=400):
                await loc.click(force=True, timeout=1200)
                clicked = True
                await asyncio.sleep(0.3)
                break
        except Exception:
            pass

    try:
        js_done = await page.evaluate("""() => {
            let res = false;
            if (window.OneTrust && typeof window.OneTrust.AcceptAll === 'function') {
                try { window.OneTrust.AcceptAll(); res = true; } catch(e) {}
            }
            const b = document.getElementById('onetrust-accept-btn-handler') ||
                      document.getElementById('accept-recommended-btn-handler') ||
                      Array.from(document.querySelectorAll('button, div[role="button"], a')).find(el => {
                          const t = (el.innerText || '').trim();
                          return t === 'موافق' || t === 'قبول الكل' || t === 'الضرورية فقط' || t === 'قبول';
                      });
            if (b) {
                try { b.click(); res = true; } catch(e) {}
            }

            const sels = ['#onetrust-banner-sdk', '#onetrust-consent-sdk', '#onetrust-pc-sdk', '.onetrust-pc-dark-filter', '.ot-fade-in'];
            sels.forEach(s => {
                document.querySelectorAll(s).forEach(e => {
                    e.style.setProperty('display', 'none', 'important');
                    e.remove();
                });
            });
            if (document.body) {
                document.body.style.overflow = 'auto';
                document.body.classList.remove('ot-modal-open');
            }
            if (document.documentElement) {
                document.documentElement.style.overflow = 'auto';
            }
            return res;
        }""")
        if js_done:
            clicked = True
    except Exception:
        pass
    return clicked


async def is_on_interests_screen(page) -> bool:
    """Detects if page is currently on the onboarding interests/genres selection screen."""
    try:
        cur_url = (page.url or "").lower()
        if "/onboarding" in cur_url or "/interests" in cur_url:
            return True
        body_t = await page.locator("body").inner_text()
        has_skip_text = any(x in body_t for x in ["تخطى وابدأ المشاهدة", "تخطي وابدأ المشاهدة", "ابدأ المشاهدة", "تخطي الخطوة", "تخطى", "تخطي"])
        has_interests_text = any(x in body_t for x in ["عروض موسيقية", "مسلسلات أكشن", "أفلام أجنبية", "كوميديا", "جرائم", "مسلسلات خليجية", "برامج تعليمية", "أنمي"])
        if has_interests_text:
            return True
        if has_skip_text and ("متابعة" in body_t or "اختر" in body_t):
            return True
    except Exception:
        pass
    return False


async def handle_skip_onboarding(page) -> bool:
    """Actively detects and dismisses 'تخطى وابدأ المشاهدة' / interests screen."""
    await remove_cookie_banner(page)

    for attempt in range(8):
        await remove_cookie_banner(page)

        # 1. If already on select profile screen, skip is done!
        if await is_on_select_profile_screen(page):
            print("[+] Already reached profile selection screen, skip not needed.", flush=True)
            return True

        if not await is_on_interests_screen(page) and attempt > 0:
            print("[+] Interests screen dismissed successfully!", flush=True)
            return True

        print(f"[*] Attempt {attempt+1}/8 to click 'تخطى وابدأ المشاهدة'...", flush=True)

        # Scroll to bottom so skip button is in viewport
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

        # Strategy A: Direct JS leaf click on exact text containing 'تخطى' / 'تخطي'
        clicked = False
        try:
            clicked = await page.evaluate("""() => {
                const leaves = Array.from(document.querySelectorAll('*')).filter(el => {
                    if (el.children.length > 0) return false;
                    const t = (el.textContent || '').trim();
                    return t.includes('تخطى') || t.includes('تخطي') || t.includes('ابدأ المشاهدة');
                });
                for (const leaf of leaves) {
                    const btn = leaf.closest('button, a, [role="button"], div[class*="cursor-pointer"]') || leaf;
                    try {
                        btn.scrollIntoView({ behavior: 'instant', block: 'center' });
                        btn.click();
                        btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        return true;
                    } catch(e) {}
                }
                return false;
            }""")
            if clicked:
                print("[+] Clicked skip button via DOM leaf dispatch", flush=True)
        except Exception:
            pass

        # Strategy B: Playwright locator click
        if not clicked:
            skip_selectors = [
                "text='تخطى وابدأ المشاهدة'",
                "text='تخطي وابدأ المشاهدة'",
                "text='ابدأ المشاهدة الآن'",
                "button:has-text('تخطى')",
                "button:has-text('تخطي')",
                "a:has-text('تخطى')",
                "a:has-text('تخطي')",
                "p:has-text('تخطى')",
                "span:has-text('تخطى')",
                "text='تخطى'",
                "text='تخطي'",
            ]
            for s in skip_selectors:
                try:
                    loc = page.locator(s).last
                    if await loc.is_visible(timeout=400):
                        await loc.scroll_into_view_if_needed()
                        await asyncio.sleep(0.15)
                        await loc.click(timeout=1500)
                        print(f"[+] Clicked skip button using locator: {s}", flush=True)
                        clicked = True
                        break
                except Exception:
                    pass

        # Strategy C: Fallback - Select at least 4 categories so 'متابعة' is guaranteed enabled
        if (attempt >= 2 or not clicked) and await is_on_interests_screen(page):
            try:
                print("[*] Interests fallback: selecting 4 categories and clicking 'متابعة'...", flush=True)
                await page.evaluate("""() => {
                    const pills = Array.from(document.querySelectorAll('div, p, span')).filter(el => {
                        const t = (el.innerText || '').trim();
                        return ['مسلسلات خليجية', 'أفلام أجنبية', 'مسلسلات أكشن', 'كوميديا', 'جرائم', 'أنمي'].includes(t);
                    });
                    let count = 0;
                    for (const p of pills) {
                        const target = p.closest('div[class*="cursor-pointer"]') || p.parentElement || p;
                        try { target.click(); count++; } catch(e) {}
                        if (count >= 4) break;
                    }
                }""")
                await asyncio.sleep(0.5)

                # Now click 'متابعة'
                follow_btn = page.locator("button:has-text('متابعة')").first
                for _ in range(4):
                    if await follow_btn.is_visible(timeout=400) and not await follow_btn.is_disabled():
                        await follow_btn.click()
                        print("[+] Clicked enabled 'متابعة' button!", flush=True)
                        clicked = True
                        break
                    await asyncio.sleep(0.4)
            except Exception:
                pass

        # Strategy D: If still stuck on interests screen after attempt 4, navigate directly to profile screen
        if attempt >= 4 and await is_on_interests_screen(page):
            try:
                print("[*] Navigating directly to /ar/hub/profile to bypass stuck interests screen...", flush=True)
                await page.goto("https://shahid.mbc.net/ar/hub/profile", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2.0)
            except Exception:
                pass

        for _ in range(6):
            await asyncio.sleep(0.5)
            if await is_on_select_profile_screen(page):
                print("[+] Arrived on profile selection screen after skip!", flush=True)
                return True
            if not await is_on_interests_screen(page):
                print("[+] Interests screen dismissed successfully!", flush=True)
                return True

        await asyncio.sleep(1.0)

    return False

# Alias so both names work identically
handle_skip = handle_skip_onboarding


async def is_on_select_profile_screen(page: Page) -> bool:
    """Checks if we are currently on the 'اختر من يشاهد الآن' profile selection screen."""
    try:
        cur_url = (page.url or "").lower()
        if "/hub/profile" in cur_url or "/select-profile" in cur_url:
            return True
        plus_sel = "[data-testid='SelectProfilePage_profile-plus-icon'], [data-test='plus'], img[alt='اضف ملف'], div:has-text('إضافة'):has(img)"
        if await page.locator(plus_sel).first.is_visible(timeout=300):
            return True
        body_t = await page.locator("body").inner_text()
        if any(x in body_t for x in ["اختر من يشاهد", "من يشاهد؟", "Select Profile", "إدارة الملفات"]):
            return True
    except Exception:
        pass
    return False


async def open_avatar_dropdown(page: Page) -> bool:
    """Waits patiently for the header avatar icon to appear and clicks it once to open the user menu."""
    await remove_cookie_banner(page)

    # 1. If already on select profile screen, there is NO header avatar here!
    if await is_on_select_profile_screen(page):
        print("[+] Already on profile selection screen, no header avatar exists here. Skipping avatar search!", flush=True)
        return False

    # 2. If "تغير الملف الشخصي" is already visible on screen, dropdown is already open
    try:
        already_open = await page.locator("[data-testid='selectedProfileDetails_change-profile'], text='تغير الملف الشخصي', text='تغيير الملف الشخصي'").first.is_visible(timeout=500)
        if already_open:
            print("[+] Dropdown already open!", flush=True)
            return True
    except Exception:
        pass

    avatar_selectors = [
        "[data-testid='ShahidHeaderContainer_item-avatar-profile']",
        "[data-testid='menuItemavatar']",
        "div[data-testid='menuItemavatar'] div",
        "header img[src*='avatar']",
        "header img[src*='avatar-v3']",
        "img[src*='avatar-v3']",
        "header [class*='cursor-pointer']:has(img):not(#shahid-logo)",
    ]

    print("[*] Waiting for top avatar icon to appear...", flush=True)
    avatar_loc = None
    for sec in range(12):
        await remove_cookie_banner(page)
        # Check if profile selection screen appeared in the meantime
        if await is_on_select_profile_screen(page):
            print("[+] Profile selection screen appeared, skipping avatar search!", flush=True)
            return False

        for sel in avatar_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=400):
                    avatar_loc = loc
                    print(f"[+] Top avatar icon appeared using selector: {sel} (second {sec+1})", flush=True)
                    break
            except Exception:
                pass
        if avatar_loc:
            break
        await asyncio.sleep(1.0)

    if not avatar_loc:
        try:
            js_found = await page.evaluate("""() => {
                const el = document.querySelector('[data-testid="ShahidHeaderContainer_item-avatar-profile"]') ||
                           document.querySelector('[data-testid="menuItemavatar"]') ||
                           document.querySelector('header img[src*="avatar"]');
                return !!el;
            }""")
            if js_found:
                avatar_loc = page.locator("[data-testid='ShahidHeaderContainer_item-avatar-profile'], [data-testid='menuItemavatar']").first
        except Exception:
            pass

    if not avatar_loc:
        print("[!] Top avatar icon did not appear after wait.", flush=True)
        return False

    await asyncio.sleep(1.0)
    print("[*] Clicking top avatar icon...", flush=True)
    clicked = False
    try:
        await avatar_loc.click()
        clicked = True
    except Exception:
        try:
            await avatar_loc.click(force=True)
            clicked = True
        except Exception:
            try:
                clicked = await page.evaluate("""() => {
                    const el = document.querySelector('[data-testid="ShahidHeaderContainer_item-avatar-profile"]') ||
                               document.querySelector('[data-testid="menuItemavatar"]');
                    if (el) { el.click(); return true; }
                    return false;
                }""")
            except Exception as e:
                print(f"[!] open_avatar_dropdown JS error: {e}", flush=True)

    await asyncio.sleep(1.0)
    return clicked


async def click_change_profile_span(page: Page) -> bool:
    """Waits patiently for 'تغير الملف الشخصي' to appear in the dropdown menu and clicks it cleanly."""
    print("[*] Waiting for 'تغير الملف الشخصي' to appear in dropdown...", flush=True)

    selectors = [
        "[data-testid='selectedProfileDetails_change-profile'] span.underline",
        "[data-testid='selectedProfileDetails_change-profile'] span:has-text('تغير')",
        "span.font-shahidMedium.underline",
        "span.underline:has-text('تغير')",
        "span:text-is('تغير الملف الشخصي')",
        "span:text-is('تغيير الملف الشخصي')",
        "span:has-text('تغير الملف الشخصي')",
    ]

    target_loc = None
    for attempt in range(15):
        for s in selectors:
            try:
                loc = page.locator(s).first
                if await loc.is_visible(timeout=500):
                    txt = (await loc.inner_text() or '').strip()
                    if ('تغير' in txt or 'تغيير' in txt) and 'تعديل' not in txt and 'معلومات' not in txt:
                        target_loc = loc
                        print(f"[+] 'تغير الملف الشخصي' appeared using selector: {s} (second {attempt+1})", flush=True)
                        break
            except Exception:
                pass
        if target_loc:
            break

        try:
            found = await page.evaluate("""() => {
                const span = document.querySelector('[data-testid="selectedProfileDetails_change-profile"] span.underline') ||
                             document.querySelector('[data-testid="selectedProfileDetails_change-profile"] span:last-child') ||
                             Array.from(document.querySelectorAll('span')).find(s => {
                                 const t = (s.innerText || '').trim();
                                 return (t === 'تغير الملف الشخصي' || t === 'تغيير الملف الشخصي') && !t.includes('تعديل');
                             });
                return !!span;
            }""")
            if found:
                target_loc = page.locator("[data-testid='selectedProfileDetails_change-profile'] span.underline, span:has-text('تغير الملف الشخصي')").first
                break
        except Exception:
            pass

        await asyncio.sleep(1.0)

    if not target_loc:
        print("[!] 'تغير الملف الشخصي' did not appear within 15 seconds.", flush=True)
        return False

    await asyncio.sleep(1.0)

    print("[*] Clicking 'تغير الملف الشخصي'...", flush=True)
    clicked = False
    try:
        await target_loc.click()
        clicked = True
    except Exception:
        try:
            await target_loc.click(force=True)
            clicked = True
        except Exception:
            pass

    try:
        js_clicked = await page.evaluate("""() => {
            const span = document.querySelector('[data-testid="selectedProfileDetails_change-profile"] span.underline') ||
                         document.querySelector('[data-testid="selectedProfileDetails_change-profile"] span:last-child') ||
                         Array.from(document.querySelectorAll('span')).find(s => {
                             const t = (s.innerText || '').trim();
                             return (t === 'تغير الملف الشخصي' || t === 'تغيير الملف الشخصي') && !t.includes('تعديل');
                         });
            if (span) {
                span.click();
                span.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                const parent = span.closest('[data-testid="selectedProfileDetails_change-profile"]');
                if (parent) {
                    parent.click();
                    parent.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                }
                return true;
            }
            return false;
        }""")
        if js_clicked:
            clicked = True
    except Exception as e:
        print(f"[!] click_change_profile_span JS error: {e}", flush=True)

    await asyncio.sleep(2.0)
    return clicked


async def navigate_to_select_profile_screen(page: Page) -> bool:
    """Navigates to the profile selection screen."""
    await remove_cookie_banner(page)

    # 1. Check if already on profile selection screen
    if await is_on_select_profile_screen(page):
        print("[+] Already on profile selection screen!", flush=True)
        return True

    # If on interests screen, dismiss it first
    if await is_on_interests_screen(page):
        print("[*] Dismissing interests screen...", flush=True)
        await handle_skip_onboarding(page)
        await asyncio.sleep(1.5)

    if await is_on_select_profile_screen(page):
        print("[+] Arrived on profile selection screen after dismiss!", flush=True)
        return True

    for _ in range(5):
        if await is_on_select_profile_screen(page):
            print("[+] Profile selection screen ready!", flush=True)
            return True
        await asyncio.sleep(0.8)

    # 2. UI flow retry loop (Avatar -> تغير الملف الشخصي -> Wait for plus icon)
    for attempt in range(3):
        print(f"[*] Navigating to profiles screen via avatar menu (attempt {attempt+1}/3)...", flush=True)
        await remove_cookie_banner(page)

        if await is_on_select_profile_screen(page):
            print("[+] Arrived on profile screen!", flush=True)
            return True

        avatar_opened = await open_avatar_dropdown(page)
        if await is_on_select_profile_screen(page):
            return True

        if avatar_opened:
            clicked = await click_change_profile_span(page)
            if clicked:
                for _ in range(10):
                    if await is_on_select_profile_screen(page):
                        print("[+] Arrived on profile screen via menu!", flush=True)
                        return True
                    await asyncio.sleep(0.8)

        # Fallback if menu didn't navigate: navigate directly to /ar/hub/profile
        if attempt == 2 and not await is_on_select_profile_screen(page):
            try:
                print("[*] Directly opening https://shahid.mbc.net/ar/hub/profile...", flush=True)
                await page.goto("https://shahid.mbc.net/ar/hub/profile", wait_until="domcontentloaded")
                await asyncio.sleep(2.0)
                if await is_on_select_profile_screen(page):
                    return True
            except Exception:
                pass

        await asyncio.sleep(1.5)

    return await is_on_select_profile_screen(page)


async def click_add_profile(page: Page) -> bool:
    """Clicks '+' / 'إضافة' to open the new profile modal with active polling and verification."""
    print("[*] Clicking '+' / 'إضافة' to open modal...", flush=True)
    await remove_cookie_banner(page)

    # 1. Check if modal is ALREADY open (look for the real profile modal input, NOT search bar!)
    modal_input_sel = "input[placeholder*='ادخل الاسم'], input[placeholder*='الاسم']:not([placeholder*='ابحث']), div[role='dialog'] input"
    try:
        if await page.locator(modal_input_sel).first.is_visible(timeout=500):
            print("[+] Add profile modal is already open!", flush=True)
            return True
    except Exception:
        pass

    plus_targets = [
        "[data-testid='SelectProfilePage_profile-plus-icon']",
        "[data-testid='SelectProfilePage_profile-plus-icon'] .rounded-full",
        "[data-testid='SelectProfilePage_profile-plus-icon'] img",
        "[data-test='plus']",
        "img[alt='اضف ملف']",
        "p:text-is('إضافة')",
    ]

    for attempt in range(8):
        print(f"[*] Attempt {attempt+1}/8 to click '+' / 'إضافة'...", flush=True)
        await remove_cookie_banner(page)

        # Check if modal opened in the meantime
        try:
            if await page.locator(modal_input_sel).first.is_visible(timeout=400):
                print("[+] Add profile modal is open!", flush=True)
                return True
        except Exception:
            pass

        # Strategy A: Playwright click
        clicked = False
        for sel in plus_targets:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500):
                    await loc.scroll_into_view_if_needed()
                    await asyncio.sleep(0.1)
                    await loc.click(timeout=1500)
                    print(f"[+] Clicked '+' using locator: {sel}", flush=True)
                    clicked = True
                    break
            except Exception:
                pass

        # Strategy B: Direct DOM click and event dispatch on the root plus container
        if not clicked:
            try:
                clicked = await page.evaluate("""() => {
                    const plus = document.querySelector('[data-testid="SelectProfilePage_profile-plus-icon"]') ||
                                 document.querySelector('[data-test="plus"]') ||
                                 document.querySelector('img[alt="اضف ملف"]')?.closest('[data-testid], .cursor-pointer') ||
                                 document.querySelector('img[alt="اضف ملف"]')?.parentElement;
                    if (plus) {
                        plus.scrollIntoView({ behavior: 'instant', block: 'center' });
                        plus.click();
                        plus.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        plus.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        plus.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        const circle = plus.querySelector('.rounded-full') || plus.querySelector('img');
                        if (circle) circle.click();
                        return true;
                    }
                    return false;
                }""")
                if clicked:
                    print("[+] Dispatched click on plus container via JS", flush=True)
            except Exception:
                pass

        # Strategy C: Click via bounding box
        if not clicked:
            try:
                loc = page.locator("[data-testid='SelectProfilePage_profile-plus-icon'], [data-test='plus']").first
                if await loc.is_visible(timeout=500):
                    box = await loc.bounding_box()
                    if box:
                        cx = box['x'] + box['width'] / 2
                        cy = box['y'] + (box['height'] * 0.4)
                        await page.mouse.click(cx, cy)
                        print(f"[+] Clicked '+' via mouse coordinates: ({cx}, {cy})", flush=True)
                        clicked = True
            except Exception:
                pass

        # Wait up to 3s to verify modal opened
        for _ in range(6):
            await asyncio.sleep(0.5)
            try:
                if await page.locator(modal_input_sel).first.is_visible(timeout=400):
                    print("[+] Add profile modal successfully opened (input field visible)!", flush=True)
                    return True
                body_t = await page.locator("body").inner_text()
                if any(k in body_t for k in ["اضافة ملف", "ادخل الاسم", "حساب أطفال"]):
                    print("[+] Add profile modal detected via text!", flush=True)
                    return True
            except Exception:
                pass

        await asyncio.sleep(0.8)

    print("[!] Failed to open add profile modal after 8 attempts.", flush=True)
    return False


async def fill_profile_name(page, profile_name: str, target_locator=None) -> bool:
    """Robustly types and verifies the profile name in the input field, clearing any 'هذه الخانة مطلوبة' error."""
    await remove_cookie_banner(page)
    input_box = target_locator
    if not input_box:
        # Strictly exclude search bar!
        selectors = [
            "input[placeholder*='ادخل الاسم']",
            "input[placeholder*='الاسم']:not([placeholder*='ابحث'])",
            "input[placeholder*='اسم']:not([placeholder*='ابحث'])",
            "div[role='dialog'] input[type='text']",
            "div[role='dialog'] input",
            "input#profileName",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    input_box = loc
                    break
            except Exception:
                pass

    if not input_box:
        print("[!] Profile name input not found via selectors, searching visible non-search inputs...", flush=True)
        try:
            has_input = await page.evaluate("""() => {
                const inputs = Array.from(document.querySelectorAll('input')).filter(el => {
                    const rect = el.getBoundingClientRect();
                    const ph = el.getAttribute('placeholder') || '';
                    const id = el.getAttribute('id') || '';
                    const name = el.getAttribute('name') || '';
                    if (id.includes('search') || name.includes('search') || ph.includes('ابحث') || ph.includes('بحث')) return false;
                    if (id.includes('onetrust') || id.includes('vendor')) return false;
                    return rect.width > 50 && rect.height > 15;
                });
                if (inputs.length > 0) {
                    inputs[0].setAttribute('data-target-profile-input', 'true');
                    return true;
                }
                return false;
            }""")
            if has_input:
                input_box = page.locator("input[data-target-profile-input='true']").first
        except Exception:
            pass

    if not input_box:
        print("[!] Profile name input could not be found!", flush=True)
        return False

    try:
        await input_box.scroll_into_view_if_needed()
        await input_box.click(timeout=2000)
    except Exception:
        try:
            await input_box.click(force=True)
        except Exception:
            pass

    await asyncio.sleep(0.2)
    try:
        await input_box.press("Control+A")
        await input_box.press("Backspace")
    except Exception:
        pass
    await asyncio.sleep(0.2)

    # 1. Native React input setter and event dispatch directly on input_box to ensure state sync and clear 'هذه الخانة مطلوبة'
    try:
        await input_box.evaluate("""(el, val) => {
            el.focus();
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
            if (setter) {
                setter.call(el, val);
            } else {
                el.value = val;
            }
            el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
        }""", profile_name)
    except Exception:
        pass

    # 2. Sequential typing and fill
    try:
        await input_box.fill(profile_name)
    except Exception:
        pass

    try:
        await input_box.press_sequentially(profile_name, delay=30)
    except Exception:
        pass

    # 3. Trigger typing keys to finalize React form validation
    try:
        await input_box.press("Space")
        await asyncio.sleep(0.05)
        await input_box.press("Backspace")
    except Exception:
        pass

    await asyncio.sleep(0.5)

    # 4. Verify value is set
    try:
        val = await input_box.input_value()
        if val != profile_name:
            print(f"[*] Value mismatch ('{val}' vs '{profile_name}'), forcing fill...", flush=True)
            await input_box.fill(profile_name)
            await input_box.evaluate("""(el, val) => {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(el, val); else el.value = val;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""", profile_name)
    except Exception:
        pass

    print(f"[+] Successfully filled profile name: '{profile_name}'", flush=True)
    return True


async def human_type_locator(page, locator, text: str) -> bool:
    """Wrapper for fill_profile_name to safely type into locator with React event handling."""
    return await fill_profile_name(page, text, target_locator=locator)


async def create_single_profile(page: Page, profile_name: str) -> bool:
    """Full lifecycle to add a new profile: navigate to screen -> click '+' -> type name -> click 'انشاء' -> skip onboarding."""
    print(f"\n==========================================", flush=True)
    print(f"[*] Starting creation for: {profile_name}", flush=True)
    print(f"==========================================", flush=True)

    # 1. Skip onboarding / interests ONLY if not already on profile selection screen
    if not await is_on_select_profile_screen(page):
        await handle_skip_onboarding(page)

    # 2. Ensure we are on profile selection screen with retry
    on_screen = False
    for attempt in range(3):
        if await navigate_to_select_profile_screen(page):
            on_screen = True
            break
        print(f"[*] Retry reaching profile screen (attempt {attempt+1}/3)...", flush=True)
        await asyncio.sleep(2.0)

    if not on_screen:
        print(f"[!] Could not reach profile screen for {profile_name}, aborting single profile attempt.", flush=True)
        return False

    await asyncio.sleep(1.0)

    # 3. Click "+" / "إضافة" to open modal
    opened_modal = False
    for attempt in range(3):
        if await click_add_profile(page):
            opened_modal = True
            break
        print(f"[*] Retrying click on '+' (attempt {attempt+1}/3)...", flush=True)
        await asyncio.sleep(1.5)

    if not opened_modal:
        print(f"[!] Could not open profile modal for {profile_name}", flush=True)
        return False

    await asyncio.sleep(1.0)

    # 4. Enter profile name in modal
    print(f"[*] Typing '{profile_name}' into modal...", flush=True)
    filled = await fill_profile_name(page, profile_name)
    if not filled:
        print(f"[!] Failed to fill profile name for {profile_name}", flush=True)
        return False

    await asyncio.sleep(1.0)

    # 5. Click "انشاء" (Wait until button is enabled! Never force click disabled button!)
    print(f"[*] Waiting for 'انشاء' button to become enabled for {profile_name}...", flush=True)
    btn_selectors = [
        "button:has-text('انشاء')",
        "button:has-text('إنشاء')",
        "div[role='dialog'] button:has-text('انشاء')",
        "div[role='dialog'] button:has-text('إنشاء')",
        "button[type='submit']",
    ]
    created = False
    for attempt in range(12):
        for s in btn_selectors:
            try:
                loc = page.locator(s).last
                if await loc.is_visible(timeout=500):
                    is_dis = await loc.is_disabled()
                    if not is_dis:
                        print(f"[*] 'انشاء' button is enabled. Waiting 2 seconds before clicking...", flush=True)
                        await asyncio.sleep(2.0)
                        await loc.click()
                        print(f"[+] Clicked 'انشاء' using selector: {s}", flush=True)
                        created = True
                        break
            except Exception:
                pass
        if created:
            break

        # Fallback: check via JS if button is enabled
        try:
            created = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(el => {
                    const t = (el.innerText || '').trim();
                    return (t === 'انشاء' || t === 'إنشاء') && !el.disabled;
                });
                if (b) {
                    b.click();
                    return true;
                }
                return false;
            }""")
            if created:
                print("[+] Clicked enabled 'انشاء' via JS evaluate!", flush=True)
                break
        except Exception:
            pass

        # If button is still disabled after 3 attempts, re-trigger typing into input to clear validation
        if attempt % 3 == 2:
            print("[*] Button still disabled, re-verifying input...", flush=True)
            await fill_profile_name(page, profile_name)

        await asyncio.sleep(0.8)

    if not created:
        print(f"[!] Warning: 'انشاء' button not confirmed for {profile_name}", flush=True)

    await asyncio.sleep(3.5)

    # 6. Skip onboarding / interests if shown after profile creation
    await handle_skip_onboarding(page)
    await asyncio.sleep(1.5)
    print(f"[+] Profile {profile_name} creation cycle finished.\n", flush=True)
    return True


async def automate_all_profiles(page: Page, status_msg, base_name: str = "dahoom", custom_names: Optional[List[str]] = None) -> List[str]:
    """Automates creation of 4 profiles using custom_names or base_name + 1..4."""
    print("[*] Starting automated profile onboarding and creation...", flush=True)

    if custom_names and len(custom_names) >= 4:
        names_to_create = [str(n).strip() for n in custom_names[:4]]
    else:
        names_to_create = [f"{base_name}1", f"{base_name}2", f"{base_name}3", f"{base_name}4"]

    p1 = names_to_create[0]

    # Step 1: First Profile on onboarding screen
    try:
        await remove_cookie_banner(page)
        print(f"[*] Setting up Profile 1: '{p1}'...", flush=True)
        # Check if first profile input is on screen
        has_p1_input = False
        p1_selectors = [
            "input#profileName",
            "input[placeholder*='ادخل الاسم']",
            "input[placeholder*='اسم الملف']",
            "input[placeholder*='الاسم']:not([placeholder*='ابحث'])",
        ]
        for sel in p1_selectors:
            try:
                if await page.locator(sel).first.is_visible(timeout=2000):
                    has_p1_input = True
                    break
            except Exception:
                pass

        if has_p1_input:
            await fill_profile_name(page, p1)
            next_btn = page.locator("button:has-text('متابعة'), button[type='submit']").first
            for _ in range(8):
                if await next_btn.is_visible() and not await next_btn.is_disabled():
                    break
                await asyncio.sleep(0.5)

            print(f"[*] Profile 1 button is enabled. Waiting 2 seconds before clicking 'متابعة'...", flush=True)
            await asyncio.sleep(2.0)
            await next_btn.click()

            for s in range(12):
                await asyncio.sleep(1.0)
                await remove_cookie_banner(page)
                if not await page.locator("input#profileName").is_visible():
                    break
                if s % 3 == 0:
                    try:
                        if await next_btn.is_visible() and not await next_btn.is_disabled():
                            print(f"[*] Retrying Profile 1 'متابعة' click due to network lag...", flush=True)
                            await next_btn.click()
                    except Exception:
                        pass
            await asyncio.sleep(2.0)
        else:
            print("[*] Profile 1 onboarding input not present, will handle via profile switcher...", flush=True)
    except Exception as e:
        print(f"[!] {p1} initial step note: {e}", flush=True)

    # Skip interests if shown
    await handle_skip_onboarding(page)
    await asyncio.sleep(2.0)

    # Step 2: Loop for profiles 2, 3, 4
    for prof in names_to_create[1:]:
        try:
            if status_msg:
                await status_msg.edit_text(f"⏳ جاري إنشاء الملف الشخصي ({prof})...")
        except Exception:
            pass

        try:
            await create_single_profile(page, prof)
        except Exception as err:
            print(f"[!] Error creating profile {prof}: {err}", flush=True)
            await handle_skip_onboarding(page)

    return names_to_create


async def human_type(page: Page, selector: str, text: str) -> None:
    """Type characters safely into input field with human intervals and focus verification."""
    await remove_cookie_banner(page)
    loc = page.locator(selector)
    try:
        await loc.click(timeout=3000)
    except Exception:
        await remove_cookie_banner(page)
        await loc.click(force=True)
    try:
        await loc.press_sequentially(text, delay=random.randint(25, 50))
    except Exception:
        for char in text:
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.03, 0.08))
    # Verify input value is 100% complete
    try:
        val = await loc.input_value()
        if val != text:
            await loc.fill(text)
    except Exception:
        pass


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
    session = RegistrationSession()
    session.user_id = user_id
    context.user_data["session"] = session
    context.user_data["user_id"] = user_id
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
    session.user_id = batch_info.get("user_id", 0)
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

        if not user_settings.get("create_profiles_enabled", True):
            session.skip_profiles = True
            await update.message.reply_text(
                f"✅ تم حفظ البريد: `{val}`\n"
                f"🔐 **تم تطبيق كلمة المرور الدائمة:** `{perm_pwd}`\n\n"
                "ℹ️ **تنبيه:** ميزة تجهيز الملفات (معطّلة حالياً).\n"
                "سيتوقف البوت تلقائياً فور اكتمال إنشاء الحساب دون إعداد أي ملفات.\n\n"
                "🚀 جاري الانطلاق مباشرة لطلب كود التحقق...",
                parse_mode="Markdown"
            )
            return await launch_browser_and_request_otp(update.message, session, "account", context)

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
            "👤 **أدخل الآن أسماء الملفات الشخصية (4 ملفات):**\n\n"
            "🔹 **الخيار 1 (اسم أساسي تلقائي):** أرسل اسماً واحداً مثل `dahoom` لترقيمه تلقائياً (`dahoom1` إلى `dahoom4`).\n"
            "🔹 **الخيار 2 (4 أسماء مخصصة بنفسك):** أرسل 4 أسماء مفصولة بسطر جديد أو فاصلة (،) وسيتم تسميتها بالضبط بالترتيب بدون أي تغيير أو زيادة!\n\n"
            "💡 أو اختر من الأزرار بالأسفل:",
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
    if not user_settings.get("create_profiles_enabled", True):
        session.skip_profiles = True
        await update.message.reply_text(
            "✅ **تم حفظ كلمة المرور بنجاح.**\n\n"
            "ℹ️ **تنبيه:** ميزة تجهيز الملفات (معطّلة حالياً).\n"
            "سيتوقف البوت تلقائياً فور اكتمال إنشاء الحساب دون إعداد أي ملفات.\n\n"
            "🚀 جاري الانطلاق مباشرة لطلب كود التحقق...",
            parse_mode="Markdown"
        )
        return await launch_browser_and_request_otp(update.message, session, "account", context)

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
        "👤 **أدخل الآن أسماء الملفات الشخصية (4 ملفات):**\n\n"
        "🔹 **الخيار 1 (اسم أساسي تلقائي):** أرسل اسماً واحداً مثل `dahoom` لترقيمه تلقائياً (`dahoom1` إلى `dahoom4`).\n"
        "🔹 **الخيار 2 (4 أسماء مخصصة بنفسك):** أرسل 4 أسماء مفصولة بسطر جديد أو فاصلة (،) وسيتم تسميتها بالضبط بالترتيب بدون أي تغيير أو زيادة!\n\n"
        "💡 أو اختر من الأزرار بالأسفل:",
        reply_markup=get_name_keyboard(),
        parse_mode="Markdown"
    )
    return BASE_NAME


async def safe_edit_message(msg, text: str, **kwargs):
    """Edit message text safely, falling back to plain text if Markdown parsing fails."""
    try:
        return await msg.edit_text(text, **kwargs)
    except Exception as exc:
        if "parse" in str(exc).lower() and "parse_mode" in kwargs:
            kwargs.pop("parse_mode", None)
            try:
                return await msg.edit_text(text, **kwargs)
            except Exception:
                pass
        logger.error(f"[!] safe_edit_message error: {exc}")
        return None


async def launch_browser_and_request_otp(msg_target, session: RegistrationSession, base: str, context: ContextTypes.DEFAULT_TYPE = None) -> int:
    batch_prefix = ""
    batch_hint = "💡 يرجى إدخال كود التحقق لإكمال إعداد الحساب فوراً."
    if context and context.user_data.get("batch_info"):
        bi = context.user_data["batch_info"]
        c_num = bi.get("current_index", 0) + 1
        b_tot = bi.get("total", 0)
        batch_prefix = f"🔄 **الإنشاء التلقائي: حساب {c_num} من {b_tot}**\n\n"
        batch_hint = "💡 فور إدخال الرمز، سينتهي إعداد هذا الحساب وينتقل البوت تلقائياً للإيميل التالي!"

    custom_names = getattr(session, "custom_names", None)
    skip_profiles = getattr(session, "skip_profiles", False)

    if skip_profiles:
        msg_names = "ℹ️ **نمط الإنشاء:** إنشاء حساب فقط (بدون إعداد ملفات شخصية).\n\n"
    elif custom_names and len(custom_names) == 4:
        names_str = ", ".join(f"`{n}`" for n in custom_names)
        msg_names = f"✅ **تم اعتماد أسماء الملفات الـ 4 المخصصة:** {names_str}\n\n"
    else:
        msg_names = f"✅ **تم اعتماد اسم الملفات:** `{base}1`, `{base}2`, `{base}3`, `{base}4`\n\n"

    # Determine user_id to get rotating proxy from pool
    u_id = getattr(session, "user_id", 0)
    if not u_id:
        if context and context.user_data.get("user_id"):
            u_id = context.user_data["user_id"]
        elif hasattr(msg_target, "chat_id"):
            u_id = msg_target.chat_id
        elif hasattr(msg_target, "from_user") and msg_target.from_user:
            u_id = msg_target.from_user.id

    proxy_config = get_next_proxy_for_user(u_id)
    session.proxy_config = proxy_config

    proxy_display = ""
    proxy_playwright = None
    if proxy_config:
        proxy_playwright = {
            "server": proxy_config["server"],
        }
        if proxy_config.get("username"):
            proxy_playwright["username"] = proxy_config["username"]
            proxy_playwright["password"] = proxy_config.get("password", "")

        host_disp = proxy_config.get("host") or proxy_config["server"].replace("http://", "").replace("https://", "")
        idx_disp = proxy_config.get("proxy_index", 1)
        tot_disp = proxy_config.get("total_proxies", 1)
        p_type = proxy_config.get("proxy_type", "private")
        type_str = "خاص بي" if p_type == "private" else ("عام مشترك" if p_type == "global" else "افتراضي")
        proxy_display = f"🌐 **البروكسي المستخدم ({type_str}):** `{host_disp}` (#{idx_disp} من {tot_disp})\n\n"
        logger.info(f"Rotating Proxy picked: {host_disp} (#{idx_disp}/{tot_disp}) for user {u_id}")

    status_msg = await msg_target.reply_text(
        batch_prefix +
        proxy_display +
        msg_names +
        "⚡ جاري التواصل مع Shahid وطلب كود التحقق... انتظر لحظات.",
        reply_markup=get_cancel_keyboard(),
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

        browser = await p.chromium.launch(
            headless=is_headless,
            args=chromium_args,
            proxy=proxy_playwright,
            ignore_default_args=["--enable-automation"],
        )
        session.browser = browser

        is_egypt = (
            proxy_config and (
                proxy_config.get("is_egypt", True)
                or "eg" in str(proxy_config.get("server", "")).lower()
                or "eg" in str(proxy_config.get("username", "")).lower()
            )
        )
        locale_setting = "ar-EG" if is_egypt else "ar-SA"
        tz_setting = "Africa/Cairo" if is_egypt else "Asia/Riyadh"

        bcontext = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            locale=locale_setting,
            timezone_id=tz_setting,
            permissions=["geolocation"],
        )
        session.context = bcontext

        page = await bcontext.new_page()
        session.page = page

        # Auto-dismiss cookie/privacy banner (OneTrust) as soon as it mounts in DOM
        await page.add_init_script("""
            (() => {
                const clickConsent = () => {
                    try {
                        if (window.OneTrust && typeof window.OneTrust.AcceptAll === 'function') {
                            window.OneTrust.AcceptAll();
                        }
                        const b = document.getElementById('onetrust-accept-btn-handler') ||
                                  document.getElementById('accept-recommended-btn-handler');
                        if (b) {
                            b.click();
                        }
                    } catch(e) {}
                };
                document.addEventListener('DOMContentLoaded', clickConsent);
                const observer = new MutationObserver(() => {
                    clickConsent();
                });
                if (document.documentElement) {
                    observer.observe(document.documentElement, { childList: true, subtree: true });
                }
            })();
        """)

        # Apply stealth evasions
        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        # Navigate to registration hub
        await page.goto("https://shahid.mbc.net/ar/hub/registration", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.5)
        for _ in range(6):
            if await remove_cookie_banner(page):
                break
            await asyncio.sleep(0.4)

        # Enter email safely and trigger form validation
        user_field = page.locator("#userName")
        if not await user_field.is_visible():
            await user_field.wait_for(timeout=15000)

        await remove_cookie_banner(page)
        await user_field.click()
        await user_field.press_sequentially(session.email, delay=35)
        await asyncio.sleep(0.5)
        await remove_cookie_banner(page)

        # Helper to trigger reCAPTCHA Enterprise resolution via Shahid's React Fiber
        async def trigger_recaptcha():
            try:
                await page.evaluate("""async () => {
                    const btn = document.querySelector("button[type='submit']");
                    const key = btn ? Object.keys(btn).find(k => k.startsWith('__reactFiber')) : null;
                    let fiber = key ? btn[key] : null;
                    while (fiber) {
                        if (fiber.memoizedProps?.bottomChildren?.props?.getTokenOnDemand) {
                            const p = fiber.memoizedProps.bottomChildren.props.getTokenOnDemand('REGISTER');
                            // Race with 3.5s timeout so it NEVER hangs
                            await Promise.race([p, new Promise(r => setTimeout(r, 3500))]);
                            break;
                        }
                        fiber = fiber.return;
                    }
                }""")
            except Exception as e:
                logger.warning(f"Note on reCAPTCHA execution: {e}")

        logger.info("[*] Triggering reCAPTCHA Enterprise resolution...")
        await trigger_recaptcha()

        # Active polling loop: monitor transition and retry clicking 'متابعة' on network glitch/warning
        logger.info("[*] Waiting for submit button to be enabled and transition to OTP screen...")
        is_otp = False
        has_clicked = False
        last_click_time = 0.0
        retry_count = 0
        max_retries = 15

        for s in range(90):
            await asyncio.sleep(1.0)
            await remove_cookie_banner(page)
            cur_url = page.url

            # 1. Success check: Reached OTP verification screen
            try:
                if "/hub/verification" in cur_url or await page.locator("#otpCode").is_visible(timeout=300):
                    is_otp = True
                    logger.info(f"[+] Reached OTP verification screen at second {s+1}!")
                    break
            except Exception:
                pass

            # 2. Check if redirected to login (email already registered)
            if "/hub/login" in cur_url:
                logger.info(f"[*] Reached login screen at second {s+1} (email already registered).")
                break

            # 3. Check for network glitch / proxy lag banner ("نشاط غير معتاد" / "تقييد الوصول" / "خطأ في الشبكة")
            body_text = ""
            try:
                body_text = await page.locator("body").inner_text()
            except Exception:
                pass

            has_network_warning = any(w in body_text for w in ["تقييد الوصول", "نشاط غير معتاد", "مشكلة في الشبكة", "خطأ في الشبكة"])

            # 4. Handle clicking and retrying 'متابعة'
            try:
                btn = page.locator("form button[type='submit'], button:has-text('متابعة')").first
                if await btn.is_visible(timeout=300):
                    btn_enabled = not await btn.is_disabled()
                    now = asyncio.get_event_loop().time()

                    if btn_enabled:
                        if not has_clicked:
                            logger.info(f"[*] Submit button is ENABLED at second {s+1}! Waiting 3 seconds before clicking...")
                            await asyncio.sleep(3.0)
                            if not await btn.is_disabled():
                                logger.info("[*] Clicking submit button now...")
                                await btn.click()
                                has_clicked = True
                                last_click_time = now
                                await asyncio.sleep(1.5)
                        elif has_network_warning and (now - last_click_time >= 3.0) and retry_count < max_retries:
                            retry_count += 1
                            logger.info(f"[*] Network warning detected. Waiting 3 seconds before retry ({retry_count}/{max_retries})...")
                            await asyncio.sleep(3.0)
                            await trigger_recaptcha()
                            await asyncio.sleep(1.5)
                            # Verify button is enabled and wait 3 seconds before clicking per user instruction
                            if await btn.is_visible(timeout=500) and not await btn.is_disabled():
                                logger.info(f"[*] Button is ready/white. Waiting 3 seconds before clicking (attempt {retry_count}/{max_retries})...")
                                await asyncio.sleep(3.0)
                                try:
                                    await btn.click()
                                except Exception:
                                    await page.evaluate("() => document.querySelector(\"button[type='submit'], button:has-text('متابعة')\")?.click()")
                                last_click_time = asyncio.get_event_loop().time()
                                await asyncio.sleep(2.0)
                        elif not has_network_warning and (now - last_click_time >= 5.0) and (s % 4 == 0):
                            logger.info(f"[*] Retrying submit click at second {s+1}...")
                            await btn.click()
                            last_click_time = now
                            await asyncio.sleep(1.0)
            except Exception:
                pass

        # Final retry attempts if still not on OTP screen
        if not is_otp and not ("/hub/login" in page.url):
            for final_try in range(2):
                try:
                    btn = page.locator("form button[type='submit'], button:has-text('متابعة')").first
                    if await btn.is_visible(timeout=500) and not await btn.is_disabled():
                        logger.info(f"[*] Final retry attempt {final_try+1} for 'متابعة'...")
                        await trigger_recaptcha()
                        await asyncio.sleep(1.5)
                        await btn.click()
                        await asyncio.sleep(3.0)
                        if "/hub/verification" in page.url or await page.locator("#otpCode").is_visible(timeout=500):
                            is_otp = True
                            logger.info("[+] Reached OTP verification screen on final retry!")
                            break
                except Exception:
                    pass

        if is_otp:
            await remove_cookie_banner(page)
            await asyncio.sleep(1.0)
            await remove_cookie_banner(page)
            await safe_edit_message(
                status_msg,
                batch_prefix +
                f"📨 تم إرسال رمز التحقق بنجاح إلى بريدك الإلكتروني:\n`{session.email}`\n\n"
                "🔢 **أدخل رمز التحقق (OTP) المكوّن من 6 أرقام هنا لإكمال التسجيل:**\n\n"
                f"👥 سيتم تجهيز الملفات: `{base}1` إلى `{base}4`\n" +
                batch_hint,
                reply_markup=get_otp_keyboard(),
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
                    await safe_edit_message(
                        status_msg,
                        f"⚠️ البريد `{session.email}` مسجل مسبقاً في شاهد!\n"
                        "🗑️ تم حذفه تلقائياً من قائمتك لتفادي التكرار.\n\n"
                        f"🚀 **جاري الانتقال تلقائياً للحساب التالي ({curr + 1} من {tot})...**",
                        parse_mode="Markdown"
                    )
                    await cleanup_session(context, force=True)
                    await asyncio.sleep(2.0)
                    return await start_batch_next_account(msg_target, context)
                else:
                    return await finish_batch(msg_target, context)

            await safe_edit_message(
                status_msg,
                "⚠️ هذا البريد مسجل مسبقاً في Shahid! يرجى استخدام بريد إلكتروني جديد.",
                reply_markup=get_main_inline_keyboard(),
            )
            return ConversationHandler.END

        # 2. Check for actual block banner
        body_text = await page.locator("body").inner_text()
        if "تقييد الوصول" in body_text or "نشاط غير معتاد" in body_text:
            await safe_edit_message(
                status_msg,
                "⚠️ تعذر استكمال الطلب حالياً من موقع شاهد بسبب ضغط في الشبكة بعد عدة محاولات. يرجى المحاولة بعد قليل.",
                reply_markup=get_main_inline_keyboard(),
            )
            return ConversationHandler.END

        # Fallback: Registration did not reach verification screen
        await safe_edit_message(
            status_msg,
            "⚠️ تعذر استكمال طلب كود التحقق من شاهد حالياً.\nيرجى إعادة المحاولة.",
            reply_markup=get_main_inline_keyboard(),
        )
        return ConversationHandler.END

    except Exception as exc:
        logger.error(f"[!] Error in launch_browser_and_request_otp: {exc}", exc_info=True)
        await safe_edit_message(
            status_msg,
            "⚠️ تعذر الاتصال بموقع شاهد حالياً، يرجى المحاولة مجدداً.",
            reply_markup=get_cancel_keyboard(),
        )
        return CODE


async def base_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    session = get_session(context)
    base = "dahoom"

    if update.callback_query:
        query = update.callback_query
        await query.answer()

        if query.data == "act_default_name":
            base = "dahoom"
            session.base_name = base
            session.custom_names = None
            msg = query.message
            return await launch_browser_and_request_otp(msg, session, base, context)

        elif query.data == "act_custom_4_names":
            await query.message.reply_text(
                "✍️ **أدخل الآن الـ 4 أسماء التي تريدها للملفات الأربعة بالترتيب:**\n\n"
                "اكتب كل اسم في سطر جديد، أو افصل بينها بفاصلة (،):\n"
                "```text\n"
                "الملف الأول\n"
                "الملف الثاني\n"
                "الملف الثالث\n"
                "الملف الرابع\n"
                "```\n"
                "*(مثال: `أحمد، سارة، خالد، منى`)*\n\n"
                "💡 سيقوم البوت بتسمية كل ملف بالاسم الذي حددته بالضبط وبنفس الترتيب بدون أي ترقيم إضافي.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏭️ استخدام dahoom الافتراضي", callback_data="act_default_name")],
                    [InlineKeyboardButton("🚫 تخطي تسمية الملفات (حساب فقط)", callback_data="act_skip_profiles")],
                    [InlineKeyboardButton("❌ إلغاء العملية", callback_data="act_cancel")],
                ]),
                parse_mode="Markdown"
            )
            return BASE_NAME

        elif query.data == "act_skip_profiles":
            session.skip_profiles = True
            await query.message.reply_text(
                "🛑 **تم اختيار تخطي تسمية الملفات!**\n"
                "سيتوقف البوت فور إتمام إنشاء الحساب دون الدخول في إعداد أي ملفات.\n\n"
                "🚀 جاري الانطلاق الآن لطلب كود التحقق...",
                parse_mode="Markdown"
            )
            return await launch_browser_and_request_otp(query.message, session, "account", context)

    else:
        raw = update.message.text.strip()
        # Parse for 4 names separated by newlines, commas (Arabic or English)
        parts = [re.sub(r"[^\w\s-]", "", p).strip() for p in re.split(r"[\n,،]+", raw)]
        parts = [p[:18] for p in parts if p]

        if len(parts) == 4:
            session.custom_names = parts[:4]
            session.base_name = parts[0]
            names_summary = "\n".join(f"  {i+1}️⃣ `{name}`" for i, name in enumerate(parts))
            await update.message.reply_text(
                f"✅ **تم اعتماد الـ 4 أسماء للملفات بنجاح وبنفس الترتيب:**\n{names_summary}\n\n"
                "🚀 جاري الانطلاق الآن لطلب كود التحقق وتجهيز الحساب...",
                parse_mode="Markdown"
            )
            return await launch_browser_and_request_otp(update.message, session, session.base_name, context)

        elif len(parts) in (2, 3) or len(parts) > 4:
            await update.message.reply_text(
                f"⚠️ لقد أدخلت ({len(parts)}) أسماء والمطلوب هو **4 أسماء بالضبط** (كل اسم لملف).\n\n"
                "يرجى إرسال الـ 4 أسماء مفصولة بسطر جديد أو فاصلة (،) أو إرسال اسم واحد فقط للترقيم التلقائي:",
                reply_markup=get_name_keyboard(),
                parse_mode="Markdown"
            )
            return BASE_NAME

        else:
            cleaned = re.sub(r"[^\w\s-]", "", raw).strip()
            base = cleaned[:18] if cleaned else "dahoom"
            session.base_name = base
            session.custom_names = None
            return await launch_browser_and_request_otp(update.message, session, base, context)


async def resend_otp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles clicking '🔄 إعادة إرسال الكود' inline button during OTP verification."""
    query = update.callback_query
    session = get_session(context)

    if not session or not session.page or session.page.is_closed():
        await query.answer("❌ لا توجد جلسة تسجيل نشطة حالياً. يرجى بدء إنشاء الحساب من جديد.", show_alert=True)
        return CODE

    page = session.page
    try:
        await remove_cookie_banner(page)

        # 1. Look for resend button / link on Shahid's verification screen
        resend_selectors = [
            "button:has-text('إعادة إرسال')",
            "button:has-text('إعادة الإرسال')",
            "button:has-text('ارسل الرمز مجددا')",
            "button:has-text('ارسل الرمز مجدداً')",
            "button:has-text('أرسل الرمز مجدداً')",
            "button:has-text('ارسل مجددا')",
            "button:has-text('أرسل مجدداً')",
            "a:has-text('إعادة إرسال')",
            "a:has-text('إعادة الإرسال')",
            "span:has-text('إعادة إرسال')",
            "p:has-text('إعادة إرسال')",
            "[data-testid*='resend']",
            "button[class*='resend']",
        ]

        resend_btn = None
        for sel in resend_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=400):
                    resend_btn = loc
                    break
            except Exception:
                pass

        btn_text = ""
        is_disabled = False

        if resend_btn:
            try:
                btn_text = (await resend_btn.inner_text()).strip()
            except Exception:
                pass
            try:
                is_disabled = await resend_btn.is_disabled()
            except Exception:
                pass
        else:
            info = await page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('button, a, span, p, div'));
                const target = els.find(el => {
                    const t = (el.innerText || '').trim();
                    return (t.includes('إعادة إرسال') || t.includes('إعادة الإرسال') || t.includes('ارسل الرمز') || t.includes('أرسل الرمز')) && t.length < 50;
                });
                if (target) {
                    return {
                        found: true,
                        text: (target.innerText || '').trim(),
                        disabled: target.disabled || target.getAttribute('aria-disabled') === 'true' || target.classList.contains('disabled') || target.classList.contains('cursor-not-allowed')
                    };
                }
                return { found: false };
            }""")
            if info.get("found"):
                btn_text = info.get("text", "")
                is_disabled = info.get("disabled", False)

        # 2. Check if button is disabled or in countdown cooldown
        if is_disabled or any(w in btn_text for w in ["ثانية", "ثوان", "انتظر"]):
            msg = f"⏳ موقع شاهد يطلب الانتظار قبل إعادة إرسال رمز جديد:\n({btn_text})"
            await query.answer(msg, show_alert=True)
            return CODE

        # 3. Click the resend button
        clicked = False
        if resend_btn:
            try:
                await resend_btn.click(timeout=3000)
                clicked = True
            except Exception:
                pass

        if not clicked:
            clicked = await page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('button, a, span, p, div'));
                const target = els.find(el => {
                    const t = (el.innerText || '').trim();
                    return (t.includes('إعادة إرسال') || t.includes('إعادة الإرسال') || t.includes('ارسل الرمز') || t.includes('أرسل الرمز')) && t.length < 50;
                });
                if (target) {
                    target.scrollIntoView({ behavior: 'instant', block: 'center' });
                    target.click();
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    return true;
                }
                return false;
            }""")

        if clicked:
            logger.info(f"[+] Resend OTP clicked successfully for {session.email}")
            await query.answer("✅ تم طلب إعادة إرسال رمز التحقق بنجاح! يرجى فحص بريدك الإلكتروني الآن.", show_alert=True)
            try:
                current_text = query.message.text or ""
                if "🔄 تم طلب رمز جديد" not in current_text:
                    new_text = current_text + "\n\n🔄 **(تم طلب إعادة إرسال كود تحقق جديد، يرجى فحص البريد الآن!)**"
                    await query.message.edit_text(new_text, reply_markup=get_otp_keyboard(), parse_mode="Markdown")
            except Exception:
                pass
        else:
            await query.answer("⚠️ تعذر العثور على زر إعادة الإرسال في الصفحة حالياً.", show_alert=True)

    except Exception as exc:
        logger.error(f"[!] Error in resend_otp_callback: {exc}", exc_info=True)
        await query.answer("⚠️ حدث خطأ أثناء طلب إعادة الإرسال. يرجى المحاولة بعد قليل.", show_alert=True)

    return CODE


async def code_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    val = update.message.text.strip()
    if not re.fullmatch(r"\d{6}", val):
        await update.message.reply_text(
            "⚠️ يرجى إدخال رمز صحيح مكوّن من 6 أرقام فقط:",
            reply_markup=get_otp_keyboard(),
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
        await remove_cookie_banner(page)
        otp_field = page.locator("#otpCode")
        if not await otp_field.is_visible():
            await remove_cookie_banner(page)
            await otp_field.wait_for(timeout=10000)

        # Type OTP using fill to ensure React enables the submit button immediately
        await remove_cookie_banner(page)
        await otp_field.click()
        await otp_field.fill(val)

        # Wait for submit button to be visible and enabled, then wait 3 seconds before clicking
        submit_btn = page.locator("form button[type='submit'], button:has-text('تحقق'), button:has-text('متابعة')").first
        for _ in range(10):
            if await submit_btn.is_visible() and not await submit_btn.is_disabled():
                break
            await asyncio.sleep(0.5)

        logger.info("[*] OTP submit button is enabled. Waiting 3 seconds before clicking per user instruction...")
        await asyncio.sleep(3.0)

        # Click submit
        await remove_cookie_banner(page)
        logger.info("[*] Clicking OTP submit button...")
        try:
            await submit_btn.click(timeout=4000)
        except Exception:
            try:
                await otp_field.press("Enter")
            except Exception:
                await submit_btn.click(force=True)

        # Resilient wait for password page with automatic retries on network pressure/glitch (up to 15 attempts)
        pwd_field = page.locator("input[type='password']").first
        reached_password = False
        otp_retry_count = 0
        max_otp_retries = 15

        for s in range(60):
            await asyncio.sleep(1.0)
            await remove_cookie_banner(page)

            if await pwd_field.is_visible():
                reached_password = True
                logger.info(f"[+] Reached password screen at second {s+1}!")
                break

            # Check for incorrect code error (user input error)
            body_text = ""
            try:
                body_text = await page.locator("body").inner_text()
            except Exception:
                pass

            if any(err in body_text for err in ["رمز غير صحيح", "كود غير صحيح", "رمزاً غير صحيح", "invalid"]):
                await safe_edit_message(
                    status_msg,
                    "❌ رمز التحقق غير صحيح. يرجى التأكد وإعادة إرسال الرمز الصحيح:",
                    reply_markup=get_otp_keyboard(),
                )
                return CODE

            # Check for network pressure / warning banner on OTP step
            has_net_warn = any(w in body_text for w in ["تقييد الوصول", "نشاط غير معتاد", "مشكلة في الشبكة", "خطأ في الشبكة"])
            if (has_net_warn or s % 4 == 0) and otp_retry_count < max_otp_retries:
                otp_retry_count += 1
                logger.info(f"[*] OTP step network lag/pressure detected. Waiting 3 seconds before retry ({otp_retry_count}/{max_otp_retries})...")
                await asyncio.sleep(3.0)
                try:
                    btn = page.locator("form button[type='submit'], button:has-text('تحقق'), button:has-text('متابعة')").first
                    if await btn.is_visible() and not await btn.is_disabled():
                        logger.info(f"[*] OTP button is white/enabled. Waiting 3 seconds before clicking ({otp_retry_count}/{max_otp_retries})...")
                        await asyncio.sleep(3.0)
                        await btn.click()
                except Exception:
                    pass

        if not reached_password:
            await safe_edit_message(
                status_msg,
                "⚠️ لم يتم قبول الرمز أو حدث ضغط بالشبكة بعد 15 محاولة.\nيرجى التأكد وإعادة إرسال رمز التحقق مجدداً:",
                reply_markup=get_otp_keyboard(),
            )
            return CODE

        # 1. Fill the password
        await pwd_field.fill(session.password)
        await asyncio.sleep(1.0)

        # 2. Defuse links and check checkboxes
        await page.evaluate("""() => {
            const card = document.querySelector('input[type="password"]')?.closest('div.relative, div.flex, form') || document.body;
            card.querySelectorAll('a').forEach(a => {
                a.onclick = (e) => { e.preventDefault(); e.stopPropagation(); return false; };
                a.removeAttribute('href');
            });
            ['promotionsNews', 'termsConditions'].forEach(id => {
                const el = document.getElementById(id);
                if (el && !el.checked) el.click();
            });
        }""")
        await asyncio.sleep(1.5)

        # 3. Wait for submit button to be visible and enabled, then wait 3 seconds before clicking
        submit_btn = page.locator("form button[type='submit'], button:has-text('إنشاء حساب'), button:has-text('متابعة')").first
        for _ in range(10):
            if await submit_btn.is_visible() and not await submit_btn.is_disabled():
                break
            await asyncio.sleep(0.5)

        logger.info("[*] Password submit button is enabled. Waiting 3 seconds before clicking per user instruction...")
        await asyncio.sleep(3.0)

        try:
            await submit_btn.click(timeout=5000)
        except Exception:
            await submit_btn.evaluate("btn => btn.click()")

        # 4. Verify URL transition with automatic retry on network pressure/lag (up to 15 attempts)
        logger.info("[*] Waiting for account creation and redirect to onboarding/profile...")
        is_registered = False
        pwd_retry_count = 0
        max_pwd_retries = 15

        for s in range(60):
            await asyncio.sleep(1.0)
            await remove_cookie_banner(page)
            cur_url = page.url

            # Redirected to onboarding, profile, home, or left set-password
            if any(path in cur_url for path in ["/onboarding", "/profile", "/home"]) or ("set-password" not in cur_url and "/hub/" not in cur_url):
                is_registered = True
                logger.info(f"[+] Account creation confirmed at second {s+1}! URL: {cur_url}")
                break

            # Or onboarding name field appeared
            try:
                name_input = page.locator("input#profileName, input[placeholder*='اسم الملف'], input[placeholder*='الاسم']").first
                if await name_input.is_visible():
                    is_registered = True
                    logger.info(f"[+] Profile onboarding input visible at second {s+1}!")
                    break
            except Exception:
                pass

            # Check for network pressure / warning banner on password step
            body_text = ""
            try:
                body_text = await page.locator("body").inner_text()
            except Exception:
                pass

            has_net_warn = any(w in body_text for w in ["تقييد الوصول", "نشاط غير معتاد", "مشكلة في الشبكة", "خطأ في الشبكة"])
            if (has_net_warn or s % 4 == 0) and pwd_retry_count < max_pwd_retries:
                pwd_retry_count += 1
                logger.info(f"[*] Account creation network lag detected. Waiting 3 seconds before retry ({pwd_retry_count}/{max_pwd_retries})...")
                await asyncio.sleep(3.0)
                try:
                    btn = page.locator("form button[type='submit'], button:has-text('إنشاء حساب'), button:has-text('متابعة')").first
                    if await btn.is_visible() and not await btn.is_disabled():
                        logger.info(f"[*] 'إنشاء حساب' button is white/enabled. Waiting 3 seconds before clicking ({pwd_retry_count}/{max_pwd_retries})...")
                        await asyncio.sleep(3.0)
                        await btn.click()
                except Exception:
                    pass

        if not is_registered:
            body = await page.locator("body").inner_text()
            error_line = "لم يتم تأكيد الحساب بعد أو هناك خطأ بكلمة المرور."
            for line in body.split("\n"):
                clean = line.strip()
                if "يجب أن تتكون من 8" in clean:
                    continue  # Skip static requirement text
                if any(w in clean for w in ["خطأ", "غير صحيح", "مطلوب", "تعذر", "فشل"]):
                    error_line = clean
                    break
            await safe_edit_message(
                status_msg,
                f"⚠️ تنبيه: لم يكتمل إنشاء الحساب بعد.\nالسبب: {error_line}\n\nيرجى المحاولة مجدداً.",
                reply_markup=get_main_inline_keyboard(),
            )
            return ConversationHandler.END

        user_id = update.effective_user.id
        user_settings = get_user_settings(user_id)
        should_create_profiles = user_settings.get("create_profiles_enabled", True) and not getattr(session, "skip_profiles", False)

        if not should_create_profiles:
            # STOP HERE! User chose NOT to create/name profiles!
            profiles_list = ["(تم تخطي الملفات بناءً على اختيارك)"]
            proxy_used = session.proxy_config.get("host", "") if getattr(session, "proxy_config", None) else ""
            save_account(user_id, session.email, session.password, profiles_list, proxy=proxy_used)
            pop_user_queued_email(user_id, session.email)

            batch_info = context.user_data.get("batch_info")
            if batch_info:
                batch_info["success_count"] += 1
                batch_info["current_index"] += 1
                curr_done = batch_info["current_index"]
                tot = batch_info["total"]

                if curr_done < tot:
                    next_email = batch_info["emails"][curr_done]
                    await safe_edit_message(
                        status_msg,
                        f"✅ **(تم إنجاز الحساب {curr_done} من {tot} بنجاح!)**\n\n"
                        f"📧 البريد: `{session.email}`\n"
                        "👤 الملفات: *(تم التخطي)*\n"
                        "🗑️ تم حذف الإيميل تلقائياً من قائمتك لعدم التكرار.\n"
                        "──────────────────────────\n"
                        f"🚀 **جاري الانتقال تلقائياً للحساب التالي ({curr_done + 1} من {tot})...**\n"
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

            await safe_edit_message(
                status_msg,
                "🎉 **تهانينا! تم إنشاء وتأكيد الحساب بنجاح في Shahid!**\n\n"
                f"📧 البريد: `{session.email}`\n"
                f"🔑 كلمة المرور: `{session.password}`\n"
                "👤 الملفات: *(تم التوقف فور تأكيد الحساب دون إعداد ملفات شخصية)*\n\n"
                "💾 *تم حفظ الحساب تلقائياً في قائمة حساباتك!*\n"
                "✨ الحساب جاهز الآن للاستخدام!",
                reply_markup=success_markup,
                parse_mode="Markdown"
            )
            await cleanup_session(context, force=True)
            return ConversationHandler.END

        # Account created successfully! Now automate setting up all 4 profiles
        base = getattr(session, "base_name", "dahoom") or "dahoom"
        custom_names = getattr(session, "custom_names", None)

        if custom_names and len(custom_names) == 4:
            names_display = ", ".join(f"`{n}`" for n in custom_names)
            status_text = f"⏳ جاري الآن تجهيز كافة الملفات الشخصية بأسمائك المخصصة ({names_display})...\nيرجى الانتظار لحظات..."
            profiles_list = list(custom_names)
        else:
            names_display = f"`{base}1` إلى `{base}4`"
            status_text = f"⏳ جاري الآن تجهيز كافة الملفات الشخصية تلقائياً ({names_display})...\nيرجى الانتظار لحظات..."
            profiles_list = [f"{base}1", f"{base}2", f"{base}3", f"{base}4"]

        await safe_edit_message(
            status_msg,
            "🎉 **تم تأكيد الحساب بنجاح!**\n\n" + status_text,
            parse_mode="Markdown"
        )

        # Run profile automation sequence
        created_profiles = await automate_all_profiles(page, status_msg, base_name=base, custom_names=custom_names)
        if created_profiles and len(created_profiles) == 4:
            profiles_list = created_profiles

        # Save account locally
        user_id = update.effective_user.id
        proxy_used = session.proxy_config.get("host", "") if getattr(session, "proxy_config", None) else ""
        save_account(user_id, session.email, session.password, profiles_list, proxy=proxy_used)

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
                p_batch = ", ".join(f"`{n}`" for n in profiles_list)
                await safe_edit_message(
                    status_msg,
                    f"✅ **(تم إنجاز الحساب {curr_done} من {tot} بنجاح!)**\n\n"
                    f"📧 البريد: `{session.email}`\n"
                    f"👤 الملفات: {p_batch}\n"
                    "🗑️ تم حذف الإيميل تلقائياً من قائمتك لعدم التكرار.\n"
                    "──────────────────────────\n"
                    f"🚀 **جاري الانتقال تلقائياً للحساب التالي ({curr_done + 1} من {tot})...**\n"
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
        p_lines = "\n".join(f"{i+1}️⃣ `{name}`" for i, name in enumerate(profiles_list))
        await safe_edit_message(
            status_msg,
            "🎉 **تهانينا! تم إنشاء وتجهيز الحساب بالكامل في Shahid!**\n\n"
            f"📧 البريد: `{session.email}`\n"
            f"🔑 كلمة المرور: `{session.password}`\n\n"
            "👤 **الملفات الشخصية المجهزة:**\n"
            f"{p_lines}\n\n"
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
                await safe_edit_message(
                    status_msg,
                    f"⚠️ حدث تنبيه في الحساب ({curr_idx + 1} من {tot}): {exc}\n\n"
                    "⏭️ جاري الانتقال تلقائياً إلى الإيميل التالي في القائمة...",
                    parse_mode="Markdown"
                )
                await cleanup_session(context, force=True)
                await asyncio.sleep(2.0)
                return await start_batch_next_account(update.effective_message, context)
            else:
                return await finish_batch(update.effective_message, context)

        logger.error(f"[!] Error in code_step: {exc}", exc_info=True)
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
            proxy_line = f"🌐 البروكسي: `{acc['proxy']}`\n" if acc.get("proxy") else ""
            text += (
                f"**{idx}️⃣ الحساب #{idx}:**\n"
                f"📧 البريد: `{acc['email']}`\n"
                f"🔑 كلمة المرور: `{acc['password']}`\n"
                f"👤 الملفات: `{profiles_str}`\n"
                f"{proxy_line}"
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
        if acc.get("proxy"):
            buf.write(f"    Proxy: {acc['proxy']}\n")
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
    user_id = update.effective_user.id if update.effective_user else 0

    batch_info = context.user_data.get("batch_info")
    if batch_info:
        succ = batch_info.get("success_count", 0)
        tot = batch_info.get("total", 0)
        rem = len(get_user_queued_emails(user_id)) if user_id else 0
        msg_text = (
            "🚫 **تم إيقاف عملية الإنشاء التلقائي وإلغاؤها بنجاح.**\n\n"
            f"• الحسابات التي تم إنجازها بنجاح: `{succ}` من `{tot}`\n"
            f"• تم حفظ الحسابات الناجحة وحذفها من القائمة تلقائياً.\n"
            f"• الإيميلات المتبقية في قائمتك: `{rem}` إيميل محفوظة بأمان."
        )
    else:
        msg_text = "🚫 **تم إلغاء العملية والعودة للقائمة الرئيسية.**\n\nاختر من الأزرار أدناه للبدء من جديد:"

    context.user_data.pop("batch_info", None)
    context.user_data.pop("pending_proxies", None)

    if update.callback_query:
        try:
            await update.callback_query.answer("تم إلغاء العملية")
        except Exception:
            pass
        try:
            await update.callback_query.message.edit_text(
                msg_text,
                reply_markup=get_main_inline_keyboard(user_id),
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        except Exception:
            pass

    if update.effective_message:
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


async def toggle_create_profiles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    new_state = toggle_user_create_profiles(user_id)
    if new_state:
        alert_text = "🟢 تم تفعيل ميزة تجهيز الملفات!\nالآن سيقوم البوت بإعداد وتسمية الملفات الـ 4 كاملة تلقائياً."
    else:
        alert_text = "🔴 تم إيقاف ميزة تجهيز الملفات!\nالآن سيتوقف البوت فور اكتمال إنشاء الحساب دون إعداد أي ملفات شخصية."
    try:
        await query.answer(alert_text, show_alert=True)
    except Exception:
        pass
    try:
        await query.message.edit_reply_markup(reply_markup=get_main_inline_keyboard(user_id))
    except Exception:
        pass


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


async def show_proxies_panel(user_id: int, send_or_edit_fn, is_edit: bool = False) -> None:
    user_info = get_user_proxy_info(user_id)
    global_info = get_global_proxy_info()

    user_proxies = user_info.get("proxies", [])
    global_proxies = global_info.get("proxies", [])
    u_count = len(user_proxies)
    g_count = len(global_proxies)

    mode = user_info.get("proxy_mode", "auto")
    rotation_enabled = user_info.get("rotation_enabled", True)

    mode_labels = {
        "auto": "🔄 تلقائي (خاص أولاً، ثم عام مشترك)",
        "private": "🔒 خاص بي فقط (لا تستخدم البروكسيات العامة)",
        "global": "🌐 عام مشترك للجميع فقط",
    }
    mode_text = mode_labels.get(mode, "🔄 تلقائي")
    rot_status = "🟢 مفعّل (بروكسي جديد مع كل حساب)" if rotation_enabled else "🔴 معطّل (استخدام أول بروكسي)"

    next_proxy = get_next_proxy_for_user(user_id)
    if next_proxy:
        p_t = next_proxy.get("proxy_type", "private")
        t_label = "خاص بي" if p_t == "private" else ("عام مشترك" if p_t == "global" else "افتراضي")
        next_disp = f"`{next_proxy['host']}` ({t_label})"
    else:
        next_disp = "*لا يوجد (اتصال مباشر)*"

    text = (
        "🌐 **إدارة البروكسيات ونطاق الاستخدام (Proxy Manager):**\n\n"
        f"• 🔒 **بروكسياتي الخاصة (لي فقط):** `{u_count}` بروكسي\n"
        f"• 🌐 **البروكسيات العامة (المشتركة لكل المستخدمين):** `{g_count}` بروكسي\n"
        f"• 🎛️ **وضع الاستخدام الحالي:** {mode_text}\n"
        f"• 🔄 **نظام التدوير التلقائي:** {rot_status}\n"
        f"• 🎯 **البروكسي التالي بالدور:** {next_disp}\n\n"
    )

    if user_proxies:
        preview_count = min(u_count, 5)
        p_lines = [f"• `{p.get('host')}`" for p in user_proxies[:preview_count]]
        if u_count > 5:
            p_lines.append(f"*... والمزيد ({u_count - 5} بروكسي خاص)*")
        text += "📋 **معاينة بروكسياتك الخاصة:**\n" + "\n".join(p_lines) + "\n\n"

    if global_proxies:
        preview_count = min(g_count, 5)
        g_lines = [f"• `{p.get('host')}`" for p in global_proxies[:preview_count]]
        if g_count > 5:
            g_lines.append(f"*... والمزيد ({g_count - 5} بروكسي عام)*")
        text += "📋 **معاينة البروكسيات العامة المشتركة:**\n" + "\n".join(g_lines) + "\n\n"

    text += (
        "💡 **أنماط الاستخدام المدعومة:**\n"
        "• **🔄 تلقائي (مدمج):** استخدام بروكسياتك الخاصة أولاً، وإن لم توجد يُستخدم البروكسي العام.\n"
        "• **🔒 خاص بي فقط:** إنشاء الحسابات ببروكسياتك الخاصة حصراً دون مشاركة أي بروكسي عام.\n"
        "• **🌐 عام مشترك:** استخدام البروكسيات العامة المتاحة لكافة مستخدمي البوت."
    )

    user_is_adm = is_admin(user_id)
    rot_btn_text = "🔄 التدوير: 🟢 مفعّل" if rotation_enabled else "🔄 التدوير: 🔴 معطّل"

    mode_btn_labels = {
        "auto": "🎛️ الوضع: 🔄 تلقائي",
        "private": "🎛️ الوضع: 🔒 خاص بي فقط",
        "global": "🎛️ الوضع: 🌐 عام مشترك",
    }
    mode_btn_text = mode_btn_labels.get(mode, "🎛️ تبديل الوضع")

    top_row = [
        InlineKeyboardButton("➕ إضافة بروكسيات خاصة بي", callback_data="act_prompt_add_proxies"),
    ]
    if user_is_adm:
        top_row.append(InlineKeyboardButton("🌐 إضافة بروكسيات عامة", callback_data="act_prompt_add_global_proxies"))

    buttons = [
        top_row,
        [
            InlineKeyboardButton(mode_btn_text, callback_data="act_cycle_proxy_mode"),
            InlineKeyboardButton(rot_btn_text, callback_data="act_toggle_proxy_rotation"),
        ]
    ]

    clear_row = []
    if user_proxies:
        clear_row.append(InlineKeyboardButton("🗑️ مسح بروكسياتي الخاصة", callback_data="act_confirm_clear_proxies"))
    if user_is_adm and global_proxies:
        clear_row.append(InlineKeyboardButton("🗑️ مسح البروكسيات العامة", callback_data="act_confirm_clear_global_proxies"))

    if clear_row:
        buttons.append(clear_row)

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


async def proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_proxies_panel(update.effective_user.id, update.effective_message.reply_text, is_edit=False)


async def proxies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("pending_proxies", None)
    context.user_data.pop("proxy_target_scope", None)
    await show_proxies_panel(update.effective_user.id, query.message.edit_text, is_edit=True)


async def cycle_proxy_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    new_mode = cycle_user_proxy_mode(user_id)
    await show_proxies_panel(user_id, query.message.edit_text, is_edit=True)


async def toggle_proxy_rotation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    toggle_user_proxy_rotation(user_id)
    await show_proxies_panel(user_id, query.message.edit_text, is_edit=True)


async def confirm_clear_proxies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ نعم، افرغ بروكسياتي الخاصة", callback_data="act_do_clear_proxies"),
            InlineKeyboardButton("❌ تراجع", callback_data="act_proxies"),
        ]
    ])
    await query.message.edit_text(
        "⚠️ **هل أنت متأكد من تفريغ ومسح بروكسياتك الخاصة؟**\n\n"
        "لا يمكن التراجع عن هذه الخطوة بعد التأكيد.",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def do_clear_proxies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    clear_user_proxies(user_id)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع لإدارة البروكسيات", callback_data="act_proxies")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu")],
    ])
    await query.message.edit_text(
        "✅ **تم تفريغ ومسح قائمة بروكسياتك الخاصة بنجاح.**",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def confirm_clear_global_proxies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("⛔ هذا الخيار مخصص للمشرفين فقط.", show_alert=True)
        return
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ نعم، افرغ البروكسيات العامة للجميع", callback_data="act_do_clear_global_proxies"),
            InlineKeyboardButton("❌ تراجع", callback_data="act_proxies"),
        ]
    ])
    await query.message.edit_text(
        "⚠️ **هل أنت متأكد من رغبتك في تفريغ ومسح البروكسيات العامة المشتركة لجميع المستخدمين؟**\n\n"
        "لا يمكن التراجع عن هذه الخطوة بعد التأكيد.",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def do_clear_global_proxies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("⛔ هذا الخيار مخصص للمشرفين فقط.", show_alert=True)
        return
    clear_global_proxies()
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع لإدارة البروكسيات", callback_data="act_proxies")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu")],
    ])
    await query.message.edit_text(
        "✅ **تم تفريغ ومسح البروكسيات العامة المشتركة بنجاح.**",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def prompt_add_proxies_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    target_scope = "user"
    if update.callback_query and "global" in (update.callback_query.data or ""):
        if is_admin(user_id):
            target_scope = "global"

    context.user_data["proxy_target_scope"] = target_scope
    is_global = (target_scope == "global")
    current_count = len(get_global_proxies()) if is_global else len(get_user_proxies(user_id))
    scope_title = "🌐 العامة (المشتركة لكل مستخدمي البوت)" if is_global else "🔒 الخاصة بك (لك وحدك)"

    prompt = (
        f"📥 **أرسل الآن قائمة البروكسيات الجديدة ({scope_title}):**\n\n"
        f"• **البروكسيات المسجلة حالياً:** `{current_count}` بروكسي\n\n"
        "📌 **طرق الإرسال المدعومة:**\n"
        "1️⃣ أرسل ملف نصي `.txt` يحتوي على قائمة البروكسيات (مثل ملف Webshare).\n"
        "2️⃣ أو أرسل البروكسيات في رسالة نصية مباشرة (كل بروكسي في سطر).\n\n"
        "💡 **الصيغ المدعومة:**\n"
        "• `IP:PORT:USERNAME:PASSWORD` (مثل: `154.73.250.236:6137:user:pass`)\n"
        "• `IP:PORT` (بدون مصادقة)\n"
        "• `http://user:pass@ip:port`\n\n"
        "للتراجع اضغط الزر أدناه:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء", callback_data="act_proxies")]
    ])
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.edit_text(prompt, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await update.callback_query.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
    return INPUT_PROXIES


async def save_proxies_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    target_scope = context.user_data.get("proxy_target_scope", "user")
    is_global = (target_scope == "global")
    target_key = "global" if is_global else str(user_id)
    scope_title = "العامة المشتركة" if is_global else "الخاصة بك"

    raw_text = ""
    if update.message.document:
        doc = update.message.document
        doc_name = (doc.file_name or "").lower()
        if doc_name.endswith((".txt", ".csv", ".log", ".json")):
            file = await context.bot.get_file(doc.file_id)
            content = await file.download_as_bytearray()
            raw_text = content.decode("utf-8", errors="ignore")
        else:
            await update.message.reply_text(
                "⚠️ يرجى إرسال ملف نصي بصيغة `.txt` أو إرسال البروكسيات كنص عادي:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ إلغاء", callback_data="act_proxies")]
                ]),
            )
            return INPUT_PROXIES
    elif update.message.text:
        raw_text = update.message.text

    parsed = parse_proxies_text(raw_text)
    if not parsed:
        await update.message.reply_text(
            "⚠️ لم يتم العثور على أي بروكسي صالح في رسالتك!\n\n"
            "يرجى التأكد من الصيغة الصحيحة مثل:\n"
            "`154.73.250.236:6137:username:password`\n\n"
            "أعد المحاولة الآن أو اضغط إلغاء:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="act_proxies")]
            ]),
        )
        return INPUT_PROXIES

    existing = get_global_proxies() if is_global else get_user_proxies(user_id)
    if existing:
        context.user_data["pending_proxies"] = parsed
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 استبدال القائمة بالكامل (مسح القديم)", callback_data="act_proxy_replace"),
            ],
            [
                InlineKeyboardButton("➕ إبقاء القديم والدمج (إضافة للجديد)", callback_data="act_proxy_append"),
            ],
            [
                InlineKeyboardButton("❌ إلغاء العملية", callback_data="act_proxies"),
            ]
        ])
        await update.message.reply_text(
            f"📥 **تم التعرف على `{len(parsed)}` بروكسي صالح في القائمة ({scope_title})!**\n\n"
            f"⚠️ **لديك بالفعل `{len(existing)}` بروكسي مسجل في هذه القائمة.**\n\n"
            "👇 **كيف ترغب في حفظ البروكسيات الجديدة؟**\n"
            "• **استبدال:** مسح البروكسيات القديمة وبدء قائمة جديدة.\n"
            "• **إبقاء ودمج:** إضافة البروكسيات الجديدة وتخطي المكرر.",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return CONFIRM_PROXY_MODE
    else:
        added, dup, total = add_proxies_to_target(target_key, parsed, replace=True)
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 عرض قائمة البروكسيات", callback_data="act_proxies")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu")],
        ])
        await update.message.reply_text(
            f"✅ **تم حفظ `{total}` بروكسي بنجاح في القائمة ({scope_title})!**\n\n"
            "🔄 **التدوير التلقائي مفعّل:** في كل عملية إنشاء حساب، سيتم استخدام بروكسي جديد مختلف بالترتيب.",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        context.user_data.pop("proxy_target_scope", None)
        return ConversationHandler.END


async def proxy_replace_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    target_scope = context.user_data.get("proxy_target_scope", "user")
    is_global = (target_scope == "global")
    target_key = "global" if is_global else str(user_id)
    scope_title = "العامة المشتركة" if is_global else "الخاصة بك"

    pending = context.user_data.pop("pending_proxies", [])
    if not pending:
        await query.message.edit_text("⚠️ لا توجد بروكسيات معلقة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="act_proxies")]]))
        return ConversationHandler.END

    added, dup, total = add_proxies_to_target(target_key, pending, replace=True)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 عرض قائمة البروكسيات", callback_data="act_proxies")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu")],
    ])
    await query.message.edit_text(
        f"✅ **تم استبدال البروكسيات السابقة في القائمة ({scope_title}) بنجاح!**\n\n"
        f"• تم مسح البروكسيات القديمة بالكامل.\n"
        f"• إجمالي البروكسيات الجديدة المسجلة: `{total}` بروكسي\n"
        "🔄 تم ضبط مؤشر التدوير للبدء من أول بروكسي جديد.",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    context.user_data.pop("proxy_target_scope", None)
    return ConversationHandler.END


async def proxy_append_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    target_scope = context.user_data.get("proxy_target_scope", "user")
    is_global = (target_scope == "global")
    target_key = "global" if is_global else str(user_id)
    scope_title = "العامة المشتركة" if is_global else "الخاصة بك"

    pending = context.user_data.pop("pending_proxies", [])
    if not pending:
        await query.message.edit_text("⚠️ لا توجد بروكسيات معلقة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="act_proxies")]]))
        return ConversationHandler.END

    added, dup, total = add_proxies_to_target(target_key, pending, replace=False)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 عرض قائمة البروكسيات", callback_data="act_proxies")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="act_menu")],
    ])
    await query.message.edit_text(
        f"✅ **تم دمج البروكسيات الجديدة في القائمة ({scope_title}) بنجاح!**\n\n"
        f"• البروكسيات المضافة حديثاً: `{added}`\n"
        f"• البروكسيات المكررة (تم تخطيها): `{dup}`\n"
        f"• إجمالي البروكسيات المتاحة الآن: `{total}` بروكسي",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    context.user_data.pop("proxy_target_scope", None)
    return ConversationHandler.END



async def admin_backup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer("⛔ هذا الخيار مخصص للمشرفين فقط.", show_alert=True)
        else:
            await update.effective_message.reply_text("⛔ هذا الخيار مخصص للمشرفين فقط.")
        return

    if update.callback_query:
        await update.callback_query.answer("⏳ جاري تجهيز النسخة الاحتياطية الشاملة...")

    status_msg = await update.effective_message.reply_text("⏳ جاري إنشاء وضغط النسخة الاحتياطية الشاملة لكافة البيانات...")

    import io, tarfile, json
    from datetime import datetime

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    files_to_backup = [
        "accounts.json",
        "proxies.json",
        "admins.json",
        "subscribers.json",
        "settings.json",
        "queued_emails.json",
        ".env",
    ]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    bio = io.BytesIO()
    stats = {}

    with tarfile.open(fileobj=bio, mode="w:gz") as tar:
        for fname in files_to_backup:
            fpath = os.path.join(base_dir, fname)
            if os.path.exists(fpath):
                tar.add(fpath, arcname=fname)
                try:
                    if fname.endswith(".json"):
                        with open(fpath, "r", encoding="utf-8") as f:
                            d = json.load(f)
                            if fname == "accounts.json" and isinstance(d, dict):
                                tot = sum(len(v) for v in d.values() if isinstance(v, list))
                                stats[fname] = f"{tot} حساب ({len(d)} مستخدم)"
                            elif fname == "proxies.json" and isinstance(d, dict):
                                tot = sum(len(v) for v in d.values() if isinstance(v, list))
                                glob = len(d.get("global", []))
                                stats[fname] = f"{tot} بروكسي (منها {glob} عامة)"
                            elif isinstance(d, (list, dict)):
                                stats[fname] = f"{len(d)} عنصر"
                            else:
                                stats[fname] = "1"
                    else:
                        stats[fname] = "محفوظ بنجاح"
                except Exception:
                    stats[fname] = "تم نسخه"
            else:
                stats[fname] = "غير موجود"

    bio.seek(0)
    bio.name = f"backup_{now_str}.tar.gz"

    caption = (
        "📦 **النسخة الاحتياطية الشاملة لكافة بيانات البوت**\n\n"
        f"📅 **التاريخ:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
        f"• الحسابات (accounts.json): {stats.get('accounts.json', '0')}\n"
        f"• البروكسيات (proxies.json): {stats.get('proxies.json', '0')}\n"
        f"• المشرفين (admins.json): {stats.get('admins.json', '0')}\n"
        f"• المشتركين (subscribers.json): {stats.get('subscribers.json', '0')}\n"
        f"• الإعدادات (settings.json): {stats.get('settings.json', '0')}\n"
        f"• قائمة الإيميلات (queued_emails): {stats.get('queued_emails.json', '0')}\n"
        f"• ملف البيئة (.env): {stats.get('.env', 'غير موجود')}\n\n"
        "💡 *يمكنك استعادة هذه النسخة في أي وقت من السيرفر عبر خيار 10 أو عبر إرسال هذا الملف للبوت مع تعليق /restore*"
    )

    await update.effective_message.reply_document(
        document=bio,
        caption=caption,
        parse_mode="Markdown"
    )
    try:
        await status_msg.delete()
    except Exception:
        pass


async def process_restore_document(update: Update, context: ContextTypes.DEFAULT_TYPE, doc) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    status_msg = await update.effective_message.reply_text("⏳ جاري تحميل وفحص النسخة الاحتياطية الشاملة...")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tmp_path = os.path.join(base_dir, f"tmp_restore_{user_id}.tar.gz")

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(tmp_path)

        import tarfile, zipfile
        if tarfile.is_tarfile(tmp_path):
            with tarfile.open(tmp_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if not member.name.startswith("/") and ".." not in member.name:
                        tar.extract(member, path=base_dir)
        elif zipfile.is_zipfile(tmp_path):
            with zipfile.ZipFile(tmp_path, "r") as zf:
                for member in zf.namelist():
                    if not member.startswith("/") and ".." not in member:
                        zf.extract(member, path=base_dir)
        else:
            raise ValueError("صيغة الملف غير مدعومة (يجب أن يكون .tar.gz أو .zip)")

        # Defensive JSON checks
        for fn in ["accounts.json", "proxies.json", "queued_emails.json", "subscribers.json", "settings.json"]:
            fp = os.path.join(base_dir, fn)
            if os.path.exists(fp):
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        with open(fp, "w", encoding="utf-8") as f:
                            json.dump({}, f)
                except Exception:
                    with open(fp, "w", encoding="utf-8") as f:
                        json.dump({}, f)

        fp = os.path.join(base_dir, "admins.json")
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    with open(fp, "w", encoding="utf-8") as f:
                        json.dump([PRIMARY_ADMIN_ID], f)
            except Exception:
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump([PRIMARY_ADMIN_ID], f)

        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        await status_msg.edit_text(
            "✅ **تمت استعادة كافة البيانات والبروكسيات والمشتركين بنجاح!**\n\n"
            "💾 أصبحت كافة الحسابات والبروكسيات والإعدادات مفعلة الآن في البوت.",
            parse_mode="Markdown"
        )
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        await status_msg.edit_text(f"❌ حدث خطأ أثناء الاستعادة: `{exc}`", parse_mode="Markdown")


async def admin_restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.effective_message.reply_text("⛔ عذراً، هذا الخيار مخصص للمشرفين فقط.")
        return

    reply = update.message.reply_to_message if update.message else None
    if reply and reply.document:
        return await process_restore_document(update, context, reply.document)

    text = (
        "📥 **طريقة الاستيراد والاستعادة الشاملة من Telegram:**\n\n"
        "1️⃣ أرسل ملف النسخة الاحتياطية (`backup_*.tar.gz` أو `.zip`).\n"
        "2️⃣ أضف في خانة التعليق (Caption) الأمر: `/restore`\n\n"
        "💡 *أو قم بالرد (Reply) على أي ملف نسخة احتياطية تم إرساله مسبقاً بالأمر `/restore` وسيتم استرجاع كافة البيانات فوراً.*"
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def admin_restore_doc_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    doc = update.message.document if update.message else None
    if not doc:
        return
    caption = (update.message.caption or "").strip()
    if "/restore" in caption or "/import" in caption:
        await process_restore_document(update, context, doc)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Error: TELEGRAM_BOT_TOKEN is missing. Set it in .env file.")

    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(32)
        .connection_pool_size(64)
        .pool_timeout(30.0)
        .build()
    )

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
            CommandHandler("proxies", proxies_command),
            CommandHandler("proxy", proxies_command),
            CommandHandler("addproxies", prompt_add_proxies_entry),
            MessageHandler(filters.Regex(r"^(\+|➕)?\s*إنشاء حساب"), start_registration_entry),
            CallbackQueryHandler(start_registration_entry, pattern="^act_create$"),
            CallbackQueryHandler(choose_queue_prompt, pattern="^act_start_queue_create$"),
            CallbackQueryHandler(handle_batch_count_choice, pattern="^act_choose_manual$"),
            CallbackQueryHandler(handle_batch_count_choice, pattern="^act_choose_queue$"),
            CallbackQueryHandler(prompt_add_emails_entry, pattern="^act_prompt_add_emails$"),
            CallbackQueryHandler(prompt_add_proxies_entry, pattern="^(act_prompt_add_proxies|act_prompt_add_global_proxies)$"),
            CallbackQueryHandler(proxies_callback, pattern="^act_proxies$"),
            CallbackQueryHandler(edit_perm_pass_entry, pattern="^act_edit_perm_pass$"),
            CallbackQueryHandler(edit_perm_name_entry, pattern="^act_edit_perm_name$"),
            CallbackQueryHandler(edit_admin_contact_entry, pattern="^act_edit_admin_contact$"),
        ],
        states={
            CHOOSE_BATCH_COUNT: [
                CallbackQueryHandler(choose_manual_callback, pattern="^act_choose_manual$"),
                CallbackQueryHandler(choose_queue_prompt, pattern="^act_choose_queue$"),
                CallbackQueryHandler(handle_batch_count_choice, pattern="^act_batch_"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_batch_count_choice),
            ],
            BATCH_PASSWORD: [
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, batch_password_step),
            ],
            INPUT_QUEUED_EMAILS: [
                CallbackQueryHandler(email_queue_callback, pattern="^act_email_queue$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, save_queued_emails_step),
            ],
            INPUT_PROXIES: [
                CallbackQueryHandler(proxies_callback, pattern="^act_proxies$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, save_proxies_step),
            ],
            CONFIRM_PROXY_MODE: [
                CallbackQueryHandler(proxy_replace_callback, pattern="^act_proxy_replace$"),
                CallbackQueryHandler(proxy_append_callback, pattern="^act_proxy_append$"),
                CallbackQueryHandler(proxies_callback, pattern="^act_proxies$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
            ],
            EMAIL: [
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, email_step),
            ],
            PASSWORD: [
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, password_step),
            ],
            BASE_NAME: [
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                CallbackQueryHandler(base_name_step, pattern="^(act_default_name|act_custom_4_names|act_skip_profiles)$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, base_name_step),
            ],
            CODE: [
                CallbackQueryHandler(resend_otp_callback, pattern="^act_resend_otp$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, code_step),
            ],
            SET_PERM_PASS: [
                CallbackQueryHandler(perm_pass_callback, pattern="^act_perm_pass$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_perm_pass_step),
            ],
            SET_PERM_NAME: [
                CallbackQueryHandler(perm_name_callback, pattern="^act_perm_name$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_perm_name_step),
            ],
            SET_ADMIN_CONTACT: [
                CallbackQueryHandler(admin_contact_callback, pattern="^act_admin_contact$"),
                CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
                MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_admin_contact_step),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start_command),
            MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel),
            CallbackQueryHandler(cancel, pattern="^act_cancel.*$"),
            CallbackQueryHandler(resend_otp_callback, pattern="^act_resend_otp$"),
            CallbackQueryHandler(email_queue_callback, pattern="^act_email_queue$"),
            CallbackQueryHandler(proxies_callback, pattern="^act_proxies$"),
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
    app.add_handler(CommandHandler("proxies", proxies_command))
    app.add_handler(CommandHandler("proxy", proxies_command))
    app.add_handler(CommandHandler("addproxies", prompt_add_proxies_entry))
    app.add_handler(CommandHandler("password", perm_pass_command))
    app.add_handler(CommandHandler("name", perm_name_command))
    app.add_handler(CommandHandler("profilename", perm_name_command))
    app.add_handler(CommandHandler("backup", admin_backup_handler))
    app.add_handler(CommandHandler("backups", admin_backup_handler))
    app.add_handler(CommandHandler("restore", admin_restore_command))
    app.add_handler(CallbackQueryHandler(admin_backup_handler, pattern="^act_admin_backup$"))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.CaptionRegex(r"/(?:restore|import)"), admin_restore_doc_message_handler))
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
    app.add_handler(MessageHandler(filters.Regex(r"^🌐\s*(إدارة\s*)?البروكسيات"), proxies_command))
    app.add_handler(MessageHandler(filters.Regex(r"^🔐\s*كلمة المرور الدائمة"), perm_pass_command))
    app.add_handler(MessageHandler(filters.Regex(r"^👤\s*اسم الملفات المحفوظ"), perm_name_command))
    app.add_handler(MessageHandler(filters.Regex(r"^👥\s*(المشتركين|إدارة المشتركين)"), subs_command))
    app.add_handler(MessageHandler(filters.Regex(r"^(📞|👤)?\s*حساب (التواصل|الدعم|الإدارة)"), admin_contact_command))
    app.add_handler(MessageHandler(filters.Regex(r"^⏳\s*اشتراكي"), my_sub_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^ℹ️\s*تعليمات"), help_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^(❌|إلغاء|الغاء|تراجع)"), cancel))
    app.add_handler(CallbackQueryHandler(my_accounts_callback, pattern="^act_my_accounts$"))
    app.add_handler(CallbackQueryHandler(email_queue_callback, pattern="^act_email_queue$"))
    app.add_handler(CallbackQueryHandler(confirm_clear_queue_callback, pattern="^act_confirm_clear_queue$"))
    app.add_handler(CallbackQueryHandler(do_clear_queue_callback, pattern="^act_do_clear_queue$"))
    app.add_handler(CallbackQueryHandler(proxies_callback, pattern="^act_proxies$"))
    app.add_handler(CallbackQueryHandler(prompt_add_proxies_entry, pattern="^act_prompt_add_proxies$"))
    app.add_handler(CallbackQueryHandler(cycle_proxy_mode_callback, pattern="^act_cycle_proxy_mode$"))
    app.add_handler(CallbackQueryHandler(toggle_proxy_rotation_callback, pattern="^act_toggle_proxy_rotation$"))
    app.add_handler(CallbackQueryHandler(confirm_clear_proxies_callback, pattern="^act_confirm_clear_proxies$"))
    app.add_handler(CallbackQueryHandler(do_clear_proxies_callback, pattern="^act_do_clear_proxies$"))
    app.add_handler(CallbackQueryHandler(confirm_clear_global_proxies_callback, pattern="^act_confirm_clear_global_proxies$"))
    app.add_handler(CallbackQueryHandler(do_clear_global_proxies_callback, pattern="^act_do_clear_global_proxies$"))
    app.add_handler(CallbackQueryHandler(proxy_replace_callback, pattern="^act_proxy_replace$"))
    app.add_handler(CallbackQueryHandler(proxy_append_callback, pattern="^act_proxy_append$"))
    app.add_handler(CallbackQueryHandler(perm_pass_callback, pattern="^act_perm_pass$"))
    app.add_handler(CallbackQueryHandler(toggle_perm_pass_callback, pattern="^act_toggle_perm_pass$"))
    app.add_handler(CallbackQueryHandler(edit_perm_pass_entry, pattern="^act_edit_perm_pass$"))
    app.add_handler(CallbackQueryHandler(perm_name_callback, pattern="^act_perm_name$"))
    app.add_handler(CallbackQueryHandler(toggle_perm_name_callback, pattern="^act_toggle_perm_name$"))
    app.add_handler(CallbackQueryHandler(edit_perm_name_entry, pattern="^act_edit_perm_name$"))
    app.add_handler(CallbackQueryHandler(toggle_create_profiles_callback, pattern="^act_toggle_create_profiles$"))
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
    app.add_handler(CallbackQueryHandler(resend_otp_callback, pattern="^act_resend_otp$"))
    app.add_handler(CallbackQueryHandler(cancel, pattern="^act_cancel.*$"))

    print("[+] Shahid VIP Account Creator Bot is now running...")
    app.run_polling()


if __name__ == "__main__":
    main()
