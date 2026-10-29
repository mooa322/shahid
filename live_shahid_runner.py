import asyncio
import json
import os
import random
import re
import sys
import time
from dotenv import load_dotenv
load_dotenv()
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "runner_state.json")
OTP_FILE = os.path.join(BASE_DIR, "otp.txt")

def set_state(status, detail=""):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"status": status, "detail": detail, "timestamp": time.time()}, f)
    print(f"[STATE] {status}: {detail}", flush=True)

async def remove_cookie_banner(page):
    clicked = False
    accept_selectors = [
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        "button:text-is('موافق')",
        "button:has-text('موافق')",
        "#accept-recommended-btn-handler",
        "#onetrust-reject-all-handler",
    ]
    for sel in accept_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=300):
                await loc.click(force=True, timeout=1000)
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
                      document.getElementById('accept-recommended-btn-handler');
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
            return res;
        }""")
        if js_done:
            clicked = True
    except Exception:
        pass
    return clicked

async def is_on_select_profile_screen(page) -> bool:
    try:
        plus_sel = "[data-testid='SelectProfilePage_profile-plus-icon'], [data-test='plus'], img[alt='اضف ملف']"
        if await page.locator(plus_sel).first.is_visible(timeout=300):
            return True
        body_t = await page.locator("body").inner_text()
        if any(x in body_t for x in ["اختر من يشاهد", "من يشاهد؟", "Select Profile"]):
            return True
    except Exception:
        pass
    return False

async def is_on_interests_screen(page) -> bool:
    """Detects if page is currently on the onboarding interests selection screen."""
    try:
        body_t = await page.locator("body").inner_text()
        has_skip_text = any(x in body_t for x in ["تخطى وابدأ المشاهدة", "تخطي وابدأ المشاهدة", "ابدأ المشاهدة", "تخطي الخطوة"])
        has_interests_text = any(x in body_t for x in ["عروض موسيقية", "مسلسلات أكشن", "أفلام أجنبية", "كوميديا", "جرائم", "مسلسلات خليجية"])
        if has_skip_text or has_interests_text:
            return True
        if "/onboarding" in page.url or "/interests" in page.url:
            return True
    except Exception:
        pass
    return False


async def handle_skip_onboarding(page) -> bool:
    """Actively detects and clicks 'تخطى وابدأ المشاهدة' with resilient polling and verification until dismissed."""
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

        clicked = False
        skip_selectors = [
            r"text=/تخط[ىي]\s*وابد[أا]\s*المشاهد/",
            "text='تخطى وابدأ المشاهدة'",
            "text='تخطي وابدأ المشاهدة'",
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
                loc = page.locator(s).first
                if await loc.is_visible(timeout=500):
                    await loc.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2)
                    await loc.click(timeout=2500)
                    print(f"[+] Clicked skip button using locator: {s}", flush=True)
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            try:
                loc = page.locator("text=/تخط[ىي]/").first
                if await loc.is_visible(timeout=500):
                    box = await loc.bounding_box()
                    if box:
                        await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                        print("[+] Clicked skip via mouse coordinates", flush=True)
                        clicked = True
            except Exception:
                pass

        try:
            js_clicked = await page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('button, a, p, span, div'));
                const el = all.find(e => {
                    const t = (e.innerText || '').trim();
                    return (t.includes('تخطى') || t.includes('تخطي')) && (t.includes('مشاهد') || t.includes('ابدأ') || t.includes('ابدا') || t.length < 25);
                });
                if (el) {
                    el.scrollIntoView({ behavior: 'instant', block: 'center' });
                    el.click();
                    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    const parent = el.closest('button, a, [role="button"], div.cursor-pointer');
                    if (parent && parent !== el) {
                        parent.click();
                        parent.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    }
                    return true;
                }
                return false;
            }""")
            if js_clicked:
                clicked = True
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


async def open_profile_switcher(page):
    print("[*] Ensuring page is on profile selection screen...", flush=True)
    await remove_cookie_banner(page)

    for _ in range(8):
        if await is_on_select_profile_screen(page):
            print("[+] Already on profile selection screen!", flush=True)
            return True
        await asyncio.sleep(0.8)

    if "/hub/profile" in page.url:
        await page.goto("https://shahid.mbc.net/ar/home", wait_until="domcontentloaded")
        await asyncio.sleep(2.5)

    for attempt in range(3):
        if await is_on_select_profile_screen(page):
            return True

        avatar_loc = page.locator("[data-testid='ShahidHeaderContainer_item-avatar-profile'], [data-testid='menuItemavatar']").first
        if await avatar_loc.is_visible(timeout=3000):
            await avatar_loc.click()
            await asyncio.sleep(1.0)
            span = page.locator("[data-testid='selectedProfileDetails_change-profile'] span.underline, span:has-text('تغير الملف الشخصي')").first
            if await span.is_visible(timeout=3000):
                await span.click()
                await asyncio.sleep(2.0)

        for _ in range(10):
            if await is_on_select_profile_screen(page):
                return True
            await asyncio.sleep(1.0)
    return False

