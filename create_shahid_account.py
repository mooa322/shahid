import asyncio
import os
import random
import re
import sys
import time
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

sys.path.append(os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "brain", "fd86b4b7-ddeb-4eb3-aa32-04d8b8639292", "scratch"))
from test_mail_tm import create_temp_mail, poll_for_otp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def shot(name):
    return os.path.join(BASE_DIR, name)

async def human_type(page, locator, text: str):
    await locator.click()
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.04, 0.08))

async def remove_cookie_banner(page):
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

async def handle_skip(page):
    """Clicks 'تخطى وابدأ المشاهدة' if it appears on screen."""
    await asyncio.sleep(2.5)
    await remove_cookie_banner(page)
    print(f"[*] Checking for skip button on {page.url}...", flush=True)
    try:
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
            print(f"[+] Skip clicked: {clicked}", flush=True)
            await asyncio.sleep(3.0)
            return True
    except Exception as e:
        print(f"[!] Skip check note: {e}", flush=True)
    return False

async def open_profile_switcher(page):
    """Clicks the header avatar and then clicks 'تغير الملف الشخصي'."""
    print("[*] Opening profile switcher from header avatar...", flush=True)
    await remove_cookie_banner(page)
    await asyncio.sleep(1.5)

    # 1. Click header avatar (avoid clicking MBC logo on the right)
    avatar_clicked = False
    selectors = [
        "img[src*='avatar']",
        "img[alt*='avatar']",
        "[data-testid='menuItempromo'] ~ *",
        "header [class*='cursor-pointer']:has(svg, img):not(#shahid-logo)"
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2000):
                print(f"[*] Clicking avatar with selector: {sel}", flush=True)
                await loc.click(force=True)
                avatar_clicked = True
                break
        except Exception:
            pass

    if not avatar_clicked:
        print("[*] Clicking avatar at top-left coordinates (45, 58)...", flush=True)
        await page.mouse.click(45, 58)

    await asyncio.sleep(2.0)

    # 2. Click "تغير الملف الشخصي" inside dropdown
    print("[*] Clicking 'تغير الملف الشخصي'...", flush=True)
    switch_clicked = await page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('button, a, div, span, p'));
        const item = all.find(el => {
            const t = (el.innerText || '').trim();
            return t === 'تغير الملف الشخصي' || t === 'تغيير الملف الشخصي' || (t.includes('تغير الملف') && t.length < 25);
        });
        if (item) {
            item.click();
            return item.innerText;
        }
        return null;
    }""")
    print(f"[+] Switch item clicked: {switch_clicked}", flush=True)
    await asyncio.sleep(3.0)
    print(f"[*] URL at profile screen: {page.url}", flush=True)

async def create_profile_flow(page, name: str):
    print(f"\n======================================", flush=True)
    print(f"[*] Starting creation of: {name}", flush=True)
    print(f"======================================", flush=True)

    # 1. Open profile switcher
    await open_profile_switcher(page)
    await page.screenshot(path=shot(f"{name}_1_profiles_screen.png"))

    # 2. Click "+" or "إضافة"
    print(f"[*] Clicking '+' / 'إضافة'...", flush=True)
    add_clicked = await page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('button, a, div, span, p'));
        const item = all.find(el => {
            const t = (el.innerText || '').trim();
            return t === 'إضافة' || t === '+';
        });
        if (item) {
            item.click();
            return item.innerText;
        }
        return null;
    }""")
    print(f"[+] Add button clicked: {add_clicked}", flush=True)
    await asyncio.sleep(2.5)
    await page.screenshot(path=shot(f"{name}_2_modal.png"))

    # 3. Enter profile name in "ادخل الاسم"
    print(f"[*] Typing name: {name}...", flush=True)
    name_input = page.locator("input[placeholder*='الاسم'], input[placeholder*='اسم'], input[type='text']:visible").first
    await name_input.wait_for(state="visible", timeout=8000)
    await name_input.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.3)
    for c in name:
        await page.keyboard.type(c)
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.0)

    # 4. Click "انشاء"
    print(f"[*] Clicking 'انشاء'...", flush=True)
    # Try native Playwright locator first
    clicked_create = False
    try:
        create_btn = page.locator("button:has-text('انشاء'), button:has-text('إنشاء')").last
        if await create_btn.is_visible(timeout=3000):
            await create_btn.click(force=True)
            clicked_create = True
            print("[+] Native Playwright clicked 'انشاء'", flush=True)
    except Exception as e:
        print(f"[!] Playwright click error: {e}", flush=True)

    if not clicked_create:
        print("[*] Fallback: Clicking button by position (640, 710)...", flush=True)
        await page.mouse.click(640, 710)
        await asyncio.sleep(0.5)

    await asyncio.sleep(4.0)
    await page.screenshot(path=shot(f"{name}_3_done.png"))

    # 5. Handle skip if it appears
    await handle_skip(page)
    await asyncio.sleep(2.0)
    print(f"[+] Successfully finished creation of {name}!", flush=True)

