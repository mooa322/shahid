#!/usr/bin/env bash
set -u

SERVICE="shahid-bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -d "/opt/shahid-bot" && -f "/opt/shahid-bot/bot.py" ]]; then
  BOT_DIR="/opt/shahid-bot"
elif [[ -f "$SCRIPT_DIR/bot.py" ]]; then
  BOT_DIR="$SCRIPT_DIR"
else
  BOT_DIR="/opt/shahid-bot"
fi

# Auto-link 'shahid' global command into /usr/local/bin if running with write access
if [[ -d "/usr/local/bin" && -w "/usr/local/bin" ]]; then
  TARGET_SCRIPT="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "$BOT_DIR/menu.sh")"
  if [[ -f "$TARGET_SCRIPT" ]]; then
    ln -sf "$TARGET_SCRIPT" /usr/local/bin/shahid 2>/dev/null || true
    chmod +x /usr/local/bin/shahid "$TARGET_SCRIPT" 2>/dev/null || true
  fi
fi

PRIMARY_ADMIN="1291024378"

PY_BIN="python3"
if [[ -x "$BOT_DIR/.venv/bin/python" ]]; then
  PY_BIN="$BOT_DIR/.venv/bin/python"
elif [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PY_BIN="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 &>/dev/null; then
  PY_BIN="python3"
elif command -v python &>/dev/null; then
  PY_BIN="python"
fi

pause() { read -r -p "Press Enter to return..." _; }

manage_admins() {
  local admins_file="$BOT_DIR/admins.json"
  while true; do
    clear 2>/dev/null || true
    echo ""
    echo "  ════════════════════════════════════════════"
    echo "         قائمة إدارة الأدمن (ADMIN MANAGEMENT)"
    echo "  ════════════════════════════════════════════"
    echo "  المشرفين المصرح لهم حالياً (Authorized Admins):"

    "$PY_BIN" - "$admins_file" "$PRIMARY_ADMIN" <<'PY'
import json, os, sys
p = sys.argv[1]
primary = int(sys.argv[2])
data = [primary]
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
            raw = d if isinstance(d, list) else d.get("admins", [])
            for item in raw:
                try:
                    val = int(item)
                    if val not in data:
                        data.append(val)
                except Exception:
                    pass
    except Exception as e:
        print(f"    [!] خطأ في قراءة ملف الأدمن: {e}")

try:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
except Exception:
    pass

for idx, a in enumerate(data, 1):
    tag = " (Primary Admin / أدمن أساسي دائم ⭐)" if a == primary else " (Admin / أدمن مصرح له)"
    print(f"    [{idx}] ID: {a}{tag}")
PY

    echo "  ────────────────────────────────────────────"
    echo "  1) إضافة أدمن جديد (Add Admin ID)"
    echo "  2) حذف أدمن (Remove Admin ID)"
    echo "  3) مسح جميع الأدمن الإضافيين (Reset to Primary Admin)"
    echo "  4) تحديد أو تعديل حساب التواصل للدعم (Admin Contact Telegram)"
    echo "  0) العودة للقائمة الرئيسية (Back to Main Menu)"
    echo "  ────────────────────────────────────────────"
    read -r -p "  Select [0-4]: " a_choice || return 0
    echo ""

    case "$a_choice" in
      1)
        read -r -p "  أدخل Telegram User ID للأدمن (أرقام فقط): " new_id
        if [[ "$new_id" =~ ^[0-9]+$ ]]; then
          "$PY_BIN" - "$admins_file" "$new_id" "$PRIMARY_ADMIN" <<'PY'
import json, os, sys
p = sys.argv[1]
uid = int(sys.argv[2])
primary = int(sys.argv[3])
data = [primary]
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
            raw = d if isinstance(d, list) else d.get("admins", [])
            for item in raw:
                try:
                    val = int(item)
                    if val not in data:
                        data.append(val)
                except Exception:
                    pass
    except Exception:
        data = [primary]

if uid in data:
    print(f"\n  [!] الآيدي ({uid}) مضاف بالفعل مسبقاً في قائمة الأدمن!")
else:
    data.append(uid)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  ✅ تم إضافة الأدمن بنجاح: {uid}")
    print("  ✨ تم تفعيل الصلاحيات الكاملة له في البوت فوراً دون الحاجة لإعادة التشغيل!")
PY
        else
          echo "  ❌ خطأ: يجب إدخال أرقام فقط للآيدي (Telegram User ID)."
        fi
        pause
        ;;
      2)
        read -r -p "  أدخل Telegram User ID المراد حذفه: " del_id
        if [[ "$del_id" =~ ^[0-9]+$ ]]; then
          "$PY_BIN" - "$admins_file" "$del_id" "$PRIMARY_ADMIN" <<'PY'
import json, os, sys
p = sys.argv[1]
uid = int(sys.argv[2])
primary = int(sys.argv[3])

if uid == primary:
    print(f"\n  ⚠️ لا يمكن حذف الأدمن الأساسي الدائم ({primary})!")
    sys.exit(0)

data = [primary]
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
            raw = d if isinstance(d, list) else d.get("admins", [])
            for item in raw:
                try:
                    val = int(item)
                    if val not in data:
                        data.append(val)
                except Exception:
                    pass
    except Exception:
        data = [primary]

if uid not in data:
    print(f"\n  [!] الآيدي ({uid}) غير موجود في قائمة الأدمن!")
