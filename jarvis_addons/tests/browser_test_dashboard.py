r"""Real browser test of the dashboard (headless Chrome through Selenium). The dashboard must be running.
    venv\Scripts\python.exe jarvis_addons\tests\browser_test_dashboard.py PASSWORD_FILE OUT_DIR [all|basic]
"all" also asks JARVIS to build a new capability with the real AI; "basic" leaves that out."""
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

PW = re.search(r"password: (\S+)", open(sys.argv[1], encoding="utf-8").read()).group(1)
OUT, PHASE = sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "all")
URL = "http://127.0.0.1:8765"
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"U: {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def browser(width, height, mobile=False):
    o = webdriver.ChromeOptions()
    for a in ("--headless=new", f"--window-size={width},{height}", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--log-level=3", "--mute-audio"):
        o.add_argument(a)
    if mobile:
        o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": width, "height": height, "pixelRatio": 2.0},
                                                      "userAgent": "Mozilla/5.0 (Linux; Android 14; SM-A000X) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36"})
    o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return webdriver.Chrome(options=o)


def wait(d, js, timeout=20, what=""):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if d.execute_script("return (" + js + ")"):
                return True
        except Exception:
            pass
        time.sleep(0.15)
    raise TimeoutError("waited for: " + (what or js))


def txt(d, sel):
    return d.execute_script("const e = document.querySelector(arguments[0]); return e ? (e.innerText ?? e.textContent) : ''", sel)


def count(d, sel):
    return d.execute_script("return document.querySelectorAll(arguments[0]).length", sel)


def login(d):
    d.get(URL)
    wait(d, "document.getElementById('login').classList.contains('on')")
    d.find_element(By.ID, "pw").send_keys(PW + Keys.ENTER)
    wait(d, "!document.getElementById('login').classList.contains('on') && document.getElementById('nettxt').textContent === 'JARVIS Online'")


def shot(d, name):
    h = d.execute_script("return document.documentElement.scrollHeight")
    w = d.execute_script("return window.innerWidth")
    d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": w, "height": min(h, 4000), "deviceScaleFactor": 1, "mobile": False})
    d.save_screenshot(OUT + "/" + name)
    d.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})