async def main():
    print("[*] Creating disposable email via mail.tm...", flush=True)
    email, token = create_temp_mail()
    password = "Shahid@Dahoom2026"
    print(f"[+] Email: {email}", flush=True)
    print(f"[+] Password: {password}", flush=True)

    stealth = Stealth()
    p = await async_playwright().start()

    env_headless = os.getenv("HEADLESS")
    if env_headless is not None:
        is_headless = env_headless.strip().lower() in ("1", "true", "yes", "y")
    else:
        is_headless = (os.name != "nt") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))

    chromium_args = [
        "--disable-blink-features=AutomationControlled",
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
        args=chromium_args
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="ar-SA",
        timezone_id="Asia/Riyadh"
    )
    page = await context.new_page()
    await stealth.apply_stealth_async(page)

    # 1. Registration
    print("[*] Navigating to registration page...", flush=True)
    await page.goto("https://shahid.mbc.net/ar/hub/registration", wait_until="domcontentloaded")
    await asyncio.sleep(2.5)

    user_input = page.locator("#userName")
    await user_input.wait_for(state="visible", timeout=10000)
    await human_type(page, user_input, email)
    await asyncio.sleep(1.0)
    await page.locator("button[type='submit']").click()
    print("[*] Submitted email, polling for OTP...", flush=True)

    otp = await asyncio.to_thread(poll_for_otp, token, 60)
    if not otp:
        print("[-] OTP not received!")
        return

    print(f"[+] Received OTP: {otp}", flush=True)
    otp_input = page.locator("#otpCode")
    await otp_input.wait_for(state="visible", timeout=10000)
    await human_type(page, otp_input, otp)
    await asyncio.sleep(1.0)
    await page.locator("button[type='submit']").click()
    print("[*] Submitted OTP. Waiting for set-password...", flush=True)

    # 2. Password & Checkboxes
    pwd_input = page.locator("input[type='password']")
    await pwd_input.wait_for(state="visible", timeout=12000)
    await human_type(page, pwd_input, password)
    await asyncio.sleep(1.0)

    # Defuse card links and check checkboxes
    print("[*] Checking terms and promotions...", flush=True)
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

    # Click create account
    print("[*] Clicking 'إنشاء حساب'...", flush=True)
    submit_btn = page.locator("button:has-text('إنشاء حساب'), button[type='submit']")
    await submit_btn.click()
    await asyncio.sleep(5.0)
    print(f"[+] URL after registration: {page.url}", flush=True)

    # 3. Profile 1 (dahoom1)
    print("[*] Profile 1: entering 'dahoom1'...", flush=True)
    await remove_cookie_banner(page)
    name_box = page.locator("input#profileName, input[placeholder*='اسم الملف'], input[type='text']:visible").first
    await name_box.wait_for(state="visible", timeout=12000)
    await human_type(page, name_box, "dahoom1")
    await asyncio.sleep(1.2)

    follow_btn = page.locator("button:has-text('متابعة'), button[type='submit']").first
    await follow_btn.click()
    print("[*] Clicked 'متابعة' for dahoom1.", flush=True)
    await asyncio.sleep(3.0)

    # 4. Skip interests
    print("[*] Skipping onboarding interests...", flush=True)
    await handle_skip(page)
    await asyncio.sleep(3.0)
    print(f"[+] Ready on Home: {page.url}", flush=True)

    # 5. Profiles 2, 3, 4 (dahoom2, dahoom3, dahoom4)
    for prof in ["dahoom2", "dahoom3", "dahoom4"]:
        await create_profile_flow(page, prof)
        await asyncio.sleep(2.0)

    print("\n*****************************************************", flush=True)
    print("[***] ALL 4 PROFILES CREATED SUCCESSFULLY! [***]", flush=True)
    print(f"Email: {email}", flush=True)
    print(f"Password: {password}", flush=True)
    print("Profiles: dahoom1, dahoom2, dahoom3, dahoom4", flush=True)
    print("*****************************************************", flush=True)

    await page.screenshot(path=shot("FINAL_COMPLETED.png"))
    print("[*] KEEPING BROWSER OPEN PER USER INSTRUCTION!", flush=True)
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
