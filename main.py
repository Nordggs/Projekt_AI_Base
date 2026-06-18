import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time as time_module
import urllib.request
from datetime import datetime
from pathlib import Path

import webview

from core.logger import LogBuffer
from exporters.base import Browser
from exporters.deepseek import DeepSeekExporter
from exporters.gemini_extract import (
    extract_gemini_dom,
    extract_gemini_rpc,
    discover_gemini_sidebar_urls,
)
from exporters.playwright_browser import BrowserPlaywright
from exporters.writer import ExportWriter


MAX_LOGIN_SOFT = 600
MAX_LOGIN_HARD = 1800


class API:
    def __init__(self, app):
        self._app = app

    def add_account(self, url):
        return self._app.add_account(url)

    def sync_provider(self, urls_json):
        return self._app.sync_provider(urls_json)

    def reconnect(self):
        return self._app.reconnect()

    def sync_all(self, urls_json="[]"):
        self._app.log.add("[INFO] sync_all triggered")
        if self._app.pw:
            self._app.sync_provider(urls_json)

    def add_gemini_account(self, url):
        return self._app.add_gemini_account(url)

    def sync_gemini(self, urls_json):
        return self._app.sync_gemini(urls_json)

    def reconnect_gemini(self):
        return self._app.reconnect_gemini()

    def launch_chrome(self):
        return self._app.launch_chrome_cdp()

    def save_ui_snapshot(self, content):
        threading.Thread(
            target=self._app.save_ui_snapshot,
            args=(content,),
            daemon=True
        ).start()
        return "OK"

    def get_log(self):
        return self._app.log.get()

    def save_log(self):
        os.makedirs("logs", exist_ok=True)
        path = f"logs/debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        lines = self._app.log.get().splitlines()
        trimmed = "\n".join(lines[-200:])
        with open(path, "w", encoding="utf-8") as f:
            f.write(trimmed)
        return path


def _check_cdp_alive():
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1) as r:
            return "webSocketDebuggerUrl" in json.load(r)
    except Exception:
        return False