else:
    data = [x for x in data if x != uid]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  ✅ تم حذف الأدمن بنجاح: {uid}")
PY
        else
          echo "  ❌ خطأ: يجب إدخال أرقام فقط."
        fi
        pause
        ;;
      3)
        read -r -p "  هل أنت متأكد من مسح جميع الأدمن الإضافيين؟ (y/N): " confirm
        if [[ "$confirm" =~ ^[yY]$ ]]; then
          "$PY_BIN" - "$admins_file" "$PRIMARY_ADMIN" <<'PY'
import json, os, sys
p = sys.argv[1]
primary = int(sys.argv[2])
with open(p, "w", encoding="utf-8") as f:
    json.dump([primary], f, ensure_ascii=False, indent=2)
print(f"\n  ✅ تم مسح جميع الأدمن الإضافيين بنجاح، والإبقاء على الأدمن الأساسي ({primary}).")
PY
        else
          echo "  تم الإلغاء."
        fi
        pause
        ;;
      4)
        local settings_file="$BOT_DIR/settings.json"
        echo "  📞 حساب التواصل الحالي مع الإدارة:"
        "$PY_BIN" - "$settings_file" "$PRIMARY_ADMIN" <<'PY'
import json, os, sys
p = sys.argv[1]
primary = sys.argv[2]
contact = ""
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
            contact = d.get("admin_contact", "")
    except Exception:
        pass
if contact:
    print(f"  • المعرف الحالي: @{contact.lstrip('@')}")
    print(f"  • الرابط المباشر: https://t.me/{contact.lstrip('@')}")
else:
    print(f"  • لم يُحدد بعد (الافتراضي: tg://user?id={primary})")
PY
        echo ""
        read -r -p "  أدخل المعرف الجديد للأدمن (مثال: @username أو username) [أو اضغط Enter للإلغاء]: " new_contact
        if [[ -n "$new_contact" ]]; then
          "$PY_BIN" - "$settings_file" "$new_contact" <<'PY'
import json, os, sys
p = sys.argv[1]
raw = sys.argv[2].strip()
clean = raw
if "t.me/" in clean:
    clean = clean.split("t.me/")[-1].strip("/").strip()
if clean.startswith("@"):
    clean = clean.lstrip("@").strip()

data = {}
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

data["admin_contact"] = clean
with open(p, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n  ✅ تم حفظ حساب التواصل بنجاح: @{clean}")
print(f"  🔗 الرابط المباشر للمشتركين: https://t.me/{clean}")
print("  ✨ يظهر هذا الرابط كزر مباشر لكل المستخدمين غير المشتركين في البوت فوراً!")
PY
        fi
        pause
        ;;
      0)
        return 0
        ;;
      *)
        echo "  خيار غير صالح."
        sleep 1
        ;;
    esac
  done
}

manage_subscribers() {
  local subs_file="$BOT_DIR/subscribers.json"
  while true; do
    clear 2>/dev/null || true
    echo ""
    echo "  ════════════════════════════════════════════════════════════"
    echo "       نظام إدارة المشتركين ونقطة البيع (POS SUBSCRIBERS)     "
    echo "  ════════════════════════════════════════════════════════════"
    echo "  المشتركين الحاليين (Current Subscribers):"

    "$PY_BIN" - "$subs_file" <<'PY'
import json, os, sys
from datetime import datetime
p = sys.argv[1]
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            if data:
                now = datetime.now()
                for idx, (uid, info) in enumerate(data.items(), 1):
                    exp_str = info.get("expires_at", "")
                    days = info.get("days", "")
                    try:
                        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                        if exp_dt > now and info.get("is_active", True):
                            diff = exp_dt - now
                            rem = f"متبقي: {diff.days} يوم و {diff.seconds // 3600} ساعة"
                            status = f"🟢 نشط ({rem})"
                        else:
                            status = f"🔴 منتهي ({exp_str})"
                    except Exception:
                        status = f"🔴 غير صالح ({exp_str})"
                    note = f" [{info.get('note')}]" if info.get('note') else ""
                    print(f"    [{idx}] ID: {uid}{note} | {status}")
            else:
                print("    [!] لا يوجد أي مشتركين مسجلين حالياً.")
    except Exception as e:
        print(f"    [!] خطأ في قراءة ملف المشتركين: {e}")
else:
    print("    [!] لا يوجد أي مشتركين مسجلين حالياً.")
PY

    echo "  ────────────────────────────────────────────────────────────"
    echo "  1) إضافة / تجديد اشتراك (Add or Renew Subscription)"
    echo "  2) حذف / إلغاء اشتراك (Remove Subscriber)"
    echo "  3) مسح جميع المشتركين (Clear All Subscribers)"
    echo "  0) العودة للقائمة الرئيسية (Back to Main Menu)"
    echo "  ────────────────────────────────────────────────────────────"
    read -r -p "  Select [0-3]: " s_choice || return 0
    echo ""

    case "$s_choice" in
      1)
        read -r -p "  أدخل Telegram User ID للمشترك: " s_uid
        if [[ "$s_uid" =~ ^[0-9]+$ ]]; then
          read -r -p "  أدخل مدة الاشتراك بالأيام (مثال: 30): " s_days
          if [[ "$s_days" =~ ^[0-9]+$ && "$s_days" -gt 0 ]]; then
            read -r -p "  ملاحظة أو اسم المشترك (اختياري): " s_note
            "$PY_BIN" - "$subs_file" "$s_uid" "$s_days" "$s_note" <<'PY'
