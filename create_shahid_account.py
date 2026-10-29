import asyncio
import json
import os
from dotenv import load_dotenv
load_dotenv()
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

async def remove_cookie_banner(page):
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


async def human_type(page, locator, text: str):
    await remove_cookie_banner(page)
    try:
        await locator.click(timeout=3000)
    except Exception:
        await remove_cookie_banner(page)
        await locator.click(force=True)
    try:
        await locator.press_sequentially(text, delay=random.randint(25, 50))
    except Exception:
        for char in text:
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.04, 0.08))
    try:
        val = await locator.input_value()
        if val != text:
            await locator.fill(text)
    except Exception:
        pass

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
                    let p = el.parentElement;
                    for (let i = 0; i < 4 && p; i++) {
                        p.click();
                        p.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        p = p.parentElement;
                    }
                    return true;
                }
                return false;
            }""")
            if js_clicked:
                clicked = True
        except Exception:
            pass

        # Fallback Strategy: If clicking 'تخطى' didn't dismiss after attempt 2, select categories & click 'متابعة'
        if attempt >= 2 and await is_on_interests_screen(page):
            try:
                print("[*] Interests fallback: selecting categories and clicking 'متابعة'...", flush=True)
                pills = [
                    "text='أفلام أجنبية'",
                    "text='مسلسلات خليجية'",
                    "text='مسلسلات أكشن'",
                    "text='كوميديا'",
                    "text='جرائم'",
                    "text='أنمي'",
                ]
                for p_sel in pills[:2]:
                    try:
                        p_loc = page.locator(p_sel).first
                        if await p_loc.is_visible(timeout=400):
                            await p_loc.click(force=True)
                            await asyncio.sleep(0.3)
                    except Exception:
                        pass

                btn_follow = page.locator("button:has-text('متابعة')").first
                if await btn_follow.is_visible(timeout=600) and not await btn_follow.is_disabled():
                    await btn_follow.click(force=True)
                    print("[+] Clicked 'متابعة' after selecting categories!", flush=True)
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


async def is_on_select_profile_screen(page) -> bool:
    """Checks if we are currently on the 'اختر من يشاهد الآن' profile selection screen."""
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


async def open_avatar_dropdown(page):
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


async def click_change_profile_span(page):
    """Waits patiently for 'تغير الملف الشخصي' to appear in the dropdown menu and clicks it cleanly.
    Clicks the exact underlined text span, never clicks 'تعديل المعلومات الشخصية' or other menu options."""
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
                const span = document.querySelector('[data-testid=\"selectedProfileDetails_change-profile\"] span.underline') ||
                             document.querySelector('[data-testid=\"selectedProfileDetails_change-profile\"] span:last-child') ||
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
            const span = document.querySelector('[data-testid=\"selectedProfileDetails_change-profile\"] span.underline') ||
                         document.querySelector('[data-testid=\"selectedProfileDetails_change-profile\"] span:last-child') ||
                         Array.from(document.querySelectorAll('span')).find(s => {
                             const t = (s.innerText || '').trim();
                             return (t === 'تغير الملف الشخصي' || t === 'تغيير الملف الشخصي') && !t.includes('تعديل');
                         });
            if (span) {
                span.click();
                span.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
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

async def open_profile_switcher(page):
    """Navigates to the profile selection screen ONLY via the user's required UI flow:
    1. Wait for top avatar -> click it.
    2. Wait for 'تغير الملف الشخصي' -> click it.
    3. Wait for profile selection screen / plus icon.
    No direct URL navigation fallback."""
    print("[*] Ensuring page is on profile selection screen...", flush=True)
    await remove_cookie_banner(page)

    # 1. Check if already on profile selection screen (poll for up to 8s)
    # If still on interests screen, dismiss it first!
    if await is_on_interests_screen(page):
        print("[*] Detected interests screen inside profile switcher! Dismissing it now...", flush=True)
        await handle_skip_onboarding(page)
        await asyncio.sleep(1.5)

    print("[*] Checking if already on profile selection screen...", flush=True)
    for _ in range(8):
        if await is_on_select_profile_screen(page):
            print("[+] Already on profile selection screen!", flush=True)
            return True
        await asyncio.sleep(0.8)

    # If accidentally on /hub/profile, return to home first
    if "/hub/profile" in page.url:
        print("[!] Detected on /hub/profile! Returning to home...", flush=True)
        await page.goto("https://shahid.mbc.net/ar/home", wait_until="domcontentloaded")
        await asyncio.sleep(2.5)

    # 2. Strict UI flow retry loop (Avatar -> تغير الملف الشخصي -> Wait for plus icon)
    for attempt in range(3):
        print(f"[*] Navigating to profiles screen via avatar menu (attempt {attempt+1}/3)...", flush=True)
        await remove_cookie_banner(page)

        if await is_on_select_profile_screen(page):
            print("[+] Arrived on profile screen!", flush=True)
            return True

        if "/hub/profile" in page.url:
            print("[!] On /hub/profile, returning to home...", flush=True)
            await page.goto("https://shahid.mbc.net/ar/home", wait_until="domcontentloaded")
            await asyncio.sleep(2.5)

        avatar_opened = await open_avatar_dropdown(page)
        if await is_on_select_profile_screen(page):
            print("[+] Arrived on profile screen!", flush=True)
            return True

        if not avatar_opened:
            print(f"[*] Retrying open avatar dropdown...", flush=True)
            await asyncio.sleep(2.0)
            continue

        clicked = await click_change_profile_span(page)
        if not clicked:
            print(f"[*] 'تغير الملف الشخصي' click retry...", flush=True)
            await asyncio.sleep(2.0)
            continue

        # Wait up to 12s for profile selection screen to appear
        print("[*] Waiting for profile selection screen to load after click...", flush=True)
        for _ in range(12):
            if await is_on_select_profile_screen(page):
                print("[+] Arrived on profile screen via menu!", flush=True)
                return True

            if "/hub/profile" in page.url:
                print("[!] Accidental navigation to /hub/profile detected! Returning to home...", flush=True)
                await page.goto("https://shahid.mbc.net/ar/home", wait_until="domcontentloaded")
                await asyncio.sleep(2.5)
                break

            await asyncio.sleep(1.0)

    print(f"[*] URL at profile screen: {page.url}", flush=True)
    return False


async def click_add_profile(page) -> bool:
    """Clicks '+' / 'إضافة' to open the new profile modal with active polling and verification."""
    print("[*] Clicking '+' / 'إضافة' to open modal...", flush=True)
    await remove_cookie_banner(page)

    # 1. Check if modal is ALREADY open
    modal_input = page.locator("input#profileName, input[placeholder*='الاسم'], input[placeholder*='اسم'], input[placeholder*='ادخل'], form input[type='text']:visible").first
    try:
        if await modal_input.is_visible(timeout=600):
            print("[+] Add profile modal is already open!", flush=True)
            return True
    except Exception:
        pass

    # Locators for plus icon / circle / text
    plus_targets = [
        "[data-testid='SelectProfilePage_profile-plus-icon'] .rounded-full",
        "[data-testid='SelectProfilePage_profile-plus-icon'] img[alt='اضف ملف']",
        "[data-testid='SelectProfilePage_profile-plus-icon'] img",
        "[data-testid='SelectProfilePage_profile-plus-icon']",
        "[data-test='plus']",
        "img[alt='اضف ملف']",
        "p:text-is('إضافة')",
    ]

    for attempt in range(8):
        print(f"[*] Attempt {attempt+1}/8 to click '+' / 'إضافة'...", flush=True)
        await remove_cookie_banner(page)

        # Check if modal opened in the meantime
        try:
            if await modal_input.is_visible(timeout=400):
                print("[+] Add profile modal is open!", flush=True)
                return True
        except Exception:
            pass

        # Strategy A: Real Playwright click on the first visible plus element
        clicked = False
        for sel in plus_targets:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=600):
                    await loc.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2)
                    await loc.click(timeout=2500)
                    print(f"[+] Clicked '+' using locator: {sel}", flush=True)
                    clicked = True
                    break
            except Exception:
                pass

        # Strategy B: Click using mouse coordinates at center of circular plus icon
        if not clicked:
            try:
                loc = page.locator("[data-testid='SelectProfilePage_profile-plus-icon']").first
                if await loc.is_visible(timeout=500):
                    box = await loc.bounding_box()
                    if box:
                        cx = box['x'] + box['width'] / 2
                        cy = box['y'] + (box['height'] * 0.35)
                        await page.mouse.click(cx, cy)
                        print(f"[+] Clicked '+' via mouse coordinates: ({cx}, {cy})", flush=True)
                        clicked = True
            except Exception:
                pass

        # Strategy C: Dispatch bubbling events in JavaScript directly on the plus circle
        if not clicked:
            try:
                clicked = await page.evaluate("""() => {
                    const plus = document.querySelector('[data-testid="SelectProfilePage_profile-plus-icon"]') ||
                                 document.querySelector('img[alt="اضف ملف"]') ||
                                 document.querySelector('[data-test="plus"]');
                    if (plus) {
                        const circle = plus.querySelector('.rounded-full') || plus.querySelector('img') || plus;
                        circle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        circle.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        circle.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        circle.click();
                        return true;
                    }
                    return false;
                }""")
            except Exception:
                pass

        # Check if modal opened (wait up to 3 seconds)
        for _ in range(6):
            await asyncio.sleep(0.5)
            try:
                if await modal_input.is_visible(timeout=500):
                    print("[+] Add profile modal successfully opened (input field visible)!", flush=True)
                    return True
                body_t = await page.locator("body").inner_text()
                if any(k in body_t for k in ["ادخل الاسم", "اضافة ملف", "أضف ملف"]):
                    print("[+] Add profile modal detected via text!", flush=True)
                    return True
            except Exception:
                pass

        await asyncio.sleep(1.0)

    print("[!] Failed to open add profile modal after 8 attempts.", flush=True)
    return False


async def fill_profile_name(page, profile_name: str, target_locator=None) -> bool:
    """Robustly types and verifies the profile name in the input field, clearing any 'هذه الخانة مطلوبة' error."""
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

    # 1. Type with sequential keyboard events
    try:
        await input_box.press_sequentially(profile_name, delay=random.randint(30, 60))
    except Exception:
        for c in profile_name:
            await page.keyboard.type(c)
            await asyncio.sleep(0.04)

    await asyncio.sleep(0.3)

    # 2. Check and fill fallback
    try:
        val = await input_box.input_value()
        if val != profile_name:
            await input_box.fill(profile_name)
    except Exception:
        try:
            await input_box.fill(profile_name)
        except Exception:
            pass

    # 3. Native React input setter and event dispatch directly on input_box to ensure state sync and clear 'هذه الخانة مطلوبة'
    try:
        await input_box.evaluate("""(el, val) => {
            el.focus();
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
            if (setter) {
                setter.call(el, val);
            } else {
                el.value = val;
            }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
        }""", profile_name)
    except Exception:
        try:
            await page.evaluate("""(val) => {
                const el = document.querySelector("input[placeholder*='الاسم'], input[placeholder*='ادخل'], input#profileName, form input[type='text']:visible, input[type='text']:visible");
                if (el) {
                    el.focus();
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                    if (setter) setter.call(el, val); else el.value = val;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
                }
            }""", profile_name)
        except Exception:
            pass

    # 4. Trigger typing keys to finalize React form validation
    try:
        await input_box.press("End")
        await input_box.press("Space")
        await asyncio.sleep(0.05)
        await input_box.press("Backspace")
    except Exception:
        pass

    await asyncio.sleep(0.5)
    print(f"[+] Successfully filled profile name: '{profile_name}'", flush=True)
    return True


async def human_type_locator(page, locator, text: str) -> bool:
    """Wrapper for fill_profile_name to safely type into locator with React event handling."""
    return await fill_profile_name(page, text, target_locator=locator)


async def create_profile_flow(page, name: str):
    print("\n======================================", flush=True)
    print(f"[*] Starting creation of: {name}", flush=True)
    print(f"======================================", flush=True)

    # 1. Skip onboarding / interests ONLY if not already on profile selection screen
    if not await is_on_select_profile_screen(page):
        await handle_skip(page)

    # 2. Open profile switcher with retry
    for attempt in range(3):
        if await open_profile_switcher(page):
            break
        print(f"[*] Retrying open profile switcher ({attempt+1}/3)...", flush=True)
        await asyncio.sleep(2.0)

    await page.screenshot(path=shot(f"{name}_1_profiles_screen.png"))

    # 3. Click "+" or "إضافة" to open modal
    opened_modal = False
    for attempt in range(3):
        if await click_add_profile(page):
            opened_modal = True
            break
        print(f"[*] Retrying click on '+' (attempt {attempt+1}/3)...", flush=True)
        await asyncio.sleep(1.5)

    if not opened_modal:
        print(f"[!] Could not open modal for {name}", flush=True)

    await page.screenshot(path=shot(f"{name}_2_modal.png"))

    # 4. Enter profile name in "ادخل الاسم"
    print(f"[*] Typing name: {name}...", flush=True)
    await fill_profile_name(page, name)
    await asyncio.sleep(1.0)

    # 5. Click "انشاء" with active polling and 3s delay
    print(f"[*] Clicking 'انشاء'...", flush=True)
    btn_selectors = [
        "button:has-text('انشاء')",
        "button:has-text('إنشاء')",
        "button[type='submit']",
    ]
    clicked_create = False
    for attempt in range(15):
        for s in btn_selectors:
            try:
                loc = page.locator(s).last
                if await loc.is_visible(timeout=1000) and not await loc.is_disabled():
                    print(f"[*] 'انشاء' button is enabled. Waiting 3 seconds before clicking per user instruction...", flush=True)
                    await asyncio.sleep(3.0)
                    await loc.click(force=True)
                    clicked_create = True
                    print(f"[+] Clicked 'انشاء' using selector: {s}", flush=True)
                    break
            except Exception:
                pass
        if clicked_create:
            break

        try:
            clicked_create = await page.evaluate("""() => {
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
        except Exception:
            pass
        if clicked_create:
            break
        await asyncio.sleep(1.0)

    await asyncio.sleep(4.0)
    await page.screenshot(path=shot(f"{name}_3_done.png"))

    # 6. Handle skip if it appears
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

    proxy_config = None
    proxies_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxies.json")
    if os.path.exists(proxies_file):
        try:
            with open(proxies_file, "r", encoding="utf-8") as pf:
                pdata = json.load(pf)
            entry = None
            for k in ["global", "1291024378"] + list(pdata.keys()):
                if k in pdata and pdata[k].get("proxies"):
                    entry = pdata[k]
                    break
            if entry and entry.get("proxies"):
                plist = entry["proxies"]
                c_idx = entry.get("current_index", 0)
                sel = plist[c_idx % len(plist)]
                if entry.get("rotation_enabled", True) and len(plist) > 1:
                    entry["current_index"] = (c_idx + 1) % len(plist)
                    with open(proxies_file, "w", encoding="utf-8") as pf:
                        json.dump(pdata, pf, ensure_ascii=False, indent=2)
                proxy_config = {"server": sel["server"]}
                if sel.get("username"):
                    proxy_config["username"] = sel["username"]
                    proxy_config["password"] = sel.get("password", "")
                print(f"[*] Loaded rotating proxy #{c_idx % len(plist) + 1}/{len(plist)} from proxies.json: {sel.get('host', sel['server'])}")
        except Exception as e:
            print(f"[!] Note: Could not load proxy from proxies.json: {e}")

    if not proxy_config:
        proxy_server = os.getenv("PROXY_SERVER")
        if proxy_server:
            proxy_config = {"server": proxy_server}
            if os.getenv("PROXY_USERNAME"):
                proxy_config["username"] = os.getenv("PROXY_USERNAME")
                proxy_config["password"] = os.getenv("PROXY_PASSWORD", "")

    browser = await p.chromium.launch(
        headless=is_headless,
        args=chromium_args,
        proxy=proxy_config,
        ignore_default_args=["--enable-automation"],
    )
    is_egypt = (
        proxy_config and (
            proxy_config.get("is_egypt", True)
            or "eg" in str(proxy_config.get("server", "")).lower()
            or "eg" in str(proxy_config.get("username", "")).lower()
        )
    )
    locale_setting = "ar-EG" if is_egypt else "ar-SA"
    tz_setting = "Africa/Cairo" if is_egypt else "Asia/Riyadh"

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        locale=locale_setting,
        timezone_id=tz_setting,
    )
    page = await context.new_page()
    await stealth.apply_stealth_async(page)

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

    # 1. Registration
    print("[*] Navigating to registration page...", flush=True)
    await page.goto("https://shahid.mbc.net/ar/hub/registration", wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(1.5)
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

    # Helper to trigger reCAPTCHA Enterprise resolution
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
            print(f"[!] Note on reCAPTCHA execution: {e}", flush=True)

    print("[*] Triggering reCAPTCHA Enterprise resolution...", flush=True)
    await trigger_recaptcha()

    # Active polling loop: monitor transition and retry clicking 'متابعة' on network glitch/warning
    print("[*] Waiting for submit button to be enabled and transition to OTP screen...", flush=True)
    is_otp = False
    has_clicked = False
    last_click_time = 0.0
    retry_count = 0
    max_retries = 5

    for s in range(45):
        await asyncio.sleep(1.0)
        await remove_cookie_banner(page)
        cur_url = page.url

        try:
            if "/hub/verification" in cur_url or await page.locator("#otpCode").is_visible(timeout=300):
                is_otp = True
                print(f"[+] Reached OTP verification screen at second {s+1}!", flush=True)
                break
        except Exception:
            pass

        if "/hub/login" in cur_url:
            print(f"[*] Reached login screen at second {s+1} (email already registered).", flush=True)
            break

        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass

        has_network_warning = any(w in body_text for w in ["تقييد الوصول", "نشاط غير معتاد", "مشكلة في الشبكة", "خطأ في الشبكة"])

        try:
            btn = page.locator("form button[type='submit'], button:has-text('متابعة')").first
            if await btn.is_visible(timeout=300):
                btn_enabled = not await btn.is_disabled()
                now = asyncio.get_event_loop().time()

                if btn_enabled:
                    if not has_clicked:
                        print(f"[*] Submit button is ENABLED at second {s+1}! Waiting 3 seconds before clicking...", flush=True)
                        await asyncio.sleep(3.0)
                        if not await btn.is_disabled():
                            print("[*] Clicking submit button now...", flush=True)
                            await btn.click()
                            has_clicked = True
                            last_click_time = now
                            await asyncio.sleep(1.5)
                    elif has_network_warning and (now - last_click_time >= 3.0) and retry_count < max_retries:
                        retry_count += 1
                        print(f"[*] Network warning detected. Waiting 3 seconds before retry ({retry_count}/{max_retries})...", flush=True)
                        await asyncio.sleep(3.0)
                        await trigger_recaptcha()
                        await asyncio.sleep(1.5)
                        if await btn.is_visible(timeout=500) and not await btn.is_disabled():
                            print(f"[*] Button is white/ready. Waiting 3 seconds before clicking ({retry_count}/{max_retries})...", flush=True)
                            await asyncio.sleep(3.0)
                            try:
                                await btn.click()
                            except Exception:
                                await page.evaluate("() => document.querySelector(\"button[type='submit'], button:has-text('متابعة')\")?.click()")
                            last_click_time = asyncio.get_event_loop().time()
                            await asyncio.sleep(2.0)
                    elif not has_network_warning and (now - last_click_time >= 5.0) and (s % 4 == 0):
                        print(f"[*] Retrying submit click at second {s+1}...", flush=True)
                        await btn.click()
                        last_click_time = now
                        await asyncio.sleep(1.0)
        except Exception:
            pass

    if not is_otp and not ("/hub/login" in page.url):
        for final_try in range(2):
            try:
                btn = page.locator("form button[type='submit'], button:has-text('متابعة')").first
                if await btn.is_visible(timeout=500) and not await btn.is_disabled():
                    print(f"[*] Final retry attempt {final_try+1} for 'متابعة'...", flush=True)
                    await trigger_recaptcha()
                    await asyncio.sleep(1.5)
                    await btn.click()
                    await asyncio.sleep(3.0)
                    if "/hub/verification" in page.url or await page.locator("#otpCode").is_visible(timeout=500):
                        is_otp = True
                        print("[+] Reached OTP verification screen on final retry!", flush=True)
                        break
            except Exception:
                pass

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
    await remove_cookie_banner(page)
    # Wait for OTP submit button to be visible and enabled, then wait 2 seconds before clicking
    submit_btn = page.locator("form button[type='submit'], button:has-text('تحقق'), button:has-text('متابعة')").first
    for _ in range(10):
        if await submit_btn.is_visible() and not await submit_btn.is_disabled():
            break
        await asyncio.sleep(0.5)

    print("[*] OTP submit button is enabled. Waiting 3 seconds before clicking per user instruction...", flush=True)
    await asyncio.sleep(3.0)

    try:
        await submit_btn.click(timeout=4000)
    except Exception:
        await submit_btn.click(force=True)
    print("[*] Submitted OTP. Waiting for set-password with retry on network lag (up to 15 attempts)...", flush=True)

    pwd_input = page.locator("input[type='password']").first
    otp_retry_count = 0
    max_otp_retries = 15
    for s in range(60):
        await asyncio.sleep(1.0)
        await remove_cookie_banner(page)
        if await pwd_input.is_visible():
            print(f"[+] Reached password screen at second {s+1}!", flush=True)
            break
        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        has_net_warn = any(w in body_text for w in ["تقييد الوصول", "نشاط غير معتاد", "مشكلة في الشبكة", "خطأ في الشبكة"])
        if (has_net_warn or s % 4 == 0) and otp_retry_count < max_otp_retries:
            otp_retry_count += 1
            print(f"[*] OTP step network lag/pressure detected. Waiting 3 seconds before retry ({otp_retry_count}/{max_otp_retries})...", flush=True)
            await asyncio.sleep(3.0)
            try:
                btn = page.locator("form button[type='submit'], button:has-text('تحقق'), button:has-text('متابعة')").first
                if await btn.is_visible() and not await btn.is_disabled():
                    print(f"[*] OTP button is white/enabled. Waiting 3 seconds before clicking ({otp_retry_count}/{max_otp_retries})...", flush=True)
                    await asyncio.sleep(3.0)
                    await btn.click()
            except Exception:
                pass

    # 2. Password & Checkboxes
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

    # Wait for create account button to be enabled, then wait 2 seconds before clicking
    submit_btn = page.locator("button:has-text('إنشاء حساب'), button[type='submit']").first
    for _ in range(10):
        if await submit_btn.is_visible() and not await submit_btn.is_disabled():
            break
        await asyncio.sleep(0.5)

    print("[*] Password submit button is enabled. Waiting 3 seconds before clicking per user instruction...", flush=True)
    await asyncio.sleep(3.0)
    print("[*] Clicking 'إنشاء حساب'...", flush=True)
    try:
        await submit_btn.click(timeout=5000)
    except Exception:
        await submit_btn.evaluate("btn => btn.click()")

    # Resilient wait for URL transition with retry on network lag (up to 15 attempts)
    pwd_retry_count = 0
    max_pwd_retries = 15
    for s in range(60):
        await asyncio.sleep(1.0)
        await remove_cookie_banner(page)
        cur_url = page.url
        if any(path in cur_url for path in ["/onboarding", "/profile", "/home"]) or ("set-password" not in cur_url and "/hub/" not in cur_url):
            break
        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        has_net_warn = any(w in body_text for w in ["تقييد الوصول", "نشاط غير معتاد", "مشكلة في الشبكة", "خطأ في الشبكة"])
        if (has_net_warn or s % 4 == 0) and pwd_retry_count < max_pwd_retries:
            pwd_retry_count += 1
            print(f"[*] Account creation network lag detected. Waiting 3 seconds before retry ({pwd_retry_count}/{max_pwd_retries})...", flush=True)
            await asyncio.sleep(3.0)
            try:
                btn = page.locator("form button[type='submit'], button:has-text('إنشاء حساب'), button:has-text('متابعة')").first
                if await btn.is_visible() and not await btn.is_disabled():
                    print(f"[*] 'إنشاء حساب' button is white/enabled. Waiting 3 seconds before clicking ({pwd_retry_count}/{max_pwd_retries})...", flush=True)
                    await asyncio.sleep(3.0)
                    await btn.click()
            except Exception:
                pass

    print(f"[+] URL after registration: {page.url}", flush=True)

    skip_profiles = "--no-profiles" in sys.argv or os.getenv("SKIP_PROFILES") == "1"
    if skip_profiles:
        print("\n*****************************************************", flush=True)
        print("[***] ACCOUNT CREATED SUCCESSFULLY (SKIPPED PROFILES)! [***]", flush=True)
        print(f"Email: {email}", flush=True)
        print(f"Password: {password}", flush=True)
        print("*****************************************************", flush=True)
        await page.screenshot(path=shot("FINAL_COMPLETED_NO_PROFILES.png"))
        print("[*] KEEPING BROWSER OPEN PER USER INSTRUCTION!", flush=True)
        while True:
            await asyncio.sleep(60)

    custom_profiles = sys.argv[1:5] if len(sys.argv) >= 5 else ["dahoom1", "dahoom2", "dahoom3", "dahoom4"]

    # 3. Profile 1
    p1 = custom_profiles[0]
    print(f"[*] Profile 1: entering '{p1}'...", flush=True)
    await remove_cookie_banner(page)
    await fill_profile_name(page, p1)

    follow_btn = page.locator("button:has-text('متابعة'), button[type='submit']").first
    for _ in range(8):
        if await follow_btn.is_visible() and not await follow_btn.is_disabled():
            break
        await asyncio.sleep(0.5)

    print(f"[*] Profile 1 button is enabled. Waiting 3 seconds before clicking 'متابعة' per user instruction...", flush=True)
    await asyncio.sleep(3.0)
    await follow_btn.click()
    print(f"[*] Clicked 'متابعة' for {p1}.", flush=True)

    # Resilient wait for Profile 1 transition with retry on network lag (up to 15 retries)
    p1_retries = 0
    for s in range(45):
        await asyncio.sleep(1.0)
        await remove_cookie_banner(page)
        if not await page.locator("input#profileName").is_visible():
            break
        body_text = ""
        try:
            body_text = await page.locator("body").inner_text()
        except Exception:
            pass
        has_net_warn = any(w in body_text for w in ["تقييد الوصول", "نشاط غير معتاد", "مشكلة في الشبكة", "خطأ في الشبكة"])
        if (has_net_warn or s % 3 == 0) and p1_retries < 15:
            p1_retries += 1
            print(f"[*] Profile 1 network lag detected. Waiting 3 seconds before retry ({p1_retries}/15)...", flush=True)
            await asyncio.sleep(3.0)
            try:
                if await follow_btn.is_visible() and not await follow_btn.is_disabled():
                    print(f"[*] Profile 1 button is white/enabled. Waiting 3 seconds before clicking ({p1_retries}/15)...", flush=True)
                    await asyncio.sleep(3.0)
                    await follow_btn.click()
            except Exception:
                pass
    await asyncio.sleep(2.0)

    # 4. Skip interests
    print("[*] Skipping onboarding interests...", flush=True)
    await handle_skip(page)
    await asyncio.sleep(3.0)
    print(f"[+] Ready on Home: {page.url}", flush=True)

    # 5. Profiles 2, 3, 4
    for prof in custom_profiles[1:]:
        await create_profile_flow(page, prof)
        await asyncio.sleep(2.0)

    print("\n*****************************************************", flush=True)
    print("[***] ALL 4 PROFILES CREATED SUCCESSFULLY! [***]", flush=True)
    print(f"Email: {email}", flush=True)
    print(f"Password: {password}", flush=True)
    print(f"Profiles: {', '.join(custom_profiles)}", flush=True)
    print("*****************************************************", flush=True)

    await page.screenshot(path=shot("FINAL_COMPLETED.png"))
    print("[*] KEEPING BROWSER OPEN PER USER INSTRUCTION!", flush=True)
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