d = browser(1400, 900)
try:
    d.get(URL)
    wait(d, "document.getElementById('login').classList.contains('on')")
    check("login screen is shown first, nothing behind it is filled", txt(d, "#msgs").strip() == "" and txt(d, "#wnow") == "--")
    d.find_element(By.ID, "pw").send_keys("wrong-password" + Keys.ENTER)
    wait(d, "document.getElementById('loginerr').textContent.length > 0")
    check("wrong password is refused with a message", "Wrong" in txt(d, "#loginerr"), txt(d, "#loginerr"))
    d.find_element(By.ID, "pw").clear()
    d.find_element(By.ID, "pw").send_keys(PW + Keys.ENTER)
    wait(d, "!document.getElementById('login').classList.contains('on') && document.getElementById('nettxt').textContent === 'JARVIS Online'")
    check("status shows JARVIS Online", True)
    wait(d, "document.querySelectorAll('#msgs .msg').length > 0", 10)
    n0 = count(d, "#msgs .msg")
    check("earlier chat history is back after login (kept on the server)", n0 >= 6, f"{n0} messages")

    nj = count(d, "#msgs .msg.jarvis")
    d.execute_script("window.__typed = false; new MutationObserver(() => { if (document.getElementById('typing').classList.contains('on')) window.__typed = true; })"
                     ".observe(document.getElementById('typing'), {attributes: true})")          # the indicator can be shorter than one look from outside
    d.find_element(By.ID, "text").send_keys("what day is it" + Keys.ENTER)
    wait(d, f"document.querySelectorAll('#msgs .msg.jarvis').length > {nj}", 60)
    check("typing indicator while JARVIS works", d.execute_script("return window.__typed"))
    last = d.execute_script("const m = document.querySelectorAll('#msgs .msg.jarvis'); return m[m.length - 1].innerText")
    check("Enter sent the message and JARVIS's real answer arrived", "2026" in last or "day" in last.lower(), last.replace("\n", " ")[:90])
    d.refresh()
    wait(d, "document.querySelectorAll('#msgs .msg').length > 0", 10)
    check("history survives a page refresh", count(d, "#msgs .msg") >= n0 + 2, f"{count(d, '#msgs .msg')} messages")

    wait(d, "document.getElementById('wnow').textContent !== '--' && document.getElementById('tcpu').textContent !== '--'", 25)
    check("weather card filled", "°C" in txt(d, "#wnow"), f"{txt(d, '#wcity')} {txt(d, '#wnow')} {txt(d, '#wdesc')}")
    check("system rings filled", txt(d, "#tcpu").endswith("%") and txt(d, "#tram").endswith("%"), f"CPU {txt(d, '#tcpu')} RAM {txt(d, '#tram')} BAT {txt(d, '#tbat')}")
    check("devices card filled", txt(d, "#dphone") != "--", f"{txt(d, '#dphone')} / {txt(d, '#dpbat')} / laptop {txt(d, '#dlbat')}")
    for card in ("weather", "devices", "system"):
        d.find_element(By.CSS_SELECTOR, f".card[data-card={card}]").click()
        wait(d, "document.querySelectorAll('#mbody tr').length > 2", 25)
        check(f"click on the {card} card opens its details", True, txt(d, "#mtitle") + ": " + txt(d, "#mbody tr").replace("\t", " = ").replace("\n", " "))
        d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        wait(d, "!document.getElementById('modal').classList.contains('on')")
    fx = d.execute_script("const c = document.getElementById('bgfx'), a = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < a.length; i += 4) if (a[i] > 30) n++;"
                          "return [n, getComputedStyle(document.querySelector('.card')).backdropFilter, document.querySelectorAll('#hub g.node .ic').length, document.querySelectorAll('#notes .item svg').length]")
    check("living background, glass panels and line-art icons are there", fx[0] > 20 and "blur" in fx[1] and fx[2] >= 6, f"{fx[0]} particle pixels, panels {fx[1]}, {fx[2]} hub icons, {fx[3]} notification icons")
    nodes = count(d, "#hub g.node")
    check("hub shows the real connections", nodes >= 6, f"{nodes} nodes, {txt(d, '#conncount')}")
    d.execute_script("document.querySelectorAll('#hub g.node')[1].dispatchEvent(new MouseEvent('click', {bubbles: true}))")
    wait(d, "document.getElementById('modal').classList.contains('on') && document.querySelectorAll('#mbody tr').length >= 2")
    check("click on a hub node opens its details", True, txt(d, "#mtitle") + ": " + txt(d, "#mbody tr:nth-child(2)").replace("\t", " = "))
    d.find_element(By.ID, "mclose").click()
    wait(d, "document.querySelectorAll('#notes .item').length > 0", 15)
    hs = d.execute_script("return [...document.querySelectorAll('#notes .item')].slice(0, 12).map(e => Math.round(e.getBoundingClientRect().height))")
    check("notification rows keep their full height in the scrolling list", min(hs) >= 30, f"row heights {hs[:6]}")
    d.find_element(By.CSS_SELECTOR, "#notes .item").click()
    wait(d, "document.getElementById('modal').classList.contains('on')")
    check("click on a notification opens the full text", len(txt(d, "#mbody")) > 5, f"{count(d, '#notes .item')} notifications")
    d.find_element(By.ID, "mclose").click()
    wait(d, "document.querySelectorAll('#caps .item').length > 0", 15)
    d.find_element(By.CSS_SELECTOR, "#caps .item").click()
    wait(d, "document.querySelectorAll('#mbody tr').length > 4")
    check("click on a capability shows when it was made and its status", "status" in txt(d, "#mbody").lower() and "made" in txt(d, "#mbody").lower(), txt(d, "#mtitle"))
    d.find_element(By.ID, "mclose").click()
    shot(d, "dashboard_desktop.png")
    # ---- the 3D reactor: a real WebGL scene that keeps rendering and follows data-s
    wait(d, "window.__orb3d && window.__orb3d.frames > 5", 25, "the WebGL scene to start")
    g = d.execute_script("const c = document.getElementById('orb3d'), r = c.getBoundingClientRect(), o = document.getElementById('orb').getBoundingClientRect();"
                         "return [c.width > 0, window.__orb3d.rings, window.__orb3d.dots, window.__orb3d.version, Math.round(r.width), Math.round(o.width), document.getElementById('orb').classList.contains('gl')]")
    check("orb is a real WebGL scene (Three.js) in the same container", g[0] and g[1] == 4 and g[2] >= 4 and g[6], f"three.js r{g[3]}, {g[1]} torus rings, {g[2]} orbiting spheres, canvas {g[4]}px around a {g[5]}px orb")
    f1 = d.execute_script("return window.__orb3d.frames"); time.sleep(1.0); f2 = d.execute_script("return window.__orb3d.frames")
    check("it renders continuously (requestAnimationFrame)", f2 - f1 >= 8, f"{f2 - f1} frames in one second")
    import io
    from PIL import Image, ImageChops
    def orb_image():
        return Image.open(io.BytesIO(d.find_element(By.ID, "orb").screenshot_as_png)).convert("RGB")
    im1 = orb_image(); time.sleep(0.7); im2 = orb_image()
    lit = sum(1 for p in im1.resize((64, 64)).getdata() if max(p) > 90)
    moved = sum(1 for p in ImageChops.difference(im1, im2).resize((64, 64)).getdata() if max(p) > 25)
    check("the scene really draws (lit pixels) and really moves between two moments", lit > 150 and moved > 20, f"{lit} of 4096 sampled pixels lit, {moved} changed after 0.7 s")
    im1.save(OUT + "/orb3d.png")
    arc = d.execute_script("return getComputedStyle(document.getElementById('rcpu')).stroke")
    check("the CPU ring's value arc is drawn in its own colour", "0, 229, 255" in arc, arc)

    d.find_element(By.ID, "orb").click()
    wait(d, "document.getElementById('orb').dataset.s === 'LISTENING'", 8)
    check("orb click starts listening (state LISTENING, mic button red)", "rec" in d.find_element(By.ID, "mic").get_attribute("class"))
    rp = d.execute_script("return [window.__ripples || 0, document.getElementById('app').classList.contains('ripple'), getComputedStyle(document.getElementById('app')).getPropertyValue('--rc')]")
    check("state change sends a ripple over the panels in the state's colour", rp[0] >= 1 and rp[1], f"{rp[0]} ripple(s), colour {rp[2].strip()}")
    wait(d, "window.__orb3d && window.__orb3d.state === 'LISTENING'", 5, "the 3D core to follow LISTENING")
    check("the 3D core follows the state (LISTENING)", True)
    time.sleep(1.5)
    d.find_element(By.ID, "orb").click()
    wait(d, "document.getElementById('orb').dataset.s !== 'LISTENING'", 8)
    check("second orb click stops listening and sends the recording", True)
    wait(d, "document.querySelectorAll('.toast').length > 0", 25)
    toasts = " | ".join(d.execute_script("return [...document.querySelectorAll('.toast')].map(t => t.textContent)"))
    check("the recording reached JARVIS's speech-to-text (a clear message either way)", toasts != "", toasts[:140])
    wait(d, "!document.getElementById('send').disabled", 90)

    if PHASE == "all":
        before = count(d, "#act div")
        d.find_element(By.ID, "text").send_keys("add a feature that tells me a random fun fact about space")
        d.find_element(By.ID, "send").click()
        seen, end = set(), time.time() + 170
        while time.time() < end:
            seen.add(d.find_element(By.ID, "orb").get_attribute("data-s"))
            if "SUCCESS" in txt(d, "#act").split("New capability requested")[-1] and count(d, "#act div") > before and not d.execute_script("return document.getElementById('typing').classList.contains('on')"):
                break
            time.sleep(0.12)
        act = txt(d, "#act").split("New capability requested")[-1]
        check("live activity shows the build steps", "Creating capabilities/" in act and "self-test" in act, " / ".join(x.split("  ")[-1][:46] for x in act.splitlines()[-5:]))
        check("orb went through the build states", bool({"CODING", "TESTING", "SUCCESS", "THINKING"} & seen), ", ".join(sorted(s for s in seen if s)))
        check("the steps are also in the chat as SYSTEM lines", count(d, "#msgs .msg.system") >= 3, f"{count(d, '#msgs .msg.system')} system lines")
        time.sleep(2)
        last2 = [t.replace("\n", " ")[:120] for t in d.execute_script("return [...document.querySelectorAll('#msgs .msg.jarvis')].slice(-2).map(m => m.innerText)")]
        check("JARVIS built it and ran it at once", any("Done. I can now do" in t for t in last2), " || ".join(last2))
        shot(d, "dashboard_after_build.png")

    d.execute_cdp_cmd("Network.enable", {})
    d.execute_cdp_cmd("Network.setBlockedURLs", {"urls": ["*/api/*"]})
    wait(d, "document.getElementById('nettxt').textContent === 'JARVIS Offline'", 20)
    check("backend gone: status JARVIS Offline, orb OFFLINE, a toast, the page stays usable",
          d.find_element(By.ID, "orb").get_attribute("data-s") == "OFFLINE" and count(d, ".toast") > 0 and d.find_element(By.ID, "text").is_displayed())
    d.execute_cdp_cmd("Network.setBlockedURLs", {"urls": []})
    wait(d, "document.getElementById('nettxt').textContent === 'JARVIS Online'", 20)
    check("backend back: status returns to Online by itself", True)
    errs = [e["message"] for e in d.get_log("browser") if e["level"] == "SEVERE" and "401" not in e["message"] and "ERR_BLOCKED_BY_CLIENT" not in e["message"] and "403" not in e["message"]]
    check("no JavaScript errors in the page", not errs, "; ".join(errs)[:220])