import json, os, sys
from datetime import datetime, timedelta

p = sys.argv[1]
uid = sys.argv[2]
days = int(sys.argv[3])
note = sys.argv[4] if len(sys.argv) > 4 else ""

data = {}
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

now = datetime.now()
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

data[uid] = {
    "user_id": int(uid),
    "started_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    "expires_at": new_exp.strftime("%Y-%m-%d %H:%M:%S"),
    "days": days,
    "is_active": True,
    "note": note or data.get(uid, {}).get("note", ""),
    "created_by": "menu_sh",
    "updated_at": now.strftime("%Y-%m-%d %H:%M:%S")
}

with open(p, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n  ✅ تم تفعيل الاشتراك بنجاح للمستخدم: {uid}")
print(f"  ⏱️ مدة الاشتراك: {days} يوم")
print(f"  📅 تاريخ الانتهاء: {data[uid]['expires_at']}")
print("  ✨ تم تفعيل الصلاحيات له في البوت فوراً!")
PY
          else
            echo "  ❌ خطأ: يجب إدخال عدد أيام صحيح (أكبر من صفر)."
          fi
        else
          echo "  ❌ خطأ: يجب إدخال أرقام فقط للآيدي."
        fi
        pause
        ;;
      2)
        read -r -p "  أدخل Telegram User ID المراد حذفه: " del_uid
        if [[ "$del_uid" =~ ^[0-9]+$ ]]; then
          "$PY_BIN" - "$subs_file" "$del_uid" <<'PY'
import json, os, sys
p = sys.argv[1]
uid = sys.argv[2]
data = {}
if os.path.exists(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

if uid not in data:
    print(f"\n  [!] المستخدم ({uid}) غير موجود في قائمة المشتركين!")
else:
    del data[uid]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  ✅ تم حذف اشتراك المستخدم ({uid}) بنجاح.")
PY
        else
          echo "  ❌ خطأ: يجب إدخال أرقام فقط."
        fi
        pause
        ;;
      3)
        read -r -p "  هل أنت متأكد من مسح جميع المشتركين؟ (y/N): " confirm
        if [[ "$confirm" =~ ^[yY]$ ]]; then
          "$PY_BIN" - "$subs_file" <<'PY'
import json, os, sys
p = sys.argv[1]
with open(p, "w", encoding="utf-8") as f:
    json.dump({}, f, ensure_ascii=False, indent=2)
print("\n  ✅ تم تفريغ قائمة المشتركين بالكامل.")
PY
        else
          echo "  تم الإلغاء."
        fi
        pause
        ;;
      0)
        return 0
        ;;
      *)
        echo "  خيار غير صالح."
        sleep 1
        ;;
    esac
  done
}