async def click_add_profile(page) -> bool:
    print("[*] Clicking '+' / 'إضافة' to open modal...", flush=True)
    await remove_cookie_banner(page)

    modal_input = page.locator("input#profileName, input[placeholder*='الاسم'], input[placeholder*='اسم'], form input[type='text']:visible").first
    try:
        if await modal_input.is_visible(timeout=600):
            return True
    except Exception:
        pass

    plus_targets = [
        "[data-testid='SelectProfilePage_profile-plus-icon'] .rounded-full",
        "[data-testid='SelectProfilePage_profile-plus-icon'] img[alt='اضف ملف']",
        "[data-testid='SelectProfilePage_profile-plus-icon']",
        "[data-test='plus']",
        "p:text-is('إضافة')",
    ]

    for attempt in range(8):
        await remove_cookie_banner(page)
        clicked = False
        for sel in plus_targets:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=600):
                    await loc.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2)
                    await loc.click(timeout=2500)
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            try:
                loc = page.locator("[data-testid='SelectProfilePage_profile-plus-icon']").first
                if await loc.is_visible(timeout=500):
                    box = await loc.bounding_box()
                    if box:
                        await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']*0.35)
                        clicked = True
            except Exception:
                pass

        for _ in range(6):
            await asyncio.sleep(0.5)
            try:
                if await modal_input.is_visible(timeout=500):
                    print("[+] Add profile modal successfully opened!", flush=True)
                    return True
            except Exception:
                pass
        await asyncio.sleep(1.0)

    return False

async def fill_profile_name(page, profile_name: str, target_locator=None) -> bool:
    await remove_cookie_banner(page)
    input_box = target_locator
    if not input_box:
        selectors = [
            "input[placeholder*='الاسم']",
            "input[placeholder*='اسم']",
            "input[placeholder*='ادخل']",
            "input#profileName",
            "form input[type='text']:visible",
            "input[type='text']:visible",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1500):
                    input_box = loc
                    break
            except Exception:
                pass

    if not input_box:
        print("[!] Profile name input not found!", flush=True)
        return False

    try:
        await input_box.scroll_into_view_if_needed()
        await input_box.click(timeout=2500)
    except Exception:
        await input_box.click(force=True)

    await asyncio.sleep(0.2)
    try:
        await input_box.press("Control+A")
        await input_box.press("Backspace")
    except Exception:
        pass
    await asyncio.sleep(0.2)

    try:
        await input_box.press_sequentially(profile_name, delay=random.randint(30, 60))
    except Exception:
        for c in profile_name:
            await page.keyboard.type(c)
            await asyncio.sleep(0.04)

    await asyncio.sleep(0.3)
    try:
        val = await input_box.input_value()
        if val != profile_name:
            await input_box.fill(profile_name)
    except Exception:
        pass

    try:
        await page.evaluate("""(val) => {
            const el = document.querySelector("input[placeholder*='الاسم'], input[placeholder*='ادخل'], input#profileName, form input[type='text']:visible, input[type='text']:visible");
            if (el) {
                el.focus();
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) {
                    setter.call(el, val);
                } else {
                    el.value = val;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.blur();
            }
        }""", profile_name)
    except Exception:
        pass

    await asyncio.sleep(0.5)
    print(f"[+] Successfully filled profile name: '{profile_name}'", flush=True)
    return True

