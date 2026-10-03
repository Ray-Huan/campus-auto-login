# -*- coding: utf-8 -*-
"""Campus network auto-login daemon.

Polls network status every few seconds; when the machine is offline but the
portal is reachable, it signs in through the system Edge browser, then closes
the portal page that Windows popped up. Credentials and portal URL are read
from ``config.ini`` (see ``config.example.ini``).

Usage:
    python campus_login.py            # run as a background daemon
    python campus_login.py --once     # single attempt, then exit
"""
import configparser
import ctypes
import logging
import re
import socket
import sys
import time
import urllib.request
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.ini"
LOG_DIR = BASE_DIR / "logs"
LOCK_PORT = 56801

log = logging.getLogger("campus_login")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_config() -> dict:
    cfg = {
        "username": "",
        "password": "",
        "isp": "",
        "portal_url": "http://192.168.0.101/",
    }
    if not CONFIG_FILE.exists():
        log.error(
            "config.ini not found: copy config.example.ini to config.ini "
            "and fill in your account."
        )
        return cfg
    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_FILE, encoding="utf-8")
        cfg["username"] = parser.get("account", "username", fallback="").strip()
        cfg["password"] = parser.get("account", "password", fallback="").strip()
        cfg["isp"] = parser.get("account", "isp", fallback="").strip()
        cfg["portal_url"] = parser.get(
            "portal", "url", fallback="http://192.168.0.101/"
        ).strip()
    except Exception as exc:
        log.error("failed to parse config.ini: %r", exc)
    return cfg


def _portal_host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or "192.168.0.101"


# --------------------------------------------------------------------------
# Network probing
# --------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # being redirected to the portal means offline


_opener = urllib.request.build_opener(
    _NoRedirect, urllib.request.ProxyHandler({})  # ignore any env proxy
)


def is_online(timeout: float = 4.0) -> bool:
    """True when the NCSI probe file is reachable (authenticated access)."""
    try:
        req = urllib.request.Request(
            "http://www.msftconnecttest.com/connecttest.txt",
            headers={"User-Agent": "Microsoft NCSI", "Cache-Control": "no-cache"},
        )
        with _opener.open(req, timeout=timeout) as resp:
            body = resp.read(128).decode("utf-8", "ignore")
            return resp.status == 200 and "Microsoft Connect Test" in body
    except Exception:
        return False


def portal_reachable(host: str, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, 80), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Close the portal window that Windows popped up (no child process, no focus steal)
# --------------------------------------------------------------------------
_WM_CLOSE = 0x0010