finally:
    d.quit()

m = browser(390, 844, mobile=True)
try:
    login(m)
    wait(m, "document.querySelectorAll('#msgs .msg').length > 0 && document.getElementById('tcpu').textContent !== '--'", 25)
    overflow = m.execute_script("return document.documentElement.scrollWidth - window.innerWidth")
    check("phone width (390 px): no sideways scrolling", overflow <= 1, f"overflow {overflow}px")
    check("phone width: chat box and send button are visible", m.find_element(By.ID, "text").is_displayed() and m.find_element(By.ID, "send").is_displayed())
    nj = count(m, "#msgs .msg.jarvis")
    m.find_element(By.ID, "text").send_keys("what time is it")
    m.find_element(By.ID, "send").click()
    wait(m, f"document.querySelectorAll('#msgs .msg.jarvis').length > {nj}", 60)
    check("phone width: a command works", True, m.execute_script("const x = document.querySelectorAll('#msgs .msg.jarvis'); return x[x.length - 1].innerText").replace("\n", " ")[:70])
    m.find_element(By.CSS_SELECTOR, ".card[data-card=devices]").click()
    wait(m, "document.querySelectorAll('#mbody tr').length > 2", 25)
    box = m.execute_script("const r = document.querySelector('#modal .box').getBoundingClientRect(); return [r.left, r.right, window.innerWidth]")
    check("phone width: the detail window fits the screen", box[0] >= 0 and box[1] <= box[2] + 1, f"{box[0]:.0f}..{box[1]:.0f} of {box[2]}")
    m.find_element(By.ID, "mclose").click()
    shot(m, "dashboard_phone.png")
finally:
    m.quit()
print("U: ALL PASS" if ok_all else "U: SOME FAILED")