class App:
    def __init__(self):
        self.api = API(self)
        self.window = webview.create_window(
            "AI Chat Saver",
            url="ui/app.html",
            js_api=self.api,
            width=1300,
            height=750,
        )
        self.log = LogBuffer()
        self.writer = ExportWriter()
        self.pw = None
        self._auto_reconnecting = False
        self._seen_urls = set()

        # Playwright actor: single-thread queue
        self._pw_queue = queue.Queue()
        self._connect_done = threading.Event()
        self._connect_error = None
        self._last_watched_url = ""
        self._export_active = False
        self._output_folders = {
            "deepseek": "raw/deepseek",
            "chatgpt": "raw/chatgpt",
            "gemini": "raw/gemini",
            "claude": "raw/claude",
        }

        threading.Thread(target=self._pw_worker, daemon=True).start()

        # Gemini worker (isolated single-owner)
        self.gemini_pw = None
        self.gemini_page = None
        self._gemini_playwright = None
        self._gw_queue = queue.Queue()
        self._connect_gemini_done = threading.Event()
        self._gemini_export_active = False
        self._gemini_writer = None
        threading.Thread(target=self._gw_worker, daemon=True).start()

        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.ui_log_path = f"logs/ui_session_{self.session_id}.log"
        self.ui_snapshot_path = f"logs/ui_snapshot_{self.session_id}.log"
        os.makedirs("logs", exist_ok=True)
        open(self.ui_log_path, "a").close()
        open(self.ui_snapshot_path, "a").close()

        if os.path.exists(".cookies/playwright"):
            self.log.add("[INFO] Saved cookies found, will auto-restore session...")
            t = threading.Thread(target=self._try_restore_session, daemon=True)
            t.start()



    # ── Login detection ──

    def _is_logged_in(self, page):
        try:
            return page.evaluate("""
                () => {
                    if (document.readyState !== "complete") return false;
                    if (location.href.includes("login")) return false;
                    const hasMessages = document.querySelectorAll('.ds-message').length > 0;
                    const hasInput = document.querySelector('textarea, [contenteditable="true"]') !== null;
                    const hasDS = document.querySelector('.ds-scroll-area, .the-header') !== null;
                    return hasMessages || hasInput || hasDS;
                }
            """)
        except Exception:
            return False

    # ── Page resolver (find sidebar page, not export tab) ──

    def _main_page(self):
        try:
            if not self.pw:
                return None
            for p in self.pw._context.pages:
                try:
                    url = p.url
                    if "chat.deepseek.com" in url and "/chat/s/" not in url:
                        return p
                except Exception:
                    continue
            return self.pw.page if hasattr(self.pw, "page") else None
        except Exception:
            return None

    # ── Playwright worker (single thread, sole owner of self.pw) ──

    def _pw_worker(self):
        while True:
            try:
                cmd, arg = self._pw_queue.get(timeout=1)
            except queue.Empty:
                self._watch_url()
                continue

            try:
                if cmd == "connect":       self._do_connect(arg)
                elif cmd == "reconnect":   self._do_reconnect()
                elif cmd == "export_batch":
                    try:
                        self._do_export_batch(arg)
                    finally:
                        self._export_active = False
            except Exception as e:
                self.log.add(f"[ERROR] _pw_worker cmd={cmd}: {e}")
                self._push_log(f"ERR: {e}")
                if cmd in ("connect", "reconnect"):
                    self._connect_error = str(e)
                    self._connect_done.set()

            self._watch_url()

    # ── Gemini worker (isolated single-owner) ──

    def _gw_worker(self):
        from playwright.sync_api import sync_playwright
        self._gemini_playwright = sync_playwright().start()
        while True:
            try:
                cmd, arg = self._gw_queue.get(timeout=1)
            except queue.Empty:
                continue

            try:
                if cmd == "connect_gemini":      self._do_connect_gemini(arg)
                elif cmd == "export_gemini_batch":
                    try:
                        self._do_export_gemini_batch(arg)
                    finally:
                        self._gemini_export_active = False
            except Exception as e:
                self.log.add(f"[ERROR] _gw_worker cmd={cmd}: {e}")
                self._push_log(f"Gemini ERR: {e}")
                if cmd == "connect_gemini":
                    self._connect_gemini_done.set()

    def _do_connect(self, url):
        self.log.add("[INFO] Connecting DeepSeek account...")
        self.log_ui_event("add_account start")

        if self.pw:
            self.pw.close()
            self.pw = None

        pw = BrowserPlaywright(log=self.log, headless=False)
        pw.start()
        pw.page.goto(url, wait_until="domcontentloaded", timeout=30000)

        start = time_module.time()
        while True:
            if self._is_logged_in(pw.page):
                self.log.add("[INFO] DeepSeek account connected")
                self.log_ui_event("add_account connected")
                self.pw = pw
                self._connect_error = None
                self._connect_done.set()
                self._seen_urls.clear()
                self._last_watched_url = ""
                try:
                    self.window.evaluate_js("setConnected()")
                except Exception:
                    pass
                return

            elapsed = time_module.time() - start

            if elapsed > MAX_LOGIN_HARD:
                pw.close()
                self._connect_error = f"Login timeout ({MAX_LOGIN_HARD // 60} min)"
                self._connect_done.set()
                raise RuntimeError(f"Login timeout ({MAX_LOGIN_HARD // 60} min)")

            if elapsed > MAX_LOGIN_SOFT and int(elapsed) % 10 == 0:
                self.log.add(f"[WARN] Login taking longer than expected ({int(elapsed)}s)")

            try:
                self.window.evaluate_js(f"setWaiting({int(elapsed)})")
            except Exception:
                pass

            time_module.sleep(2)

    def _do_reconnect(self):
        self.log.add("[INFO] Reconnecting DeepSeek...")
        self.log_ui_event("reconnect")
        shutil.rmtree(".cookies/playwright", ignore_errors=True)
        if self.pw:
            self.pw.close()
            self.pw = None
        self._do_connect("https://chat.deepseek.com/")

    # ── Gemini connection (isolated single-owner) ──

    def _do_connect_gemini(self, arg):
        self.log.add("[INFO] Connecting to Gemini via CDP...")
        if not _check_cdp_alive():
            self.log.add("[ERROR] Chrome CDP not running on 127.0.0.1:9222")
            raise RuntimeError("CDP not available: Chrome not started with --remote-debugging-port=9222")

        if self.gemini_pw:
            try:
                self.gemini_pw.close()
            except Exception:
                pass
            self.gemini_pw = None
            self.gemini_page = None

        try:
            browser = self._gemini_playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
            ctx = browser.contexts[0] if browser.contexts else None
            if not ctx:
                raise RuntimeError("No browser context found in CDP session")
            page = ctx.new_page()
            page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1000)

            self.gemini_pw = browser
            self.gemini_page = page
            self._connect_gemini_done.set()
            try:
                self.window.evaluate_js("setGeminiConnected()")
            except Exception:
                pass
            self.log.add("[INFO] Gemini connected via CDP")
        except Exception as e:
            self.log.add(f"[ERROR] CDP attach failed: {e}")
            raise

    def _ensure_gemini_page(self):
        try:
            if self.gemini_page:
                self.gemini_page.evaluate("1")
                return self.gemini_page
        except Exception:
            pass
        if not self.gemini_pw or not self.gemini_pw.contexts:
            raise RuntimeError("CDP disconnected")
        ctx = self.gemini_pw.contexts[0]
        self.gemini_page = ctx.new_page()
        return self.gemini_page

    def _export_one(self, url):
        self.log.add(f"[INFO] Exporting {url[:60]}...")

        # scroll sidebar until target URL appears in visible DOM
        try:
            mp = self._main_page()
            if mp:
                target_url = url.rstrip("/")
                for _ in range(60):
                    urls = mp.evaluate("""
                        () => [...document.querySelectorAll('a[href*="/chat/s/"]')]
                            .map(a => 'https://chat.deepseek.com' + a.getAttribute('href'))
                    """)
                    if any(target_url in (u or "") for u in (urls or [])):
                        break
                    mp.evaluate("""
                        () => {
                            const c = document.querySelector('.ds-virtual-list');
                            if (c) c.scrollTop += 300;
                        }
                    """)
                    time_module.sleep(0.2)
        except Exception:
            pass

        page = None
        try:
            page = self.pw.new_page()
            page.set_default_timeout(120_000)

            browser = Browser(window=None, mode="playwright", log=self.log)
            browser._playwright_page = page

            result = []
            exporter = DeepSeekExporter(browser, url=url,
                capture_ssr=False, capture_cache=False,
                capture_bundles=False, capture_state=False)
            exporter.start(lambda r: result.append(r))

            data = result[0] if result else None
            if data and "error" not in data:
                path = self.writer.write(data)
                n = len(data.get("messages", []))
                self.log.add(f"[SUCCESS] {n} msgs → {path.name}")
                self._push_log(f"OK: {n} msgs — {data.get('title', '?')}")
                self.log_ui_event(f"export {n} msgs — {path.name}")
                return True
            else:
                err = (data or {}).get("error", "no result")
                self.log.add(f"[ERROR] {err}")
                self._push_log(f"ERR: {err}")
                return False
        except Exception as e:
            self.log.add(f"[ERROR] Export failed: {e}")
            self._push_log(f"ERR: {e}")
            return False
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

    def _do_export_batch(self, urls):
        self.log.add(f"[DEEPSEEK] batch urls={len(urls)}")
        urls = urls or self._discover_sidebar_urls()
        if not urls:
            self.log.add("[ERROR] No DeepSeek chat URLs found")
            self._push_log("ERR: no URLs found")
            return

        self._export_active = True
        self.writer = ExportWriter(out_dir=self._output_folders["deepseek"])
        ok = 0
        for url in urls:
            if self._export_one(url):
                ok += 1
        self.log.add(f"[INFO] Batch done: {ok}/{len(urls)} OK")
        self._push_log(f"Batch done: {ok}/{len(urls)} OK")
        try:
            self.window.evaluate_js("copyLogContent()")
        except Exception:
            pass

    # ── Gemini export (isolated single-owner) ──

    def _goto_gemini(self, url):
        try:
            page = self._ensure_gemini_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return page
        except Exception as e:
            if "closed" in str(e).lower():
                self.log.add("[WARN] Gemini page closed, recreating...")
                page = self._ensure_gemini_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                return page
            raise

    def _export_gemini(self, url):
        self.log.add(f"[INFO] Exporting Gemini {url[:60]}...")

        try:
            page = self._goto_gemini(url)
            page.wait_for_timeout(1500)

            data = extract_gemini_rpc(page, url)
            if not data:
                self.log.add("[INFO] RPC failed, using DOM extraction")
                try:
                    data = extract_gemini_dom(page, url)
                except Exception as e:
                    self.log.add(f"[GEMINI][EXTRACT][FATAL] {e}")
                    return None

            if not data:
                self.log.add("[ERROR] Gemini extraction returned no data")
                self._push_log("ERR: Gemini no data")
                return None

            path = self._gemini_writer.write(data)
            n = len(data.get("messages", []))
            self.log.add(f"[SUCCESS] Gemini {n} msgs → {path.name}")
            self._push_log(f"Gemini OK: {n} msgs — {data.get('title', '?')}")
            return {"ok": True, "path": str(path), "count": n}

        except Exception as e:
            self.log.add(f"[ERROR] Gemini export failed: {e}")
            self._push_log(f"Gemini ERR: {e}")
            return None

    def _do_export_gemini_batch(self, urls):
        self.log.add(f"[GEMINI] batch start user_urls={urls}")
        urls = urls or discover_gemini_sidebar_urls(self.gemini_page)
        if not urls:
            self.log.add("[ERROR] No Gemini chat URLs found")
            self._push_log("Gemini ERR: no URLs found")
            return

        self._gemini_export_active = True
        self._gemini_writer = ExportWriter(out_dir=self._output_folders["gemini"])
        Path(self._output_folders["gemini"]).mkdir(parents=True, exist_ok=True)
        self.log.add(f"[GEMINI] writer exists = {self._gemini_writer is not None}")

        results = []
        for url in urls:
            self.log.add(f"[GEMINI] calling export for {url[:60]}")
            r = self._export_gemini(url)
            if r:
                results.append(r)

        total = sum(r["count"] for r in results) if results else 0
        self.log.add(f"[INFO] Gemini batch done: {total} msgs from {len(results)}/{len(urls)} chats")
        self._push_log(f"Gemini batch: {total} msgs — {len(results)}/{len(urls)}")
        try:
            self.window.evaluate_js("copyLogContent()")
        except Exception:
            pass

    # ── URL watcher (runs in _pw_worker idle loop) ──

    def _watch_url(self):
        if not self.pw:
            return
        try:
            mp = self._main_page()
            if not mp:
                return
            url = mp.evaluate("() => location.href")
            if url == self._last_watched_url:
                return
            self._last_watched_url = url
            self._handle_url(url)
        except Exception:
            pass

    def _handle_url(self, url):
        if url in self._seen_urls:
            return
        if re.match(r"https://chat\.deepseek\.com/a/chat/s/[a-f0-9-]+", url):
            self._seen_urls.add(url)
            try:
                self.window.evaluate_js(f"addChatUrl({json.dumps(url)})")
            except Exception:
                pass
            self.log.add(f"[INFO] Chat URL captured: {url[:60]}...")

    # ── Hybrid discovery (runs in _pw_worker via _export_batch) ──

    def _discover_sidebar_urls(self):
        mp = self._main_page()
        if not mp:
            return []
        try:
            urls = mp.evaluate("""
                () => [...new Set(
                    [...document.querySelectorAll('a[href*="/chat/s/"]')]
                        .map(a => 'https://chat.deepseek.com' + a.getAttribute('href'))
                        .filter(Boolean)
                )]
            """)
            urls = urls or []
            self.log.add(f"[INFO] Sidebar discovery: {len(urls)} URLs")
            for u in urls:
                self.log.add(f"[DEBUG]   {u[:60]}")
            return urls
        except Exception as e:
            self.log.add(f"[WARN] Sidebar discovery failed: {e}")
            return []

    def _discover_all_chat_urls(self):
        mp = self._main_page()
        if not mp:
            return []
        try:
            all_urls = set()
            seen_hashes = set()
            stable = 0
            MAX_STABLE = 5
            MAX_ITER = 50
            SCROLL_STEP = 300

            for i in range(MAX_ITER):
                urls = mp.evaluate("""
                    () => [...new Set(
                        [...document.querySelectorAll('a[href*="/chat/s/"]')]
                            .map(a => 'https://chat.deepseek.com' + a.getAttribute('href'))
                            .filter(Boolean)
                    )]
                """)
                current = set(urls or [])
                if current.issubset(seen_hashes):
                    stable += 1
                    if stable >= MAX_STABLE:
                        break
                else:
                    stable = 0
                    seen_hashes |= current
                    all_urls |= current

                mp.evaluate("""
                    () => {
                        const c = document.querySelector('.ds-virtual-list, [class*="sidebar"]');
                        if (c) c.scrollTop += arguments[0];
                    }
                """, SCROLL_STEP)
                time_module.sleep(0.3)

            result = list(all_urls)
            self.log.add(f"[INFO] Full sidebar discovery: {len(result)} URLs (after {i+1} scrolls)")
            for u in result:
                self.log.add(f"[DEBUG]   {u[:60]}")
            return result
        except Exception as e:
            self.log.add(f"[WARN] Full sidebar discovery failed: {e}")
            return []

    # ── Public API (called from API class, runs on bridge threads) ──

    def add_account(self, url):
        if self._auto_reconnecting:
            return "BUSY"
        self._connect_done.clear()
        self._connect_error = None
        self._pw_queue.put(("connect", url))
        if not self._connect_done.wait(timeout=MAX_LOGIN_HARD):
            raise RuntimeError(f"Login timeout ({MAX_LOGIN_HARD // 60} min)")
        if self._connect_error:
            raise RuntimeError(self._connect_error)
        return "OK"

    def sync_provider(self, urls_json):
        if not self.pw:
            self.log.add("[ERROR] No account connected")
            return "ERR: no account"

        if self._export_active:
            self.log.add("[WARN] Export already in progress")
            return "ERR: export in progress"

        urls = json.loads(urls_json)
        self.log.add(f"[INFO] Enqueuing batch export ({len(urls)} urls from UI)...")
        self._pw_queue.put(("export_batch", urls))
        return "STARTED"

    def reconnect(self):
        self._connect_done.clear()
        self._connect_error = None
        self._pw_queue.put(("reconnect", None))
        if not self._connect_done.wait(timeout=MAX_LOGIN_HARD):
            raise RuntimeError(f"Reconnect timeout ({MAX_LOGIN_HARD // 60} min)")
        if self._connect_error:
            raise RuntimeError(self._connect_error)
        return "OK"

    # ── Gemini public API ──

    def add_gemini_account(self, url):
        if self._auto_reconnecting:
            return "BUSY"
        self._connect_gemini_done.clear()
        self._gw_queue.put(("connect_gemini", url))
        if not self._connect_gemini_done.wait(timeout=30):
            raise RuntimeError("Gemini CDP connection timeout")
        if not self.gemini_pw:
            raise RuntimeError("Gemini CDP connection failed")
        return "OK"

    def sync_gemini(self, urls_json):
        if not self.gemini_pw:
            self.log.add("[ERROR] Gemini not connected")
            raise RuntimeError("Gemini not connected")
        if self._gemini_export_active:
            self.log.add("[WARN] Gemini export already in progress")
            raise RuntimeError("Gemini export already in progress")
        urls = json.loads(urls_json)
        self.log.add(f"[INFO] Enqueuing Gemini batch export ({len(urls)} urls)")
        self._gw_queue.put(("export_gemini_batch", urls))
        return "STARTED"

    def reconnect_gemini(self):
        self.log.add("[INFO] Reconnecting Gemini...")
        if self.gemini_pw:
            try:
                self.gemini_pw.close()
            except Exception:
                pass
            self.gemini_pw = None
            self.gemini_page = None
        self.add_gemini_account("cdp")

    def launch_chrome_cdp(self):
        if _check_cdp_alive():
            self.log.add("[INFO] CDP already active")
            return "ALREADY_RUNNING"

        chrome_dir = os.path.expanduser("~/.ai_pipeline/chrome_gemini")
        os.makedirs(chrome_dir, exist_ok=True)

        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in paths:
            if os.path.exists(p):
                self.log.add(f"[INFO] Launching Chrome from {p}")
                subprocess.Popen([
                    p, "--remote-debugging-port=9222",
                    f"--user-data-dir={chrome_dir}",
                ])
                return "OK"

        raise RuntimeError("Chrome not found in standard paths")

    # ── Session restore on startup ──

    def _try_restore_session(self):
        time_module.sleep(3)
        self._auto_reconnecting = True
        try:
            self.log.add("[INFO] Auto-restoring DeepSeek session...")
            self.window.evaluate_js("autoRestore()")
        finally:
            self._auto_reconnecting = False

    # ── UI logging / snapshots ──

    def log_ui_event(self, event):
        ts = datetime.now().isoformat()
        line = f"[{ts}] EVENT [source=ui] {event}"
        with open(self.ui_log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save_ui_snapshot(self, content):
        ts = datetime.now().isoformat()
        with open(self.ui_snapshot_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== SNAPSHOT START [{ts}] =====\n")
            f.write(content + "\n")
            f.write(f"===== SNAPSHOT END [{ts}] =====\n")
        self.log.add(f"[INFO] snapshot → {self.ui_snapshot_path}")
        self._push_log(f"snapshot → {self.ui_snapshot_path}")

    def _push_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            self.window.evaluate_js(f"pushLog({json.dumps(f'[{ts}] {msg}')})")
        except Exception:
            pass


if __name__ == "__main__":
    app = App()
    webview.start(debug=True)
