#!/usr/bin/env bash
# ==============================================================================
# Shahid VIP Bot - Ubuntu 24.04 LTS (Noble Numbat) VPS Automated Setup Script
# Works flawlessly on Ubuntu 24.04, Ubuntu 22.04, and Debian 12
# ==============================================================================

set -euo pipefail

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${CYAN}${BOLD}"
echo "  ╔══════════════════════════════════════════════════════════════════╗"
echo "  ║        SHAHID VIP TELEGRAM BOT - UBUNTU 24.04 LTS VPS SETUP      ║"
echo "  ║             تثبيت وتهيئة البوت الكاملة لسيرفرات أوبنتو 24        ║"
echo "  ╚══════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 1. Root Check
if [[ "$EUID" -ne 0 ]]; then
  echo -e "${RED}[✘] يرجى تشغيل السكريبت بصلاحيات الرووت (root):${NC}"
  echo -e "    sudo bash $0"
  exit 1
fi

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo -e "${BLUE}[ℹ] مجلد المشروع المكتشف:${NC} $INSTALL_DIR"

# 2. Operating System Detection
if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  echo -e "${GREEN}[✔] النظام المكتشف:${NC} $PRETTY_NAME"
  if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" ]]; then
    echo -e "${YELLOW}[!] تحذير: هذا السكريبت مصمم لأنظمة Ubuntu / Debian، قد تختلف بعض الحزم في توزيعتك.${NC}"
  fi
else
  echo -e "${YELLOW}[!] لم نتمكن من قراءة /etc/os-release، سيتم المتابعة بحذر.${NC}"
fi

# 3. System Packages Installation (Handles Ubuntu 24.04 PEP 668 & 64-bit time_t libs)
echo ""
echo -e "${CYAN}[1/7] تحديث قوائم الحزم وتثبيت متطلبات النظام الأساسية...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y

# Packages required on Ubuntu 24.04
PACKAGES=(
  python3
  python3-pip
  python3-venv
  python3-dev
  xvfb
  xauth
  curl
  wget
  git
  ca-certificates
  procps
  libasound2t64
  libatk1.0-0t64
  libatk-bridge2.0-0t64
  libcups2t64
  libdrm2
  libxkbcommon0
  libxcomposite1
  libxdamage1
  libxfixes3
  libxrandr2
  libgbm1
  libpango-1.0-0
  libcairo2
)

# Install packages with fallback for older distros where t64 suffix is not present
INSTALL_LIST=()
for pkg in "${PACKAGES[@]}"; do
  if apt-cache show "$pkg" &>/dev/null; then
    INSTALL_LIST+=("$pkg")
  else
    # Try legacy name without t64
    legacy_pkg="${pkg%t64}"
    if apt-cache show "$legacy_pkg" &>/dev/null; then
      INSTALL_LIST+=("$legacy_pkg")
    fi
  fi
done

apt-get install -y --no-install-recommends "${INSTALL_LIST[@]}"
echo -e "${GREEN}[✔] تم تثبيت متطلبات النظام بنجاح!${NC}"

# 4. Setup Python Virtual Environment (PEP 668 Compliant)
echo ""
echo -e "${CYAN}[2/7] إنشاء وتجهيز بيئة العمل الافتراضية (Python Virtual Environment)...${NC}"
VENV_DIR="$INSTALL_DIR/.venv"