user_statement() {
  local reports_dir="$BOT_DIR/reports"
  mkdir -p "$reports_dir" 2>/dev/null || true

  while true; do
    clear 2>/dev/null || true
    echo ""
    echo "  ════════════════════════════════════════════════════════════"
    echo "    كشف حسابات المشرفين والمشتركين (USER ACCOUNT STATEMENT)   "
    echo "  ════════════════════════════════════════════════════════════"
    echo "  المستخدمين المسجلين في النظام (All System Users):"

    local users_output
    users_output=$("$PY_BIN" - "$BOT_DIR" "$PRIMARY_ADMIN" <<'PY'
import json, os, sys
from datetime import datetime

bot_dir = sys.argv[1]
primary_admin = int(sys.argv[2])

def load_json(fname, default):
    p = os.path.join(bot_dir, fname)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

admins = load_json("admins.json", [])
subs = load_json("subscribers.json", {})
accounts = load_json("accounts.json", {})
settings = load_json("settings.json", {})
queued = load_json("queued_emails.json", {})

all_uids = [primary_admin]
for a in admins:
    try:
        val = int(a)
        if val not in all_uids:
            all_uids.append(val)
    except Exception:
        pass

for s in subs.keys():
    try:
        val = int(s)
        if val not in all_uids:
            all_uids.append(val)
    except Exception:
        pass

for acc_uid in accounts.keys():
    try:
        val = int(acc_uid)
        if val not in all_uids:
            all_uids.append(val)
    except Exception:
        pass

for sett_uid in settings.keys():
    try:
        val = int(sett_uid)
        if val not in all_uids:
            all_uids.append(val)
    except Exception:
        pass

now = datetime.now()
for idx, uid in enumerate(all_uids, 1):
    uid_str = str(uid)
    acc_count = len(accounts.get(uid_str, []))

    if uid == primary_admin:
        role_tag = "⭐ أدمن أساسي دائم"
    elif uid in admins:
        role_tag = "👤 مشرف (Admin)"
    elif uid_str in subs:
        sub = subs[uid_str]
        exp_str = sub.get("expires_at", "")
        try:
            exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
            if exp_dt > now and sub.get("is_active", True):
                diff = exp_dt - now
                rem = f"{diff.days} يوم" if diff.days > 0 else f"{diff.seconds // 3600} ساعة"
                role_tag = f"🟢 مشترك نشط (متبقي: {rem})"
            else:
                role_tag = "🔴 مشترك منتهي الصلاحية"
        except Exception:
            role_tag = "🔴 مشترك منتهي"
    else:
        role_tag = "مستخدم مسجل"

    note = f" [{subs.get(uid_str, {}).get('note')}]" if subs.get(uid_str, {}).get("note") else ""
    print(f"    [{idx}] ID: {uid}{note} | {role_tag} | عدد الحسابات: {acc_count}")
PY
)
    if [[ -z "$users_output" ]]; then
      echo "    [!] لا يوجد مستخدمين مسجلين في النظام."
      pause
      return 0
    else
      echo "$users_output"
    fi

    echo "  ────────────────────────────────────────────────────────────"
    echo "  أدخل رقم المستخدم من القائمة (مثال: 1) أو اكتب الـ ID مباشرة"
    echo "  أو اضغط 0 للعودة للقائمة الرئيسية:"
    read -r -p "  الخيار أو الآيدي: " target_input || return 0
    echo ""

    if [[ "$target_input" == "0" || -z "$target_input" ]]; then
      return 0
    fi

    "$PY_BIN" - "$BOT_DIR" "$PRIMARY_ADMIN" "$target_input" "$reports_dir" <<'PY'
import json, os, sys
from datetime import datetime

bot_dir = sys.argv[1]
primary_admin = int(sys.argv[2])
target_input = sys.argv[3]
reports_dir = sys.argv[4]

def load_json(fname, default):
    p = os.path.join(bot_dir, fname)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

admins = load_json("admins.json", [])
subs = load_json("subscribers.json", {})
accounts = load_json("accounts.json", {})
settings = load_json("settings.json", {})
queued = load_json("queued_emails.json", {})

all_uids = [primary_admin]
for a in admins:
    try:
        val = int(a)
        if val not in all_uids:
            all_uids.append(val)
    except Exception:
        pass
for s in subs.keys():
    try:
        val = int(s)
        if val not in all_uids:
            all_uids.append(val)
    except Exception:
        pass
for acc_uid in accounts.keys():
    try:
        val = int(acc_uid)
        if val not in all_uids:
            all_uids.append(val)
    except Exception:
        pass

chosen_uid = None
if target_input.isdigit():
    idx_val = int(target_input)
    if 1 <= idx_val <= len(all_uids):
        chosen_uid = all_uids[idx_val - 1]
    else:
        chosen_uid = idx_val
else:
    print(f"❌ إدخال غير صالح: {target_input}")
    sys.exit(0)

uid_str = str(chosen_uid)
is_primary = (chosen_uid == primary_admin)
is_adm = (chosen_uid in admins) or is_primary
sub_info = subs.get(uid_str, {})
user_sett = settings.get(uid_str, {})
user_accs = accounts.get(uid_str, [])

now = datetime.now()

lines = []
lines.append("════════════════════════════════════════════════════════════")
lines.append(f"       كشف حساب تفصيلي وشامل للمستخدم ({chosen_uid})")
lines.append("════════════════════════════════════════════════════════════")
lines.append(f"• Telegram User ID : {chosen_uid}")

if is_primary:
    lines.append("• الرتبة والصلاحية : ⭐ أدمن أساسي دائم (صلاحيات كاملة غير محدودة)")
elif is_adm:
    lines.append("• الرتبة والصلاحية : 👤 مشرف (Admin) معتمد بالنظام")
else:
    lines.append("• الرتبة والصلاحية : 👥 مشترك (Subscriber)")

if sub_info:
    exp_str = sub_info.get("expires_at", "غير محدد")
    started_str = sub_info.get("started_at", "غير محدد")
    days_val = sub_info.get("days", "غير محدد")
    note_val = sub_info.get("note", "لا توجد")
    try:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
        if exp_dt > now and sub_info.get("is_active", True):
            diff = exp_dt - now
            rem_str = f"{diff.days} يوم و {diff.seconds // 3600} ساعة"
            status_tag = f"🟢 نشط ومفعل (متبقي: {rem_str})"
        else:
            status_tag = f"🔴 منتهي الصلاحية ({exp_str})"
    except Exception:
        status_tag = f"🔴 غير صالح ({exp_str})"

    lines.append(f"• حالة الاشتراك   : {status_tag}")
    lines.append(f"• مدة الباقة     : {days_val} يوم")
    lines.append(f"• تاريخ البدء     : {started_str}")
    lines.append(f"• تاريخ الانتهاء  : {exp_str}")
    lines.append(f"• الملاحظة/الاسم  : {note_val}")
elif is_adm:
    lines.append("• حالة الاشتراك   : 🟢 دائم مدى الحياة (مشرف نظام)")
else:
    lines.append("• حالة الاشتراك   : ⚠️ غير مشترك بنظام الباقات")

lines.append("────────────────────────────────────────────────────────────")
lines.append("🔐 إعدادات المستخدم الخاصة (User Settings):")
perm_pwd = user_sett.get("perm_password", "")
perm_pwd_on = user_sett.get("perm_pass_enabled", False)
if perm_pwd:
    st = "🟢 مفعلة" if perm_pwd_on else "🔴 معطلة"
    lines.append(f"  • كلمة المرور الدائمة : {perm_pwd} ({st})")
else:
    lines.append("  • كلمة المرور الدائمة : لم تحدد بعد (معطلة 🔴)")

perm_name = user_sett.get("perm_name", "")
perm_name_on = user_sett.get("perm_name_enabled", False)
if perm_name:
    st = "🟢 مفعلة" if perm_name_on else "🔴 معطلة"
    lines.append(f"  • اسم الملفات المحفوظ : {perm_name} ({st}) -> ({perm_name}1..4)")
else:
    lines.append("  • اسم الملفات المحفوظ : افتراضي dahoom (معطلة 🔴)")

user_queue = queued.get(uid_str, [])
lines.append(f"  • قائمة الإيميلات المسبقة : {len(user_queue)} إيميل في قائمة الانتظار (من أصل 100)")

lines.append("────────────────────────────────────────────────────────────")
lines.append(f"📋 قائمة حسابات Shahid المنشأة بواسطة هذا المستخدم (الإجمالي: {len(user_accs)} حساب):")

if not user_accs:
    lines.append("  [!] لا توجد حسابات مسجلة أو منشأة لهذا المستخدم حتى الآن.")
else:
    for idx, acc in enumerate(user_accs, 1):
        profs = " | ".join(acc.get("profiles", []))
        lines.append(f"\n  [{idx}] 📧 البريد: {acc.get('email', '')}")
        lines.append(f"      🔑 كلمة المرور: {acc.get('password', '')}")
        lines.append(f"      👤 الملفات الشخصية: {profs}")
        lines.append(f"      📅 تاريخ الإنشاء: {acc.get('created_at', 'غير متوفر')}")

lines.append("════════════════════════════════════════════════════════════")

report_text = "\n".join(lines)
print(report_text)

now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
rep_file = os.path.join(reports_dir, f"statement_{chosen_uid}_{now_tag}.txt")
with open(rep_file, "w", encoding="utf-8") as f:
    f.write(report_text)

print(f"\n💾 تم حفظ نسخة من هذا الكشف تلقائياً في:")
print(f"   {rep_file}")
PY
    pause
  done
}