async def run_live(email: str, password: str = "Shahid@Dahoom2026"):
    set_state("STARTING", f"Starting live creation with email: {email}")

    # Remove old OTP file if present
    if os.path.exists(OTP_FILE):
        try:
            os.remove(OTP_FILE)
        except Exception:
            pass

    p = await async_playwright().start()

    # Load proxy from proxies.json if available
    proxy_config = None
    proxies_file = os.path.join(BASE_DIR, "proxies.json")
    if os.path.exists(proxies_file):
        try:
            with open(proxies_file, "r", encoding="utf-8") as pf:
                pdata = json.load(pf)
            entry = pdata.get("1291024378") or pdata.get("global")
            if entry and entry.get("proxies"):
                plist = entry["proxies"]
                c_idx = entry.get("current_index", 0)
                sel = plist[c_idx % len(plist)]
                proxy_config = {"server": sel["server"]}
                if sel.get("username"):
                    proxy_config["username"] = sel["username"]
                    proxy_config["password"] = sel.get("password", "")
                print(f"[*] Loaded rotating proxy #{c_idx % len(plist) + 1}/{len(plist)} from proxies.json: {sel.get('host', sel['server'])}")
        except Exception as e:
            print(f"[!] Proxy load note: {e}")

    browser = await p.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox", "--start-maximized"],
        proxy=proxy_config,
        ignore_default_args=["--enable-automation"],
    )

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        locale="ar-SA",
        timezone_id="Asia/Riyadh",
    )
    page = await context.new_page()
    stealth = Stealth()
    await stealth.apply_stealth_async(page)

    await page.add_init_script("""
        (() => {
            const clickConsent = () => {
                try {
                    if (window.OneTrust && typeof window.OneTrust.AcceptAll === 'function') {
                        window.OneTrust.AcceptAll();
                    }
                    const b = document.getElementById('onetrust-accept-btn-handler') ||
                              document.getElementById('accept-recommended-btn-handler');
                    if (b) b.click();
                } catch(e) {}
            };
            document.addEventListener('DOMContentLoaded', clickConsent);
            const observer = new MutationObserver(() => clickConsent());
            if (document.documentElement) {
                observer.observe(document.documentElement, { childList: true, subtree: true });
            }
        })();
    """)

    # 1. Registration Screen
    set_state("NAVIGATING", "Navigating to registration page...")
    await page.goto("https://shahid.mbc.net/ar/hub/registration", wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(2.0)
    for _ in range(6):
        if await remove_cookie_banner(page):
            break
        await asyncio.sleep(0.4)

    user_input = page.locator("#userName")
    await user_input.wait_for(state="visible", timeout=15000)
    await remove_cookie_banner(page)
    await user_input.click()
    await user_input.press_sequentially(email, delay=35)
    await asyncio.sleep(0.5)
    await remove_cookie_banner(page)

    # Trigger recaptcha with timeout
    try:
        await page.evaluate("""async () => {
            const btn = document.querySelector("button[type='submit']");
            const key = btn ? Object.keys(btn).find(k => k.startsWith('__reactFiber')) : null;
            let fiber = key ? btn[key] : null;
            while (fiber) {
                if (fiber.memoizedProps?.bottomChildren?.props?.getTokenOnDemand) {
                    const p = fiber.memoizedProps.bottomChildren.props.getTokenOnDemand('REGISTER');
                    await Promise.race([p, new Promise(r => setTimeout(r, 3500))]);
                    break;
                }
                fiber = fiber.return;
            }
        }""")
    except Exception:
        pass

    set_state("CLICKING_SUBMIT_EMAIL", "Waiting 3 seconds then clicking متابعة...")
    submit_email_btn = page.locator("form button[type='submit'], button:has-text('متابعة')").first
    for _ in range(12):
        if await submit_email_btn.is_visible() and not await submit_email_btn.is_disabled():
            break
        await asyncio.sleep(0.5)

    print("[*] Submit email button ready. Waiting 3 seconds...", flush=True)
    await asyncio.sleep(3.0)
    await submit_email_btn.click()

    # Wait for OTP screen
    set_state("WAITING_FOR_OTP_SCREEN", "Waiting for OTP verification screen to appear...")
    is_otp = False
    for s in range(30):
        await asyncio.sleep(1.0)
        await remove_cookie_banner(page)
        if "/hub/verification" in page.url or await page.locator("#otpCode").is_visible(timeout=300):
            is_otp = True
            break
        if s % 4 == 0:
            try:
                if await submit_email_btn.is_visible() and not await submit_email_btn.is_disabled():
                    await submit_email_btn.click()
            except Exception:
                pass

    if not is_otp:
        set_state("ERROR", f"Did not reach OTP screen. URL: {page.url}")
        await page.screenshot(path=os.path.join(BASE_DIR, "error_no_otp.png"))
        return

    await page.screenshot(path=os.path.join(BASE_DIR, "step1_otp_screen.png"))
    set_state("WAITING_FOR_USER_OTP", f"Reached OTP verification screen! Please write OTP in chat or into {OTP_FILE}")

    # Wait for OTP file to be created with 4-6 digits
    otp_code = None
    for sec in range(180): # wait up to 3 minutes
        if os.path.exists(OTP_FILE):
            try:
                with open(OTP_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content and re.search(r'\d{4,6}', content):
                    otp_code = re.search(r'\d{4,6}', content).group(0)
                    print(f"[+] Received OTP code: {otp_code}", flush=True)
                    break
            except Exception:
                pass
        await asyncio.sleep(1.0)

    if not otp_code:
        set_state("ERROR", "Timed out waiting for OTP code from user (3 minutes).")
        return

    # 2. Enter OTP
    set_state("ENTERING_OTP", f"Entering OTP code: {otp_code}")
    await remove_cookie_banner(page)
    otp_input = page.locator("#otpCode, input[name='otp'], input[type='tel'], input[placeholder*='رمز']").first
    await otp_input.wait_for(state="visible", timeout=8000)
    await otp_input.click()
    await otp_input.press("Control+A")
    await otp_input.press("Backspace")
    await otp_input.press_sequentially(otp_code, delay=40)
    await asyncio.sleep(1.0)

    # Click submit OTP
    otp_btn = page.locator("form button[type='submit'], button:has-text('تحقق'), button:has-text('متابعة')").first
    for _ in range(8):
        if await otp_btn.is_visible() and not await otp_btn.is_disabled():
            break
        await asyncio.sleep(0.5)

    print("[*] OTP submit button ready. Waiting 3 seconds...", flush=True)
    await asyncio.sleep(3.0)
    await otp_btn.click()

    # 3. Wait for password screen
    set_state("WAITING_PASSWORD_SCREEN", "Waiting for password entry screen...")
    pwd_input = page.locator("input[type='password'], input#password").first
    await pwd_input.wait_for(state="visible", timeout=25000)
    await remove_cookie_banner(page)

    set_state("ENTERING_PASSWORD", "Entering password and accepting terms...")
    await pwd_input.click()
    await pwd_input.press_sequentially(password, delay=35)
    await asyncio.sleep(1.0)

    # Accept checkboxes
    await page.evaluate("""() => {
        ['promotionsNews', 'termsConditions'].forEach(id => {
            const el = document.getElementById(id);
            if (el && !el.checked) el.click();
        });
    }""")
    await asyncio.sleep(1.0)

    # Click create account
    set_state("SUBMITTING_ACCOUNT", "Clicking 'إنشاء حساب'...")
    create_btn = page.locator("button:has-text('إنشاء حساب'), button[type='submit']").first
    for _ in range(8):
        if await create_btn.is_visible() and not await create_btn.is_disabled():
            break
        await asyncio.sleep(0.5)

    await asyncio.sleep(3.0)
    await create_btn.click()

    # 4. Profile 1
    set_state("CREATING_PROFILE_1", "Waiting for Profile 1 onboarding...")
    await asyncio.sleep(4.0)
    await remove_cookie_banner(page)

    p1_box = page.locator("input#profileName, input[placeholder*='اسم الملف'], input[placeholder*='الاسم'], input[type='text']:visible").first
    try:
        await p1_box.wait_for(state="visible", timeout=15000)
        await fill_profile_name(page, "dahoom1", target_locator=p1_box)
        await asyncio.sleep(1.0)

        follow_btn = page.locator("button:has-text('متابعة'), button[type='submit']").first
        for _ in range(8):
            if await follow_btn.is_visible() and not await follow_btn.is_disabled():
                break
            await asyncio.sleep(0.5)

        print("[*] Profile 1 button ready. Waiting 3 seconds...", flush=True)
        await asyncio.sleep(3.0)
        await follow_btn.click()
        await asyncio.sleep(3.0)
    except Exception as e:
        print(f"[!] Profile 1 step note: {e}", flush=True)

    await handle_skip(page)
    await asyncio.sleep(2.0)

    # 5. Profiles 2, 3, 4
    for prof in ["dahoom2", "dahoom3", "dahoom4"]:
        set_state(f"CREATING_{prof.upper()}", f"Creating profile {prof}...")
        for attempt in range(3):
            if await open_profile_switcher(page):
                break
            await asyncio.sleep(2.0)

        opened = False
        for attempt in range(3):
            if await click_add_profile(page):
                opened = True
                break
            await asyncio.sleep(1.5)

        if not opened:
            print(f"[!] Could not open modal for {prof}", flush=True)
            continue

        await fill_profile_name(page, prof)
        await asyncio.sleep(1.0)

        c_btn = page.locator("button:has-text('انشاء'), button:has-text('إنشاء'), button[type='submit']").last
        for _ in range(12):
            if await c_btn.is_visible() and not await c_btn.is_disabled():
                break
            await asyncio.sleep(0.5)

        print(f"[*] 'انشاء' ready for {prof}. Waiting 3 seconds...", flush=True)
        await asyncio.sleep(3.0)
        await c_btn.click()
        await asyncio.sleep(3.5)
        await handle_skip(page)
        await asyncio.sleep(2.0)

    set_state("SUCCESS", f"All profiles created successfully for {email}!")
    await page.screenshot(path=os.path.join(BASE_DIR, "FINAL_LIVE_SUCCESS.png"))
    print("\n[***] ALL 4 PROFILES CREATED SUCCESSFULLY! [***]", flush=True)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python live_shahid_runner.py <email>")
        sys.exit(1)
    asyncio.run(run_live(sys.argv[1]))