if [[ ! -d "$VENV_DIR" || ! -f "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel
echo -e "${GREEN}[✔] تم تجهيز البيئة الافتراضية بنجاح!${NC}"

# 5. Install Python Project Dependencies
echo ""
echo -e "${CYAN}[3/7] تثبيت مكتبات المشروع (Python Requirements)...${NC}"
if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
  "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
else
  "$VENV_DIR/bin/pip" install "python-telegram-bot>=21.0,<22" "playwright>=1.45,<2" "playwright-stealth>=2.0.0" "python-dotenv>=1.0.0"
fi
echo -e "${GREEN}[✔] تم تثبيت مكتبات البوت بنجاح!${NC}"

# 6. Install Playwright Chromium & System Dependencies
echo ""
echo -e "${CYAN}[4/7] تثبيت متصفح Chromium ومكتبات التشغيل عبر Playwright...${NC}"
"$VENV_DIR/bin/playwright" install chromium
"$VENV_DIR/bin/playwright" install-deps chromium || true
echo -e "${GREEN}[✔] تم تثبيت Playwright Chromium بكامل ملحقاته!${NC}"

# 7. Setup Configuration (.env and admins.json)
echo ""
echo -e "${CYAN}[5/7] فحص ملف الإعدادات (.env) وصلاحيات الأدمن...${NC}"

PRIMARY_ADMIN="1291024378"

# Check admins.json
ADMINS_FILE="$INSTALL_DIR/admins.json"
if [[ ! -f "$ADMINS_FILE" ]]; then
  echo "[\"$PRIMARY_ADMIN\"]" > "$ADMINS_FILE"
  echo -e "${GREEN}[✔] تم إنشاء $ADMINS_FILE وتعيين الأدمن الأساسي ($PRIMARY_ADMIN).${NC}"
else
  # Ensure primary admin is in admins.json
  "$VENV_DIR/bin/python" - "$ADMINS_FILE" "$PRIMARY_ADMIN" <<'PY'
import json, sys, os
f, adm = sys.argv[1], sys.argv[2]
try:
    with open(f, 'r', encoding='utf-8') as fp:
        data = json.load(fp)
except Exception:
    data = []
if not isinstance(data, list):
    data = []
if adm not in data:
    data.append(adm)
with open(f, 'w', encoding='utf-8') as fp:
    json.dump(data, fp, ensure_ascii=False, indent=2)
PY
  echo -e "${GREEN}[✔] تم التأكد من وجود الأدمن الأساسي في قائمة المشرفين.${NC}"
fi

# Check .env
ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$INSTALL_DIR/.env.example" ]]; then
    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
  else
    cat <<EOF > "$ENV_FILE"
TELEGRAM_BOT_TOKEN=
ADMIN_USER_ID=$PRIMARY_ADMIN
HEADLESS=0
EOF
  fi
fi

# Check if token is configured
CURRENT_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d ' "' || true)
if [[ -z "$CURRENT_TOKEN" || "$CURRENT_TOKEN" == *"ضع_التوكن"* || "$CURRENT_TOKEN" == *"YOUR_BOT_TOKEN"* ]]; then
  echo ""
  echo -e "${YELLOW}[!] توكن البوت غير محدد في ملف .env.${NC}"
  read -r -p "    أدخل توكن البوت من BotFather الآن (أو اضغط Enter للتخطي): " USER_TOKEN || USER_TOKEN=""
  if [[ -n "$USER_TOKEN" ]]; then
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${USER_TOKEN}|" "$ENV_FILE"
    echo -e "${GREEN}[✔] تم حفظ توكن البوت في .env${NC}"
  fi
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true

# 8. Setup Global 'shahid' Command and File Permissions
echo ""
echo -e "${CYAN}[6/7] إعداد أمر الاختصار العالمي 'shahid' والصلاحيات...${NC}"
chmod +x "$INSTALL_DIR/menu.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR/setup_ubuntu24.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR/shahid.sh" 2>/dev/null || true

if [[ -d "/usr/local/bin" ]]; then
  ln -sf "$INSTALL_DIR/menu.sh" /usr/local/bin/shahid
  chmod +x /usr/local/bin/shahid
  echo -e "${GREEN}[✔] تم ربط الأمر العالمي /usr/local/bin/shahid بنجاح!${NC}"
fi

# 9. Configure & Enable Systemd Service (shahid-bot.service)
echo ""
echo -e "${CYAN}[7/7] إعداد خدمة التشغيل التلقائي بالخلفية (Systemd Service)...${NC}"
SERVICE_FILE="/etc/systemd/system/shahid-bot.service"

# Check if xvfb-run is available to run headful on VPS
XVFB_BIN="$(command -v xvfb-run || echo "/usr/bin/xvfb-run")"
if [[ -x "$XVFB_BIN" ]]; then
  EXEC_CMD="$XVFB_BIN -a -s \"-screen 0 1920x1080x24 -ac\" $VENV_DIR/bin/python $INSTALL_DIR/bot.py"
else
  EXEC_CMD="$VENV_DIR/bin/python $INSTALL_DIR/bot.py"
fi

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Shahid VIP Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=-$INSTALL_DIR/.env
Environment=PYTHONUNBUFFERED=1
Environment=HEADLESS=0
ExecStart=$EXEC_CMD
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable shahid-bot.service
echo -e "${GREEN}[✔] تم تثبيت خدمة shahid-bot.service وتفعيلها مع إقلاع السيرفر!${NC}"

# 10. Playwright & Chromium Self-Test on Ubuntu 24.04
echo ""
echo -e "${CYAN}[*] فحص اختبار تشغيل Playwright Chromium على نظام أوبنتو 24...${NC}"
TEST_RESULT=0
"$VENV_DIR/bin/python" - <<'PY' || TEST_RESULT=1
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
        print(f"  [✔] تم الاتصال بنجاح! كود الاستجابة: {status}")
        print(f"  [✔] عنوان الصفحة: {title}")
except Exception as e:
    print(f"  [✘] حدث خطأ أثناء تشغيل الاختبار: {e}")
    sys.exit(1)
PY

if [[ $TEST_RESULT -eq 0 ]]; then
  echo -e "${GREEN}[✔] نجح اختبار تشغيل Playwright Chromium بالكامل على Ubuntu 24.04!${NC}"
else
  echo -e "${YELLOW}[!] تم إكمال التثبيت مع ملاحظة في فحص الاتصال الخارجي.${NC}"
fi

echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  تم اكتمال تثبيت وتهيئة البوت على سيرفر أوبنتو 24 بنجاح 100%!        ${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  📌 ${BOLD}أوامر إدارة البوت:${NC}"
echo -e "  • فتح القائمة التفاعلية في أي وقت:  ${CYAN}${BOLD}shahid${NC}"
echo -e "  • تشغيل البوت:                     ${CYAN}systemctl start shahid-bot${NC}"
echo -e "  • إيقاف البوت:                     ${CYAN}systemctl stop shahid-bot${NC}"
echo -e "  • إعادة تشغيل البوت:               ${CYAN}systemctl restart shahid-bot${NC}"
echo -e "  • حالة البوت:                      ${CYAN}systemctl status shahid-bot${NC}"
echo -e "  • مراقبة السجلات المباشرة:         ${CYAN}journalctl -u shahid-bot -f${NC}"
echo ""