def close_login_windows():
    """Close visible top-level windows whose title contains '上网登录'."""
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if "上网登录" in buf.value:
            user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        return True

    try:
        user32.EnumWindows(cb, 0)
    except Exception as exc:
        log.warning("close_login_windows failed: %r", exc)


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------
def do_login(portal_url: str, cfg: dict, headless: bool = True,
             timeout_seconds: int = 15) -> dict:
    """Open the portal and sign in.

    Returns ``{"state": "ok" | "logged_in" | "fail", "detail": str}`` where
    ``logged_in`` means the portal already shows the logout page (already
    authenticated), so nothing needs to be done.
    """
    from playwright.sync_api import sync_playwright  # defer heavy import

    result = {"state": "fail", "detail": ""}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=headless)
        except Exception:
            try:
                browser = p.chromium.launch(
                    executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    headless=headless,
                )
            except Exception as exc:
                return {"state": "fail", "detail": f"cannot launch Edge: {exc!r}"}

        page = browser.new_page()
        alerts = []
        page.on("dialog", lambda d: (alerts.append(str(d.message)), d.dismiss()))
        try:
            page.goto(portal_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)

            def visible_in_any_frame(selector):
                for f in list(page.frames):
                    try:
                        for el in f.query_selector_all(selector):
                            try:
                                if el.is_visible():
                                    return el
                            except Exception:
                                pass
                    except Exception:
                        pass
                return None

            def wait_find(selectors, timeout=10.0):
                deadline = time.time() + timeout
                while time.time() < deadline:
                    for sel in selectors:
                        el = visible_in_any_frame(sel)
                        if el:
                            return el
                    page.wait_for_timeout(400)
                return None

            def has_logout_btn():
                for f in list(page.frames):
                    try:
                        for el in f.query_selector_all(
                                "input[type=button],input[type=submit],button,a"):
                            txt = (el.evaluate(
                                "e=>(e.innerText||e.value||e.textContent||'')"
                            ) or "").strip()
                            if any(k in txt for k in ("注销", "下线", "logout")):
                                return True
                    except Exception:
                        pass
                return False

            # Already authenticated? The portal shows a logout page (no password box).
            pwd_el = wait_find(['input[type="password"]'], timeout=5)
            if pwd_el is None and has_logout_btn():
                result["state"] = "logged_in"
                result["detail"] = "portal shows logout page (already authenticated)"
                return result

            user_el = wait_find([
                'input[type="text"][placeholder*="1"]',
                'input[type="tel"]',
                'input[type="text"]',
                'input:not([type])',
            ])
            if pwd_el is None:
                pwd_el = wait_find(['input[type="password"]'])
            if not (user_el and pwd_el):
                result["detail"] = (
                    f"login form not found title={page.title()!r} frames={len(page.frames)}"
                )
                return result
            user_el.fill(cfg["username"])
            pwd_el.fill(cfg["password"])

            sel_el = wait_find(["select"], timeout=5)
            if sel_el:
                kw = cfg.get("isp", "")
                val = sel_el.evaluate(
                    """(sel, kw) => {
                        const norm = s => (s||'').replace(/\\s+/g,'');
                        if (kw) {
                            for (const o of sel.options)
                                if (norm(o.text).includes(norm(kw))) return o.value;
                            const short = norm(kw).replace('中国','');
                            for (const o of sel.options)
                                if (short && norm(o.text).includes(short)) return o.value;
                        }
                        return null;
                    }""",
                    kw,
                )
                if val in (None, ""):
                    opts = sel_el.evaluate(
                        "sel => Array.from(sel.options).map(o => o.text.trim())"
                    )
                    result["detail"] = f"ISP '{kw}' not found; options={opts}"
                    return result
                sel_el.select_option(val)

            cb = wait_find(['input[type="checkbox"]'], timeout=3)
            if cb:
                try:
                    if not cb.is_checked():
                        cb.check(timeout=3000)
                except Exception:
                    try:
                        cb.evaluate("e => { if (!e.checked) e.click(); }")
                    except Exception:
                        pass

            btn = None
            deadline = time.time() + 8
            btn_sels = ["button", 'input[type="submit"]', 'input[type="button"]',
                        "a[onclick]", "div[onclick]"]
            while time.time() < deadline and btn is None:
                for f in list(page.frames):
                    for sel in btn_sels:
                        for el in f.query_selector_all(sel):
                            try:
                                if not el.is_visible():
                                    continue
                                txt = (el.evaluate(
                                    "e => (e.innerText||e.value||e.textContent||'')"
                                ) or "").replace(" ", "").replace("\u3000", "")
                                if "登录" in txt:
                                    btn = el
                                    break
                            except Exception:
                                pass
                        if btn:
                            break
                    if btn:
                        break
                if btn is None:
                    page.wait_for_timeout(400)
            if btn is None:
                result["detail"] = "login button not found"
                return result
            try:
                btn.click(timeout=3000)
            except Exception:
                btn.evaluate("e => e.click()")
            log.info("clicked login, waiting for confirmation...")

            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                if is_online():
                    result["state"] = "ok"
                    break
                time.sleep(2)
            if result["state"] != "ok":
                result["detail"] = (
                    f"no connectivity within {timeout_seconds}s (dialogs={alerts})"
                )
            return result
        except Exception as exc:
            result["detail"] = f"login error: {exc!r}"
            return result
        finally:
            try:
                browser.close()
            except Exception:
                pass