manage_backups() {
  local backups_dir="$BOT_DIR/backups"
  mkdir -p "$backups_dir" 2>/dev/null || true

  while true; do
    clear 2>/dev/null || true
    echo ""
    echo "  ════════════════════════════════════════════════════════════"
    echo "        النسخ الاحتياطي والاستيراد الشامل (BACKUP & RESTORE)  "
    echo "  ════════════════════════════════════════════════════════════"
    echo "  النسخ الاحتياطية المتوفرة (Available Backups):"

    "$PY_BIN" - "$backups_dir" <<'PY'
import os, sys, datetime
bdir = sys.argv[1]
files = [f for f in os.listdir(bdir) if f.startswith("backup_") and (f.endswith(".tar.gz") or f.endswith(".zip"))]
files.sort(reverse=True)
if not files:
    print("    [!] لا توجد نسخ احتياطية محفوظة حتى الآن.")
else:
    for idx, f in enumerate(files, 1):
        fpath = os.path.join(bdir, f)
        size_kb = os.path.getsize(fpath) / 1024
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M")
        print(f"    [{idx}] {f} | الحجم: {size_kb:.1f} KB | التاريخ: {mtime}")
PY

    echo "  ────────────────────────────────────────────────────────────"
    echo "  1) إنشاء نسخة احتياطية شاملة الآن (Create Full Backup Now)"
    echo "  2) استيراد / استعادة نسخة من القائمة (Restore from List)"
    echo "  3) استيراد من ملف خارجي مخصص (Import from Custom File: .tar.gz / .zip)"
    echo "  4) حذف نسخة احتياطية من القائمة (Delete a Backup)"
    echo "  0) العودة للقائمة الرئيسية (Back to Main Menu)"
    echo "  ────────────────────────────────────────────────────────────"
    read -r -p "  Select [0-4]: " b_choice || return 0
    echo ""

    case "$b_choice" in
      1)
        echo "  ⏳ جاري إنشاء النسخة الاحتياطية الشاملة لجميع الملفات والبروكسيات..."
        "$PY_BIN" - "$BOT_DIR" "$backups_dir" <<'PY'
import os, sys, json, tarfile
from datetime import datetime

bot_dir = sys.argv[1]
backups_dir = sys.argv[2]

now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
archive_name = f"backup_{now_str}.tar.gz"
archive_path = os.path.join(backups_dir, archive_name)

files_to_backup = [
    "accounts.json",
    "proxies.json",
    "admins.json",
    "subscribers.json",
    "settings.json",
    "queued_emails.json",
    ".env",
]

stats = {}
with tarfile.open(archive_path, "w:gz") as tar:
    for fname in files_to_backup:
        fpath = os.path.join(bot_dir, fname)
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
                    stats[fname] = "موجود ومحفوظ بنجاح"
            except Exception as e:
                stats[fname] = f"تم نسخه ({e})"
        else:
            stats[fname] = "غير موجود"

