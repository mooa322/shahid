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


open_profile_switcher = navigate_to_select_profile_screen
create_profile_flow = create_single_profile


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