def dump_portal_structure(portal_url: str):
    """Dump the portal DOM on failure (diagnostics)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(channel="msedge", headless=True)
            pg = b.new_page()
            pg.goto(portal_url, wait_until="domcontentloaded", timeout=12000)
            pg.wait_for_timeout(1200)
            log.info("[structure] title=%r url=%r frames=%d",
                     pg.title(), pg.url, len(pg.frames))
            for i, f in enumerate(pg.frames):
                for el in f.query_selector_all("input"):
                    log.info("[structure] frame%d input %s", i, el.evaluate(
                        "e=>JSON.stringify({t:e.type||'',n:e.name||'',id:e.id||'',"
                        "p:e.placeholder||''})"))
                for el in f.query_selector_all("select"):
                    log.info("[structure] frame%d select %s", i, el.evaluate(
                        "e=>JSON.stringify({n:e.name||'',opts:Array.from(e.options)"
                        ".map(o=>o.text.trim())})"))
                for el in f.query_selector_all("button,input[type=submit],a"):
                    txt = el.evaluate("e=>(e.innerText||e.value||e.textContent||'').trim()")
                    if txt:
                        log.info("[structure] frame%d btn=%r", i, txt[:30])
            b.close()
    except Exception as exc:
        log.warning("[structure] dump failed: %r", exc)


# --------------------------------------------------------------------------
# Daemon
# --------------------------------------------------------------------------
_lock_sock = None


def acquire_lock() -> bool:
    global _lock_sock
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", LOCK_PORT))
        _lock_sock = s
        return True
    except OSError:
        return False


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(LOG_DIR / "campus_login.log",
                             maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def main(once: bool = False):
    setup_logging()
    if not acquire_lock():
        log.info("another instance is running; exiting")
        return
    cfg = load_config()
    if not cfg["username"]:
        log.error("no username in %s; exiting", CONFIG_FILE)
        return
    host = _portal_host(cfg["portal_url"])
    log.info("started: portal=%s isp=%s", cfg["portal_url"], cfg["isp"] or "-")

    fail_count = 0
    next_attempt = 0.0
    while True:
        try:
            if is_online():
                fail_count = 0
                close_login_windows()
                if once:
                    log.info("already online; exiting")
                    return
                time.sleep(5)
                continue

            if portal_reachable(host):
                now = time.time()
                if now < next_attempt:
                    time.sleep(5)
                    continue
                log.info("offline and portal reachable; attempting login")
                close_login_windows()

                r = do_login(cfg["portal_url"], cfg, headless=True)
                if r["state"] != "ok":
                    log.warning("headless failed (%s); retrying headed", r["detail"])
                    r = do_login(cfg["portal_url"], cfg, headless=False)

                if r["state"] == "ok":
                    log.info("login succeeded")
                    fail_count = 0
                    next_attempt = now + 300
                    close_login_windows()
                    if once:
                        return
                elif r["state"] == "logged_in":
                    log.info("already authenticated; skipping (%s)", r["detail"])
                    fail_count = 0
                    next_attempt = now + 300
                    close_login_windows()
                    if once:
                        return
                else:
                    fail_count += 1
                    backoff = min(60 * fail_count, 600)
                    next_attempt = now + backoff
                    log.error("login failed: %s (retry in %ds)", r["detail"], backoff)
                    dump_portal_structure(cfg["portal_url"])
                    if once:
                        return
                continue

            if once:
                log.info("portal unreachable; exiting")
                return
            time.sleep(5)
        except KeyboardInterrupt:
            log.info("stopped")
            return
        except Exception as exc:
            fail_count += 1
            log.error("unexpected error: %r", exc)
            time.sleep(min(10 * fail_count, 60))


if __name__ == "__main__":
    main(once="--once" in sys.argv)