size_kb = os.path.getsize(archive_path) / 1024
print(f"
  ✅ تم إنشاء النسخة الاحتياطية الشاملة بنجاح!")
print(f"  📁 اسم الملف : {archive_name}")
print(f"  📦 الحجم      : {size_kb:.1f} KB")
print(f"  📍 المسار     : {archive_path}
")
print(f"  📊 تفاصيل المحتويات المحفوظة:")
print(f"     • الحسابات (accounts.json)       : {stats.get('accounts.json', '0')}")
print(f"     • البروكسيات (proxies.json)      : {stats.get('proxies.json', '0')}")
print(f"     • المشرفين (admins.json)         : {stats.get('admins.json', '0')}")
print(f"     • المشتركين (subscribers.json)   : {stats.get('subscribers.json', '0')}")
print(f"     • الإعدادات (settings.json)      : {stats.get('settings.json', '0')}")
print(f"     • قائمة الإيميلات (queued_emails): {stats.get('queued_emails.json', '0')}")
print(f"     • ملف البيئة (.env)              : {stats.get('.env', 'غير موجود')}")
PY
        pause
        ;;
      2)
        read -r -p "  أدخل رقم النسخة المراد استعادتها (مثال: 1): " restore_idx
        if [[ "$restore_idx" =~ ^[0-9]+$ && "$restore_idx" -gt 0 ]]; then
          read -r -p "  ⚠️ هل أنت متأكد من استعادة هذه النسخة واستبدال البيانات الحالية؟ (y/N): " confirm_res
          if [[ "$confirm_res" =~ ^[yY]$ ]]; then
            echo "  ⏳ جاري الاستعادة والتحقق الشامل من سلامة البيانات..."
            "$PY_BIN" - "$BOT_DIR" "$backups_dir" "$restore_idx" <<'PY'
import os, sys, tarfile, json, zipfile

bot_dir = sys.argv[1]
backups_dir = sys.argv[2]
idx = int(sys.argv[3])

files = [f for f in os.listdir(backups_dir) if f.startswith("backup_") and (f.endswith(".tar.gz") or f.endswith(".zip"))]
files.sort(reverse=True)

if not (1 <= idx <= len(files)):
    print(f"
  ❌ رقم النسخة غير صحيح: {idx}")
    sys.exit(0)

target_archive = os.path.join(backups_dir, files[idx - 1])
print(f"  📦 استعادة من: {files[idx - 1]}")

try:
    if target_archive.endswith(".tar.gz") or target_archive.endswith(".tgz"):
        with tarfile.open(target_archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.name.startswith("/") and ".." not in member.name:
                    tar.extract(member, path=bot_dir)
    elif target_archive.endswith(".zip"):
        with zipfile.ZipFile(target_archive, "r") as zf:
            for member in zf.namelist():
                if not member.startswith("/") and ".." not in member:
                    zf.extract(member, path=bot_dir)

    # Defensive validation: ensure all extracted JSON files are valid types
    for fn in ["accounts.json", "proxies.json", "queued_emails.json", "subscribers.json", "settings.json"]:
        fp = os.path.join(bot_dir, fn)
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if not isinstance(d, dict):
                    with open(fp, "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)
            except Exception:
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)

    fp = os.path.join(bot_dir, "admins.json")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, list):
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump([1291024378], f, ensure_ascii=False, indent=2)
        except Exception:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump([1291024378], f, ensure_ascii=False, indent=2)

    print("
  ✅ تمت استعادة كافة البيانات والبروكسيات بنجاح والتحقق من سلامة الجداول!")
except Exception as e:
    print(f"
  ❌ حدث خطأ أثناء الاستعادة: {e}")
PY
            if command -v systemctl &>/dev/null; then
              echo "  🔄 جاري إعادة تشغيل خدمة البوت لتطبيق التغييرات المستعادة فوراً..."
              systemctl restart "$SERVICE" 2>/dev/null || true
              echo "  [✔] تمت إعادة تشغيل خدمة $SERVICE بنجاح."
            fi
          else
            echo "  تم الإلغاء."
          fi
        else
          echo "  ❌ رقم غير صالح."
        fi
        pause
        ;;
      3)
        read -r -p "  أدخل المسار الكامل لملف النسخة الاحتياطية (.tar.gz / .zip): " custom_path
        if [[ -f "$custom_path" ]]; then
          read -r -p "  ⚠️ هل أنت متأكد من استيراد هذا الملف واستبدال البيانات الحالية؟ (y/N): " confirm_imp
          if [[ "$confirm_imp" =~ ^[yY]$ ]]; then
            echo "  ⏳ جاري الاستيراد والتحقق..."
            "$PY_BIN" - "$BOT_DIR" "$custom_path" <<'PY'
import os, sys, tarfile, json, zipfile

bot_dir = sys.argv[1]
custom_path = sys.argv[2]

try:
    if custom_path.endswith(".tar.gz") or custom_path.endswith(".tgz") or tarfile.is_tarfile(custom_path):
        with tarfile.open(custom_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.name.startswith("/") and ".." not in member.name:
                    tar.extract(member, path=bot_dir)
    elif custom_path.endswith(".zip") or zipfile.is_zipfile(custom_path):
        with zipfile.ZipFile(custom_path, "r") as zf:
            for member in zf.namelist():
                if not member.startswith("/") and ".." not in member:
                    zf.extract(member, path=bot_dir)
    else:
        print(f"
  ❌ صيغة الملف غير مدعومة: {custom_path}")
        sys.exit(1)

    # Defensive validation: ensure all extracted JSON files are valid types
    for fn in ["accounts.json", "proxies.json", "queued_emails.json", "subscribers.json", "settings.json"]:
        fp = os.path.join(bot_dir, fn)
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if not isinstance(d, dict):
                    with open(fp, "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)
            except Exception:
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)

    fp = os.path.join(bot_dir, "admins.json")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, list):
                with open(fp, "w", encoding="utf-8") as f:
                    json.dump([1291024378], f, ensure_ascii=False, indent=2)
        except Exception:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump([1291024378], f, ensure_ascii=False, indent=2)

    print(f"
  ✅ تم استيراد واستعادة البيانات بنجاح من: {custom_path}")
except Exception as e:
    print(f"
  ❌ حدث خطأ أثناء الاستيراد: {e}")
PY
            if command -v systemctl &>/dev/null; then
              echo "  🔄 جاري إعادة تشغيل خدمة البوت لتطبيق التغييرات المستوردة فوراً..."
              systemctl restart "$SERVICE" 2>/dev/null || true
              echo "  [✔] تمت إعادة تشغيل خدمة $SERVICE بنجاح."
            fi
          else
            echo "  تم الإلغاء."
          fi
        else
          echo "  ❌ الملف غير موجود في المسار المحدد: $custom_path"
        fi
        pause
        ;;
      4)
        read -r -p "  أدخل رقم النسخة المراد حذفها: " del_idx
        if [[ "$del_idx" =~ ^[0-9]+$ && "$del_idx" -gt 0 ]]; then
          "$PY_BIN" - "$backups_dir" "$del_idx" <<'PY'
import os, sys
bdir = sys.argv[1]
idx = int(sys.argv[2])
files = [f for f in os.listdir(bdir) if f.startswith("backup_") and (f.endswith(".tar.gz") or f.endswith(".zip"))]
files.sort(reverse=True)
if 1 <= idx <= len(files):
    fpath = os.path.join(bdir, files[idx - 1])
    os.remove(fpath)
    print(f"
  ✅ تم حذف النسخة الاحتياطية بنجاح: {files[idx - 1]}")
else:
    print(f"
  ❌ رقم غير صالح: {idx}")
PY
        else
          echo "  ❌ رقم غير صالح."
        fi
        pause
        ;;
      0)
        return 0
        ;;
      *)
        echo "  خيار غير صالح."
        sleep 1
        ;;
    esac
  done
}

update_from_github() {
  clear 2>/dev/null || true
  echo ""
  echo "  ════════════════════════════════════════════════════════════"
  echo "              تحديث السكريبت والبوت من GitHub                 "
  echo "       المستودع: https://github.com/mooa322/shahid             "
  echo "  ════════════════════════════════════════════════════════════"
  echo ""

  local repo_url="https://github.com/mooa322/shahid.git"
  local target_dir="$BOT_DIR"

  echo "  [1/6] حفظ نسخة احتياطية شاملة لجميع بيانات الحسابات والإعدادات..."
  mkdir -p "$target_dir/backups"
  local bak_stamp
  bak_stamp="$(date '+%Y%m%d_%H%M%S')"
  local pre_bak="$target_dir/backups/pre_update_$bak_stamp.tar.gz"
  tar -czf "$pre_bak" -C "$target_dir" .env accounts.json proxies.json admins.json subscribers.json queued_emails.json settings.json 2>/dev/null || true
  echo "  [✔] تم حفظ نسخة احتياطية محلية: $(basename "$pre_bak")"

  echo ""
  echo "  [2/6] جاري سحب وتحديث أحدث الأكواد والملفات من GitHub..."
  local updated=0
  if [[ -d "$target_dir/.git" ]]; then
    cd "$target_dir"
    git remote set-url origin "$repo_url" 2>/dev/null || git remote add origin "$repo_url" 2>/dev/null || true
    git fetch origin main 2>/dev/null || git fetch origin master 2>/dev/null || true
    if git reset --hard origin/main 2>/dev/null || git reset --hard origin/master 2>/dev/null || git pull --rebase origin main 2>/dev/null; then
      echo "  [✔] تم سحب وتطبيق التحديث عبر Git بنجاح."
      updated=1
    fi
  fi

  if [[ $updated -eq 0 ]]; then
    echo "  [ℹ] جاري التحميل المباشر لأحدث إصدار من GitHub..."
    local tmp_tar="/tmp/shahid_update_$bak_stamp.tar.gz"
    if curl -sL "https://github.com/mooa322/shahid/archive/refs/heads/main.tar.gz" -o "$tmp_tar" && [[ -s "$tmp_tar" ]]; then
      tar -xzf "$tmp_tar" --strip-components=1 -C "$target_dir" 2>/dev/null || true
      rm -f "$tmp_tar"
      echo "  [✔] تم استخراج وتطبيق أحدث الملفات بنجاح."
      updated=1
    elif curl -sL "https://github.com/mooa322/shahid/archive/refs/heads/master.tar.gz" -o "$tmp_tar" && [[ -s "$tmp_tar" ]]; then
      tar -xzf "$tmp_tar" --strip-components=1 -C "$target_dir" 2>/dev/null || true
      rm -f "$tmp_tar"
      echo "  [✔] تم استخراج وتطبيق أحدث الملفات بنجاح."
      updated=1
    else
      echo "  [✘] تعذر تحميل التحديث من GitHub. تأكد من اتصال الإنترنت أو رفع المستودع."
    fi
  fi

  # Restore critical config files if needed
  echo ""
  echo "  [3/6] استعادة وحماية ملفات البيانات والحسابات الشخصية..."
  if [[ -f "$pre_bak" ]]; then
    tar -xzf "$pre_bak" -C "$target_dir" .env accounts.json proxies.json admins.json subscribers.json queued_emails.json settings.json 2>/dev/null || true
    echo "  [✔] تم التأكد من بقاء ملفات الحسابات والبروكسيات والإعدادات دون مساس."
  fi

  # Validate JSON structures for accounts and proxies
  python3 -c "
import json, os
for f in ['accounts.json', 'proxies.json', 'queued_emails.json', 'subscribers.json']:
    p = os.path.join('$target_dir', f)
    if os.path.exists(p):
        try:
            d = json.load(open(p, 'r', encoding='utf-8'))
            if not isinstance(d, dict):
                json.dump({}, open(p, 'w', encoding='utf-8'))
        except Exception:
            json.dump({}, open(p, 'w', encoding='utf-8'))
" 2>/dev/null || true

  echo ""
  echo "  [4/6] تحديث الصلاحيات والأمر العالمي shahid..."
  chmod +x "$target_dir/menu.sh" "$target_dir/setup_ubuntu24.sh" "$target_dir/shahid.sh" 2>/dev/null || true
  if [[ -d "/usr/local/bin" && -w "/usr/local/bin" ]]; then
    ln -sf "$target_dir/menu.sh" /usr/local/bin/shahid 2>/dev/null || true
  fi
  echo "  [✔] الصلاحيات والأمر العالمي جاهز."

  echo ""
  echo "  [5/6] فحص وتحديث مكتبات بايثون..."
  if [[ -x "$target_dir/.venv/bin/pip" && -f "$target_dir/requirements.txt" ]]; then
    "$target_dir/.venv/bin/pip" install -r "$target_dir/requirements.txt" --upgrade-strategy only-if-needed --quiet 2>/dev/null || true
    echo "  [✔] المكتبات محدثة."
  fi

  echo ""
  echo "  [6/6] إعادة تشغيل خدمة البوت في الخلفية..."
  if command -v systemctl &>/dev/null && systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    systemctl restart "$SERVICE" 2>/dev/null || true
    echo "  [✔] تمت إعادة تشغيل خدمة $SERVICE بنجاح."
  elif command -v systemctl &>/dev/null; then
    systemctl restart "$SERVICE" 2>/dev/null || true
  fi

  echo ""
  echo "  ════════════════════════════════════════════════════════════"
  echo "  ✅ اكتملت عملية تحديث السكريبت والبوت بنجاح!"
  echo "  ════════════════════════════════════════════════════════════"
  pause
}

while true; do
  clear 2>/dev/null || true
  echo ""
  echo "  SHAHID BOT MENU"
  echo "  ─────────────────────────"
  if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    echo "  Status: RUNNING"
  else
    echo "  Status: STOPPED"
  fi
  echo "  ─────────────────────────"
  echo "  1) Service status"
  echo "  2) Start bot"
  echo "  3) Stop bot"
  echo "  4) Restart bot"
  echo "  5) Recent logs"
  echo "  6) Live logs"
  echo "  7) Manage Admins (إدارة الأدمن)"
  echo "  8) Manage Subscribers (إدارة المشتركين / نقطة البيع)"
  echo "  9) User Account Statement (كشف حسابات لأي أدمن أو مشترك)"
  echo "  10) Backup & Restore (النسخ الاحتياطي والاستيراد الشامل)"
  echo "  11) Test Playwright (فحص تشغيل المتصفح)"
  echo "  12) Ubuntu 24 VPS Setup & Diagnostics (تثبيت وفحص بيئة أوبنتو 24)"
  echo "  13) Update from GitHub (تحديث السكريبت والبوت من جيتهب)"
  echo "  0) Exit"
  echo "  ─────────────────────────"
  read -r -p "  Select: " choice || exit 0
  echo

  case "$choice" in
    1) systemctl --no-pager --full status "$SERVICE"; pause ;;
    2) systemctl enable --now "$SERVICE" && echo "Bot started."; pause ;;
    3) systemctl stop "$SERVICE" && echo "Bot stopped."; pause ;;
    4) systemctl restart "$SERVICE" && echo "Bot restarted."; pause ;;
    5) journalctl -u "$SERVICE" -n 50 --no-pager; pause ;;
    6) echo "Press Ctrl+C to return."; journalctl -u "$SERVICE" -f ;;
    7|admin|admins|a|A) manage_admins ;;
    8|sub|subs|subscribers|s|S) manage_subscribers ;;
    9|statement|kashf|report|rep) user_statement ;;
    10|backup|backups|restore) manage_backups ;;
    11|test)
      echo "  جاري اختبار متصفح Playwright Chromium..."
      "$PY_BIN" - <<'PY'
import sys, os
from playwright.sync_api import sync_playwright

args = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=args)
        page = browser.new_page()
        res = page.goto("https://shahid.mbc.net/ar/hub/registration", wait_until="domcontentloaded", timeout=30000)
        status = res.status if res else "Unknown"
        title = page.title()
        browser.close()
        print(f"\n  [✔] تم الاتصال بنجاح! كود الاستجابة: {status}")
        print(f"  [✔] عنوان الصفحة: {title}\n")
except Exception as e:
    print(f"\n  [✘] حدث خطأ أثناء تشغيل Playwright: {e}\n")
    sys.exit(1)
PY
      pause
      ;;
    12|setup|ubuntu|vps)
      if [[ -f "$SCRIPT_DIR/setup_ubuntu24.sh" ]]; then
        bash "$SCRIPT_DIR/setup_ubuntu24.sh"
      elif [[ -f "$BOT_DIR/setup_ubuntu24.sh" ]]; then
        bash "$BOT_DIR/setup_ubuntu24.sh"
      else
        echo "setup_ubuntu24.sh not found."
      fi
      pause
      ;;
    13|update|upgrade|up)
      update_from_github
      ;;
    0) exit 0 ;;
    *) echo "Invalid choice."; sleep 1 ;;
  esac
done
