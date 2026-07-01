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
    extract_gemini_via_api_intercept,
    discover_gemini_sidebar_urls,
    discover_all_gemini_urls,
)
from exporters.qwen_extract import (
    extract_qwen_hybrid,
)
from adapters.qwen import QwenAdapter
from adapters.chatgpt import ChatGPTAdapter
from adapters.claude import ClaudeAdapter
from adapters.cdp_manager import CDPManager
from exporters.chatgpt_extract import ApiCapture, extract_chatgpt_pipeline
from conversation.validator import validate_all as validate_conversation
from config import CHATGPT_DIAGNOSE
from exporters.claude_extract import extract_claude_hybrid
from exporters.playwright_browser import BrowserPlaywright
from exporters.writer import ExportWriter


MAX_LOGIN_SOFT = 600
MAX_LOGIN_HARD = 1800


class Cancelled(Exception):
    pass


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
        self._app.log.add("[INFO] sync_all triggered (sequential)")
        threading.Thread(target=self._app._do_sync_all, daemon=True).start()
        return "OK"

    def add_gemini_account(self, url):
        return self._app.add_gemini_account(url)

    def sync_gemini(self, urls_json):
        return self._app.sync_gemini(urls_json)

    def reconnect_gemini(self):
        return self._app.reconnect_gemini()

    def launch_chrome(self):
        return self._app.launch_chrome_cdp()

    def get_cdp_status(self):
        return self._app.cdp.state

    def get_providers_status(self):
        a = self._app
        return {
            "gemini": a._gemini_connect_state == "connected",
            "qwen": a._qwen_connected,
            "chatgpt": a._chatgpt_connected,
            "claude": a._claude_connected,
            "deepseek": a.pw is not None,
        }

    def connect_gemini(self):
        return self._app.add_gemini_account("cdp")

    def close_chrome(self):
        self._app._close_cdp_browser()
        return "OK"

    def cancel_all(self):
        a = self._app
        a._cancel_flag = True
        a._cancel_version += 1
        a._soft_stop()
        a.log.add("[INFO] Cancel requested — cooperative stop")

    def connect_qwen(self):
        return self._app.add_qwen_account()

    def sync_qwen(self, urls_json):
        return self._app.sync_qwen(urls_json)

    def connect_chatgpt(self):
        return self._app.add_chatgpt_account()

    def sync_chatgpt(self, urls_json):
        return self._app.sync_chatgpt(urls_json)

    def connect_claude(self):
        return self._app.add_claude_account()

    def sync_claude(self, urls_json):
        return self._app.sync_claude(urls_json)

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
            "AI Chat Exporter",
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
        self._locks = {
            "deepseek": threading.Lock(),
            "gemini": threading.Lock(),
            "qwen": threading.Lock(),
            "chatgpt": threading.Lock(),
            "claude": threading.Lock(),
        }
        self._sync_state = {
            "gemini": "idle", "qwen": "idle",
            "chatgpt": "idle", "claude": "idle", "deepseek": "idle",
        }

        # Playwright actor: single-thread queue
        self._pw_queue = queue.Queue()
        self._connect_done = threading.Event()
        self._connect_error = None
        self._last_watched_url = ""
        self._export_active = False
        self._cancel_flag = False
        self._cancel_version = 0
        self._output_folders = {
            "deepseek": "raw",
            "chatgpt": "raw",
            "gemini": "raw",
            "claude": "raw",
            "qwen": "raw",
        }

        threading.Thread(target=self._pw_worker, daemon=True).start()

        # Gemini + Qwen + ChatGPT + Claude worker (CDP providers)
        self.gemini_page = None
        self._gw_queue = queue.Queue()
        self._connect_gemini_done = threading.Event()
        self._gemini_export_active = False
        self._gemini_writer = None
        self._gemini_connect_lock = False
        self._gemini_connect_state = "idle"  # idle | connecting | connected
        self._gemini_broken = False
        self._gemini_last_fail_time = 0
        self._cdp_lock = threading.Lock()

        # Qwen (CDP page in same browser as Gemini)
        self.qwen_page = None
        self._qwen_connected = False
        self._qwen_connect_lock = False
        self._connect_qwen_done = threading.Event()
        self._qwen_export_active = False
        self._qwen_writer = None
        self._qwen_session_epoch = 0

        # ChatGPT (CDP page in same browser as Gemini)
        self.chatgpt_page = None
        self._chatgpt_connected = False
        self._chatgpt_connect_lock = False
        self._connect_chatgpt_done = threading.Event()
        self._chatgpt_export_active = False
        self._chatgpt_writer = None
        self._chatgpt_session_epoch = 0

        # Claude (CDP page in same browser as Gemini)
        self.claude_page = None
        self._claude_connected = False
        self._claude_connect_lock = False
        self._connect_claude_done = threading.Event()
        self._claude_export_active = False
        self._claude_writer = None
        self._claude_session_epoch = 0

        # CDP Manager — единый Chrome для всех провайдеров
        self.cdp = CDPManager(log=self.log)

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

    def _is_logged_in(self, page, log_reason=False):
        try:
            raw = page.evaluate("""
                () => JSON.stringify({
                    readyState: document.readyState,
                    href: location.href,
                    hasMessages: document.querySelectorAll('.ds-message').length > 0,
                    hasInput: document.querySelector('textarea, [contenteditable="true"]') !== null,
                    hasDS: document.querySelector('.ds-scroll-area, .the-header') !== null,
                    isLogin: location.href.includes('login')
                })
            """)
            info = json.loads(raw)
            if info.get("readyState") != "complete":
                if log_reason:
                    self.log.add(f"[LOGIN] page not ready: {info['readyState']}")
                return False
            if info.get("isLogin"):
                if log_reason:
                    self.log.add(f"[LOGIN] login page: {info['href'][:80]}")
                return False
            logged = info.get("hasMessages") or info.get("hasInput") or info.get("hasDS")
            if not logged and log_reason:
                self.log.add(f"[LOGIN] no signals: {info}")
            return logged
        except Exception as e:
            if log_reason:
                self.log.add(f"[LOGIN] evaluate error: {e}")
            return False

    # ── Cancel infrastructure ──

    def _check_cancel(self):
        if self._cancel_flag:
            raise Cancelled()

    def _soft_stop(self):
        for page in [self.qwen_page, self.gemini_page, self.chatgpt_page, self.claude_page, self.pw.page if self.pw else None]:
            try:
                if page:
                    page.evaluate("window.stop()")
            except Exception:
                pass

    def pw_call(self, fn, *args, timeout=30, **kwargs):
        version = self._cancel_version
        try:
            result = fn(*args, timeout=timeout * 1000, **kwargs)
        except Exception:
            if self._cancel_flag or version != self._cancel_version:
                raise Cancelled()
            raise
        if self._cancel_flag or version != self._cancel_version:
            raise Cancelled()
        return result

    def _is_page_alive(self, page):
        try:
            page.evaluate("1")
            return True
        except Exception:
            return False

    def _cdp_browser(self):
        if self._gw_browser:
            try:
                self._gw_browser.contexts
                return self._gw_browser
            except Exception:
                self._gw_browser = None
        self._gw_browser = self._gw_pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        return self._gw_browser

    # ── Qwen sidebar navigation helpers ──

    def _click_qwen_chat(self, index):
        """Click the index-th chat in the Qwen sidebar (re-query DOM each time)."""
        self.qwen_page.evaluate("""(i) => {
            const items = document.querySelectorAll('div.chat-item-drag a.chat-item-drag-link');
            items[i]?.click();
        }""", index)

    def _wait_qwen_messages(self, timeout=30000):
        """Wait for messages to appear AND a /c/ URL (post-click invariant)."""
        self.qwen_page.wait_for_function("""() => {
            const msgs = document.querySelectorAll('[class*="message"]');
            return msgs.length > 0 && location.href.includes('/c/');
        }""", timeout=timeout)

    def _load_qwen_sidebar(self):
        """Navigate to chat.qwen.ai/ and wait for sidebar to render. Returns item count."""
        self.qwen_page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=30000)
        self.qwen_page.wait_for_timeout(3000)
        total = self.qwen_page.evaluate("document.querySelectorAll('div.chat-item-drag').length")
        if total < 5:
            self.qwen_page.wait_for_timeout(3000)
            total = self.qwen_page.evaluate("document.querySelectorAll('div.chat-item-drag').length")
        return total

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
        self._gw_pw = sync_playwright().start()
        self._gw_browser = None

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
                elif cmd == "connect_qwen":
                    with self._cdp_lock:
                        self._do_connect_qwen()
                    self._connect_qwen_done.set()
                elif cmd == "export_qwen_batch":
                    try:
                        self._do_export_qwen_batch(arg)
                    finally:
                        self._qwen_export_active = False
                elif cmd == "connect_chatgpt":
                    with self._cdp_lock:
                        self._do_connect_chatgpt()
                    self._connect_chatgpt_done.set()
                elif cmd == "export_chatgpt_batch":
                    try:
                        self._do_export_chatgpt_batch(arg)
                    finally:
                        self._chatgpt_export_active = False
                elif cmd == "connect_claude":
                    with self._cdp_lock:
                        self._do_connect_claude()
                    self._connect_claude_done.set()
                elif cmd == "export_claude_batch":
                    try:
                        self._do_export_claude_batch(arg)
                    finally:
                        self._claude_export_active = False
            except Exception as e:
                self.log.add(f"[ERROR] _gw_worker cmd={cmd}: {e}")
                self._push_log(f"Gemini ERR: {e}")
                if cmd == "connect_gemini":
                    self._connect_gemini_done.set()
                elif cmd == "connect_qwen":
                    self._connect_qwen_done.set()
                elif cmd == "connect_chatgpt":
                    self._connect_chatgpt_done.set()
                elif cmd == "connect_claude":
                    self._connect_claude_done.set()

    def _do_connect(self, url):
        self.log.add("[INFO] Connecting DeepSeek account...")
        self.log_ui_event("add_account start")

        if self.pw:
            self.pw.close()
            self.pw = None

        from playwright.sync_api import sync_playwright
        from adapters.cdp_manager import CDPContext
        self.cdp.start()
        pw_obj = sync_playwright().start()
        ds_browser = pw_obj.chromium.connect_over_cdp("http://127.0.0.1:9222")
        pw = CDPContext(pw_obj, ds_browser)
        pw.page.goto(url, wait_until="domcontentloaded", timeout=30000)

        start = time_module.time()
        _login_attempts = 0
        while True:
            _login_attempts += 1
            if self._is_logged_in(pw.page, log_reason=(_login_attempts == 1)):
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

            hard_limit = 30 if self._auto_reconnecting else MAX_LOGIN_HARD
            if elapsed > hard_limit:
                pw.close()
                if self._auto_reconnecting:
                    self.log.add("[WARN] Auto-restore DeepSeek session timed out (30s)")
                    self._connect_error = "Auto-restore timeout — please login manually"
                else:
                    self._connect_error = f"Login timeout ({MAX_LOGIN_HARD // 60} min)"
                self._connect_done.set()
                raise RuntimeError(self._connect_error)

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
        if self.pw:
            self.pw.close()
            self.pw = None
        self._do_connect("https://chat.deepseek.com/")

    # ── Gemini connection (isolated single-owner) ──

    def _do_connect_gemini(self, arg):
        self.log.add("[INFO] Connecting to Gemini via CDP...")
        if self.cdp.state != "running":
            raise RuntimeError("CDP not running — launch Chrome first")

        if self.gemini_page:
            try:
                self.gemini_page.close()
            except Exception:
                pass
            self.gemini_page = None

        try:
            browser = self._cdp_browser()
            page = browser.contexts[0].new_page()
            page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_load_state("domcontentloaded")

            if not self._is_page_alive(page):
                raise RuntimeError("Page not alive after navigation")

            self.gemini_page = page
            self._gemini_connect_state = "connected"
            self._gemini_broken = False
            self._connect_gemini_done.set()
            try:
                self.window.evaluate_js("setGeminiConnected()")
            except Exception:
                pass
            self.log.add("[INFO] Gemini connected via CDP")
        except Exception as e:
            self._gemini_connect_state = "idle"
            self._gemini_last_fail_time = time_module.time()
            self.log.add(f"[ERROR] CDP attach failed: {e}")
            raise

    def _ensure_gemini_page(self):
        if self.gemini_page and self._is_page_alive(self.gemini_page):
            return self.gemini_page
        browser = self._cdp_browser()
        self.gemini_page = browser.contexts[0].new_page()
        return self.gemini_page

    def _export_one(self, url, chat_order=0):
        self.log.add(f"[INFO] Exporting {url[:60]}...")
        self._check_cancel()

        # scroll sidebar until target URL appears in visible DOM
        try:
            mp = self._main_page()
            if mp:
                target_url = url.rstrip("/")
                for _ in range(60):
                    self._check_cancel()
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

        try:
            page = self.pw.page
            page.set_default_timeout(30_000)

            browser = Browser(window=None, mode="playwright", log=self.log)
            browser._playwright_page = page

            result = []
            exporter = DeepSeekExporter(browser, url=url,
                capture_ssr=False, capture_cache=False,
                capture_bundles=False, capture_state=False)
            exporter.start(lambda r: result.append(r))

            data = result[0] if result else None
            if data and "error" not in data:
                path = self.writer.write(data, chat_order=chat_order)
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

    def _do_export_batch(self, urls):
        if not self._locks["deepseek"].acquire(blocking=False):
            self.log.add("[WARN] DeepSeek sync BUSY")
            self._push_log("DeepSeek BUSY")
            return
        self.set_sync_state("deepseek", "running")
        self.log.add(f"[DEEPSEEK] batch urls={len(urls)}")
        urls = urls or self._discover_sidebar_urls()
        if not urls:
            self.log.add("[ERROR] No DeepSeek chat URLs found")
            self._push_log("ERR: no URLs found")
            self.set_sync_state("deepseek", "failed")
            self._locks["deepseek"].release()
            return

        self._export_active = True
        self.writer = ExportWriter(out_dir=self._output_folders["deepseek"])
        try:
            ok = 0
            for idx, url in enumerate(urls):
                self._check_cancel()
                if self._export_one(url, chat_order=idx):
                    ok += 1
            self.set_sync_state("deepseek", "done")
            self.log.add(f"[INFO] Batch done: {ok}/{len(urls)} OK")
            self._push_log(f"Batch done: {ok}/{len(urls)} OK")
            try:
                self.window.evaluate_js("copyLogContent()")
            except Exception:
                pass
        except Cancelled:
            self.set_sync_state("deepseek", "failed")
            self.log.add("[INFO] Batch cancelled by user")
            self._push_log("⏹ Sync cancelled")
        finally:
            self._cancel_flag = False
            self._cancel_version = 0
            self._export_active = False
            self._locks["deepseek"].release()

    # ── Gemini export (isolated single-owner) ──

    def _export_gemini(self, url, chat_order=0):
        self.log.add(f"[INFO] Exporting Gemini {url[:60]}...")
        self._check_cancel()

        try:
            page = self._ensure_gemini_page()

            # Open sidebar via keyboard shortcut if not already visible
            page.keyboard.press("Control+Shift+h")
            page.wait_for_timeout(2000)

            # Click chat in sidebar (Gemini SPA doesn't support deep-link goto)
            target_url = url.rstrip("/")
            self.log.add("[INFO] Finding chat in sidebar...")
            found = False
            for _ in range(60):
                self._check_cancel()
                # Check if already on the correct page
                current = page.evaluate("location.href.replace(/\\/+$/, '')")
                if current == target_url:
                    found = True
                    break
                # Try clicking the matching sidebar link
                clicked = page.evaluate("""(u) => {
                    const links = document.querySelectorAll('a[href*="/app/"]');
                    for (const a of links) {
                        let h = a.getAttribute('href');
                        if (!h) continue;
                        if (h.startsWith('/')) h = 'https://gemini.google.com' + h;
                        if (h.replace(/\\/+$/, '') === u) {
                            a.click();
                            return true;
                        }
                    }
                    return false;
                }""", target_url)
                if clicked:
                    # Wait for URL to update
                    try:
                        page.wait_for_function("(u) => location.href.replace(/\\/+$/, '') === u", target_url, timeout=10000)
                        page.wait_for_timeout(1000)
                        found = True
                        break
                    except Exception:
                        pass
                # Scroll sidebar to find more links
                page.evaluate("""() => {
                    let el = document.querySelector('.chat-history-scroll-container');
                    if (!el) el = document.querySelector('infinite-scroller, [class*="history"]');
                    if (!el) {
                        const links = document.querySelectorAll('a[href*="/app/"]');
                        if (links.length > 0) {
                            let p = links[0].parentElement;
                            while (p && p !== document.body) {
                                if (p.scrollHeight > p.clientHeight) { el = p; break; }
                                p = p.parentElement;
                            }
                        }
                    }
                    if (!el) el = document.querySelector('nav');
                    if (el) el.scrollTop += 400;
                }""")
                page.wait_for_timeout(300)
            if not found:
                self.log.add(f"[ERROR] Gemini chat {url[:40]} not found in sidebar")
                self._push_log("Gemini ERR: chat not found")
                return None

            page.wait_for_timeout(2000)
            self.log.add(f"[INFO] Gemini chat loaded: {page.evaluate('location.href')[:60]}")

            # Phase 1: RPC via JS fetch (fast, no page reload)
            source_label = "dom"
            data = extract_gemini_rpc(page, url)
            if data:
                source_label = "rpc"
                self.log.add(f"[GEMINI] RPC: {len(data['messages'])} msgs")
            else:
                self.log.add("[INFO] RPC failed, using DOM extraction")

            # Phase 2: DOM scroll (improved, scrolls both directions)
            if not data:
                for retry in range(2):
                    self.log.add(f"[INFO] DOM extraction (scroll) attempt {retry + 1}")
                    try:
                        data = extract_gemini_dom(page, url, log_progress=self._push_log, cancel_check=lambda: self._cancel_flag, cancel_epoch=self._cancel_version)
                    except Exception as e:
                        self.log.add(f"[GEMINI][EXTRACT][FATAL] {e}")
                        data = None
                    if data and data.get("messages"):
                        break
                    if retry == 0:
                        self.log.add("[INFO] DOM returned 0 msgs, trying sidebar click again...")
                        try:
                            page.evaluate("""(u) => {
                                const links = document.querySelectorAll('a[href*="/app/"]');
                                for (const a of links) {
                                    let h = a.getAttribute('href');
                                    if (!h) continue;
                                    if (h.startsWith('/')) h = 'https://gemini.google.com' + h;
                                    if (h.replace(/\\/+$/, '') === u) { a.click(); return true; }
                                }
                                return false;
                            }""", target_url)
                            page.wait_for_timeout(3000)
                        except Exception:
                            pass

            if not data:
                self.log.add("[ERROR] Gemini extraction returned no data")
                self._push_log("Gemini ERR: no data")
                # Navigate back to home page for next chat
                try:
                    page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                return None

            if not data.get("messages"):
                self.log.add("[ERROR] Gemini extraction returned 0 messages, skipping write")
                self._push_log("Gemini ERR: 0 messages")
                try:
                    page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                return None

            path = self._gemini_writer.write(data, chat_order=chat_order)
            if not path:
                self.log.add("[GEMINI][SKIP] writer returned None, no file created")
                self._push_log("Gemini ERR: write failed")
                try:
                    page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                return None

            n = len(data.get("messages", []))
            self.log.add(f"[SUCCESS] Gemini {n} msgs ({source_label}) → {path.name}")
            self._push_log(f"Gemini OK: {n} msgs — {data.get('title', '?')}")

            # Navigate back to main page for next chat
            try:
                page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            return {"ok": True, "path": str(path), "count": n}

        except Exception as e:
            self.log.add(f"[ERROR] Gemini export failed: {e}")
            self._push_log(f"Gemini ERR: {e}")
            return None

    def _do_export_gemini_batch(self, urls):
        if not self._locks["gemini"].acquire(blocking=False):
            self.log.add("[WARN] Gemini sync BUSY")
            self._push_log("Gemini BUSY")
            return
        self.set_sync_state("gemini", "running")
        self.log.add(f"[GEMINI] batch start user_urls={urls}")
        urls = urls or discover_all_gemini_urls(self.gemini_page)
        if not urls:
            self.log.add("[ERROR] No Gemini chat URLs found")
            self._push_log("Gemini ERR: no URLs found")
            self.set_sync_state("gemini", "failed")
            self._locks["gemini"].release()
            return

        self._gemini_export_active = True
        self._gemini_writer = ExportWriter(out_dir=self._output_folders["gemini"])

        try:
            results = []
            for idx, url in enumerate(urls, 1):
                self._check_cancel()

                if self._gemini_broken:
                    break

                if not _check_cdp_alive():
                    self._gemini_broken = True
                    self._push_log("Gemini ERR: CDP disconnected — batch aborted")
                    break

                self.log.add(f"[GEMINI] [{idx}/{len(urls)}] exporting {url[:40]}")
                self._push_log(f"Gemini [{idx}/{len(urls)}]...")
                r = self._export_gemini(url, chat_order=idx - 1)
                if r:
                    results.append(r)
                else:
                    self.log.add(f"[GEMINI] export returned None for {url[:60]}")
                    if not _check_cdp_alive():
                        self._gemini_broken = True
                        self._push_log("Gemini ERR: CDP lost — batch aborted")
                        break

            total = sum(r["count"] for r in results) if results else 0
            self.set_sync_state("gemini", "done")
            self.log.add(f"[INFO] Gemini batch done: {total} msgs from {len(results)}/{len(urls)} chats")
            self._push_log(f"Gemini batch: {total} msgs — {len(results)}/{len(urls)}")
            try:
                self.window.evaluate_js("copyLogContent()")
            except Exception:
                pass
        except Cancelled:
            self.set_sync_state("gemini", "failed")
            self.log.add("[INFO] Gemini batch cancelled by user")
            self._push_log("⏹ Gemini sync cancelled")
        finally:
            self._gemini_export_active = False
            self._cancel_flag = False
            self._cancel_version = 0
            self._locks["gemini"].release()

    # ── Qwen (CDP page in same browser as Gemini) ──

    def _do_connect_qwen(self):
        self.log.add("[INFO] Connecting to Qwen via CDP...")
        if self.cdp.state != "running":
            raise RuntimeError("CDP not running — launch Chrome first")

        if self.qwen_page:
            try:
                self.qwen_page.close()
            except Exception:
                pass
            self.qwen_page = None

        try:
            browser = self._cdp_browser()
            page = browser.contexts[0].new_page()
            page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_load_state("domcontentloaded")

            if not self._is_page_alive(page):
                raise RuntimeError("Page not alive after navigation")

            canonical = "https://chat.qwen.ai/"
            current = page.url.rstrip("/")
            if current != canonical.rstrip("/"):
                self.log.add(f"[QWEN] redirect detected: {current} → restoring canonical")
                with self._cdp_lock:
                    page.goto(canonical, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_load_state("domcontentloaded")

            self.qwen_page = page
            self._qwen_session_epoch += 1
            self._qwen_connected = True
            try:
                self.window.evaluate_js("setQwenConnected()")
            except Exception:
                pass
            self.log.add("[INFO] Qwen connected via CDP")
        except Exception as e:
            self._qwen_connected = False
            self.qwen_page = None
            self.log.add(f"[ERROR] Qwen CDP attach failed: {e}")
            raise

    def ensure_qwen_alive(self):
        if not self.qwen_page:
            self._qwen_connected = False
            return False
        try:
            self.qwen_page.url
            return True
        except Exception:
            self.qwen_page = None
            self._qwen_connected = False
            self._push_log("Qwen page lost — reconnect required")
            return False

    def _export_qwen(self, url, epoch=0, chat_order=0):
        if epoch and self._qwen_session_epoch != epoch:
            self.log.add(f"[EXPORT][RACE] epoch_mismatch expected={epoch} current={self._qwen_session_epoch}")
            return None

        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url

        chat_id = url.rstrip("/").split("/")[-1]
        self.log.add(f"[INFO] Exporting Qwen {url[:60]}...")
        self._check_cancel()

        try:
            # Load chat list (sidebar)
            with self._cdp_lock:
                try:
                    total = self._load_qwen_sidebar()
                except Exception:
                    pass
                current = self.qwen_page.url
                if "/auth" in current or "/login" in current:
                    self.log.add("[WARN] Qwen redirected to auth page, need re-login")
                    self._push_log("Qwen ERR: session expired — re-login required")
                    return None

            if epoch and self._qwen_session_epoch != epoch:
                self.log.add(f"[EXPORT][RACE] epoch_mismatch_after_goto expected={epoch} current={self._qwen_session_epoch}")
                return None

            total = self.qwen_page.evaluate("document.querySelectorAll('div.chat-item-drag').length")
            self.log.add(f"[QWEN] sidebar: {total} items, searching for {chat_id[:12]}")
            self._push_log(f"Qwen search: {chat_id[:12]}")

            found = False
            for i in range(total):
                self._check_cancel()
                if epoch and self._qwen_session_epoch != epoch:
                    return None

                with self._cdp_lock:
                    self._click_qwen_chat(i)
                    self.qwen_page.wait_for_timeout(2000)

                if chat_id in self.qwen_page.url:
                    found = True
                    with self._cdp_lock:
                        self.pw_call(self.qwen_page.wait_for_function, """() => {
                            const msgs = document.querySelectorAll('[class*="message"]');
                            return msgs.length > 0 && location.href.includes('/c/');
                        }""", timeout=30)
                        self.qwen_page.wait_for_timeout(500)
                    self.log.add(f"[QWEN] found chat {chat_id[:12]} at index {i}")
                    break

            if not found:
                self.log.add(f"[ERROR] Qwen chat {chat_id[:12]} not found in sidebar")
                self._push_log("Qwen ERR: chat not found in sidebar")
                return None

            data = extract_qwen_hybrid(self.qwen_page, url, log_progress=self._push_log, cancel_check=lambda: self._cancel_flag, cancel_epoch=self._cancel_version)

            if not data:
                self.log.add("[ERROR] Qwen extraction returned no data")
                self._push_log("ERR: Qwen no data")
                return None

            if not data.get("messages"):
                self.log.add("[ERROR] Qwen extraction returned 0 messages, skipping write")
                self._push_log("Qwen ERR: 0 messages")
                return None

            path = self._qwen_writer.write(data, chat_order=chat_order)
            if not path:
                self._push_log("Qwen ERR: write failed")
                return None

            n = len(data.get("messages", []))
            self.log.add(f"[SUCCESS] Qwen {n} msgs → {path.name}")
            self._push_log(f"Qwen OK: {n} msgs — {data.get('title', '?')}")
            return {"ok": True, "path": str(path), "count": n}

        except Exception as e:
            self.log.add(f"[ERROR] Qwen export failed: {e}")
            self._push_log(f"Qwen ERR: {e}")
            return None

    def add_qwen_account(self):
        if self._qwen_connect_lock:
            return "BUSY"
        if self._qwen_connected:
            return "OK"
        self._qwen_connect_lock = True
        try:
            self._connect_qwen_done.clear()
            self._gw_queue.put(("connect_qwen", ""))
            if not self._connect_qwen_done.wait(timeout=30):
                raise RuntimeError("Qwen CDP connection timeout")
            if not self._qwen_connected:
                raise RuntimeError("Qwen CDP connection failed")
            return "OK"
        finally:
            self._qwen_connect_lock = False

    def sync_qwen(self, urls_json):
        if not self._qwen_connected:
            raise RuntimeError("Qwen not connected")
        if not _check_cdp_alive():
            raise RuntimeError("CDP not available")
        if self._qwen_export_active:
            raise RuntimeError("Qwen export already in progress")
        urls = json.loads(urls_json)
        self.log.add(f"[INFO] Enqueuing Qwen batch export ({len(urls)} urls)")
        self._gw_queue.put(("export_qwen_batch", urls))
        return "STARTED"

    def reconnect_qwen(self):
        self.log.add("[INFO] Reconnecting Qwen...")
        if self.qwen_page:
            try:
                self.qwen_page.close()
            except Exception:
                pass
            self.qwen_page = None
        self._qwen_connected = False
        self.add_qwen_account()

    def _do_export_qwen_batch(self, urls):
        if not self._locks["qwen"].acquire(blocking=False):
            self.log.add("[WARN] Qwen sync BUSY")
            self._push_log("Qwen BUSY")
            return
        self.set_sync_state("qwen", "running")
        self.log.add(f"[QWEN] batch start user_urls={urls}")
        batch_epoch = self._qwen_session_epoch
        self.log.add(f"[BATCH] start epoch={batch_epoch} page_url={self.qwen_page.url}")

        self._qwen_export_active = True
        self._qwen_writer = ExportWriter(out_dir=self._output_folders["qwen"])

        try:
            # ── Sync Selected: user-provided URLs ──
            if urls:
                urls = list(dict.fromkeys(urls))
                if len(urls) == 1:
                    self._push_log("Qwen single-export mode: break on first success")

                results = []
                for idx, url in enumerate(urls, 1):
                    self._check_cancel()
                    if self._qwen_session_epoch != batch_epoch:
                        self.log.add(f"[BATCH][RACE] epoch_mismatch expected={batch_epoch} current={self._qwen_session_epoch}")
                        self._push_log("Qwen ERR: session changed")
                        break
                    if not _check_cdp_alive():
                        self.log.add("[WARN] CDP not available, stopping batch")
                        self._push_log("Qwen ERR: CDP lost")
                        break

                    self.log.add(f"[QWEN] [{idx}/{len(urls)}] exporting {url[:40]}")
                    self._push_log(f"Qwen [{idx}/{len(urls)}]...")
                    r = self._export_qwen(url, epoch=batch_epoch, chat_order=idx - 1)
                    if r:
                        results.append(r)
                        if len(urls) == 1:
                            self.log.add("[QWEN] single-export success, breaking")
                            break
                    else:
                        self.log.add(f"[QWEN] export returned None for {url[:60]}")

                total = sum(r["count"] for r in results) if results else 0
                self.set_sync_state("qwen", "done")
                self.log.add(f"[INFO] Qwen batch done: {total} msgs from {len(results)}/{len(urls)} chats")
                self._push_log(f"Qwen batch: {total} msgs — {len(results)}/{len(urls)}")
                return

            # ── Sync All: QwenAdapter path ──
            adapter = QwenAdapter(
                self.qwen_page, self._cdp_lock,
                self._push_log,
                lambda: self._cancel_flag,
            )
            if not adapter.healthcheck():
                self.log.add("[WARN] Qwen adapter healthcheck failed")
                self._push_log("Qwen ERR: page not available")
                return

            chats = adapter.list_chats()
            self.log.add(f"[QWEN] sidebar: {len(chats)} chats")
            self._push_log(f"Qwen sidebar: {len(chats)} chats")

            if not chats:
                self.log.add("[ERROR] No Qwen chats found in sidebar")
                self._push_log("Qwen ERR: no chats in sidebar")
                return

            results = []

            for chat_idx, chat in enumerate(chats):
                self._check_cancel()
                if self._qwen_session_epoch != batch_epoch:
                    self.log.add(f"[BATCH][RACE] epoch_mismatch expected={batch_epoch} current={self._qwen_session_epoch}")
                    self._push_log("Qwen ERR: session changed")
                    break
                if not _check_cdp_alive():
                    self.log.add("[WARN] CDP not available, stopping batch")
                    self._push_log("Qwen ERR: CDP lost")
                    break

                self._push_log(f"Qwen [{chat_idx+1}/{len(chats)}] {chat['title'][:40]}")
                ok = adapter.open_chat(chat)
                if not ok:
                    self._push_log(f"Qwen [{chat_idx+1}/{len(chats)}] ERR: not found")
                    continue

                record = adapter.extract_chat(chat)
                if record:
                    path = self._qwen_writer.write(record.to_dict(), chat_order=chat_idx)
                    if path:
                        n = len(record.messages)
                        results.append({"ok": True, "path": str(path), "count": n})
                        self.log.add(f"[QWEN] [{chat_idx+1}/{len(chats)}] {n} msgs -> {path.name}")
                        self._push_log(f"Qwen [{chat_idx+1}/{len(chats)}] OK: {n} msgs")
                    else:
                        self._push_log(f"Qwen [{chat_idx+1}/{len(chats)}] ERR: write failed")
                else:
                    self._push_log(f"Qwen [{chat_idx+1}/{len(chats)}] ERR: no data")

            total_msgs = sum(r["count"] for r in results) if results else 0
            self.set_sync_state("qwen", "done")
            self.log.add(f"[INFO] Qwen batch done: {total_msgs} msgs from {len(results)}/{len(chats)} chats")
            self._push_log(f"Qwen batch: {total_msgs} msgs — {len(results)}/{len(chats)}")

            try:
                self.window.evaluate_js("copyLogContent()")
            except Exception:
                pass

        except Cancelled:
            self.set_sync_state("qwen", "failed")
            self.log.add("[INFO] Qwen batch cancelled by user")
            self._push_log("⏹ Qwen sync cancelled")
        finally:
            self._qwen_export_active = False
            self._cancel_flag = False
            self._cancel_version = 0
            self._locks["qwen"].release()

    # ── ChatGPT (CDP page in same browser as Gemini) ──

    def _do_connect_chatgpt(self):
        self.log.add("[INFO] Connecting to ChatGPT via CDP...")
        if self.cdp.state != "running":
            raise RuntimeError("CDP not running — launch Chrome first")

        if self.chatgpt_page:
            try:
                self.chatgpt_page.close()
            except Exception:
                pass
            self.chatgpt_page = None

        try:
            browser = self._cdp_browser()
            page = browser.contexts[0].new_page()
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_load_state("domcontentloaded")

            if not self._is_page_alive(page):
                raise RuntimeError("Page not alive after navigation")

            self.chatgpt_page = page
            self._chatgpt_session_epoch += 1
            self._chatgpt_connected = True
            try:
                self.window.evaluate_js("setChatGPTConnected()")
            except Exception:
                pass
            self.log.add("[INFO] ChatGPT connected via CDP")
        except Exception as e:
            self._chatgpt_connected = False
            self.chatgpt_page = None
            self.log.add(f"[ERROR] ChatGPT CDP attach failed: {e}")
            raise

    def ensure_chatgpt_alive(self):
        if not self.chatgpt_page:
            self._chatgpt_connected = False
            return False
        try:
            self.chatgpt_page.url
            return True
        except Exception:
            self.chatgpt_page = None
            self._chatgpt_connected = False
            self._push_log("ChatGPT page lost — reconnect required")
            return False

    def add_chatgpt_account(self):
        if self._chatgpt_connect_lock:
            return "BUSY"
        if self._chatgpt_connected:
            return "OK"
        self._chatgpt_connect_lock = True
        try:
            self._connect_chatgpt_done.clear()
            self._gw_queue.put(("connect_chatgpt", ""))
            if not self._connect_chatgpt_done.wait(timeout=30):
                raise RuntimeError("ChatGPT CDP connection timeout")
            if not self._chatgpt_connected:
                raise RuntimeError("ChatGPT CDP connection failed")
            return "OK"
        finally:
            self._chatgpt_connect_lock = False

    def sync_chatgpt(self, urls_json):
        if not self._chatgpt_connected:
            raise RuntimeError("ChatGPT not connected")
        if not _check_cdp_alive():
            raise RuntimeError("CDP not available")
        if self._chatgpt_export_active:
            raise RuntimeError("ChatGPT export already in progress")
        urls = json.loads(urls_json)
        self.log.add(f"[INFO] Enqueuing ChatGPT batch export ({len(urls)} urls)")
        self._gw_queue.put(("export_chatgpt_batch", urls))
        return "STARTED"

    def reconnect_chatgpt(self):
        self.log.add("[INFO] Reconnecting ChatGPT...")
        if self.chatgpt_page:
            try:
                self.chatgpt_page.close()
            except Exception:
                pass
            self.chatgpt_page = None
        self._chatgpt_connected = False
        self.add_chatgpt_account()

    def _export_chatgpt(self, url, epoch=0, chat_order=0):
        if epoch and self._chatgpt_session_epoch != epoch:
            self.log.add(f"[EXPORT][RACE] epoch_mismatch expected={epoch} current={self._chatgpt_session_epoch}")
            return None

        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url

        self.log.add(f"[INFO] Exporting ChatGPT {url[:60]}...")
        self._check_cancel()

        try:
            # Phase 1: passive API capture during navigation
            capture = ApiCapture(self.chatgpt_page)
            capture.__enter__()
            try:
                with self._cdp_lock:
                    self.pw_call(self.chatgpt_page.goto, url, wait_until="domcontentloaded", timeout=30)
                    self.chatgpt_page.wait_for_timeout(2000)
                captured = capture.wait(timeout=10)
            finally:
                capture.__exit__()

            # Phase 2: extraction pipeline (API → NEXT_DATA → DOM)
            model = extract_chatgpt_pipeline(
                self.chatgpt_page, url,
                capture_result=captured,
                log_progress=self._push_log,
                cancel_check=lambda: self._cancel_flag,
            )

            if not model:
                self.log.add("[ERROR] ChatGPT extraction returned no data")
                self._push_log("ERR: ChatGPT no data")
                return None

            if not model.messages or len(model.messages) < 2:
                self.log.add(f"[ERROR] ChatGPT extraction returned {len(model.messages) if model.messages else 0} messages, skipping write")
                self._push_log("ChatGPT ERR: 0 messages")
                return None

            # Phase 3: validate
            vr = validate_conversation(model)
            if not vr.ok:
                self.log.add(f"[ERROR] ChatGPT validation failed: {vr.errors}")
                self._push_log(f"ChatGPT ERR: validation failed ({len(vr.errors)} errors)")
                return None
            if vr.warnings:
                for w in vr.warnings:
                    self.log.add(f"[WARN] ChatGPT validation: {w}")

            # Phase 4: write
            path = self._chatgpt_writer.write(model, chat_order=chat_order)
            if not path:
                self._push_log("ChatGPT ERR: write failed")
                return None

            n = len(model.messages)
            self.log.add(f"[SUCCESS] ChatGPT {n} msgs ({model.metadata.get('source', '?')}) → {path.name}")
            self._push_log(f"ChatGPT OK: {n} msgs — {model.title}")
            return {"ok": True, "path": str(path), "count": n}

        except Exception as e:
            self.log.add(f"[ERROR] ChatGPT export failed: {e}")
            self._push_log(f"ChatGPT ERR: {e}")
            return None

    def _do_export_chatgpt_batch(self, urls):
        if not self._locks["chatgpt"].acquire(blocking=False):
            self.log.add("[WARN] ChatGPT sync BUSY")
            self._push_log("ChatGPT BUSY")
            return
        self.set_sync_state("chatgpt", "running")
        self.log.add(f"[CHATGPT] batch start user_urls={urls}")
        batch_epoch = self._chatgpt_session_epoch

        self._chatgpt_export_active = True
        self._chatgpt_writer = ExportWriter(out_dir=self._output_folders["chatgpt"])

        try:
            if urls:
                urls = list(dict.fromkeys(urls))
                results = []
                for idx, url in enumerate(urls, 1):
                    self._check_cancel()
                    if self._chatgpt_session_epoch != batch_epoch:
                        self.log.add(f"[BATCH][RACE] epoch_mismatch")
                        break
                    if not _check_cdp_alive():
                        self.log.add("[WARN] CDP not available, stopping batch")
                        self._push_log("ChatGPT ERR: CDP lost")
                        break

                    self.log.add(f"[CHATGPT] [{idx}/{len(urls)}] exporting {url[:40]}")
                    self._push_log(f"ChatGPT [{idx}/{len(urls)}]...")
                    r = self._export_chatgpt(url, epoch=batch_epoch, chat_order=idx - 1)
                    if r:
                        results.append(r)
                        if len(urls) == 1:
                            break
                    else:
                        self.log.add(f"[CHATGPT] export returned None for {url[:60]}")

                total = sum(r["count"] for r in results) if results else 0
                self.set_sync_state("chatgpt", "done")
                self.log.add(f"[INFO] ChatGPT batch done: {total} msgs from {len(results)}/{len(urls)} chats")
                self._push_log(f"ChatGPT batch: {total} msgs — {len(results)}/{len(urls)}")
                return

            adapter = ChatGPTAdapter(
                self.chatgpt_page, self._cdp_lock,
                self._push_log,
                lambda: self._cancel_flag,
            )
            if not adapter.healthcheck():
                self.log.add("[WARN] ChatGPT adapter healthcheck failed")
                self._push_log("ChatGPT ERR: page not available")
                return

            chats = adapter.list_chats()
            self.log.add(f"[CHATGPT] sidebar: {len(chats)} chats")
            self._push_log(f"ChatGPT sidebar: {len(chats)} chats")

            if not chats:
                self.log.add("[ERROR] No ChatGPT chats found in sidebar")
                self._push_log("ChatGPT ERR: no chats in sidebar")
                return

            results = []
            for chat_idx, chat in enumerate(chats):
                self._check_cancel()
                if self._chatgpt_session_epoch != batch_epoch:
                    break
                if not _check_cdp_alive():
                    self._push_log("ChatGPT ERR: CDP lost")
                    break

                self._push_log(f"ChatGPT [{chat_idx+1}/{len(chats)}] {chat['title'][:40]}")
                ok = adapter.open_chat(chat)
                if not ok:
                    self._push_log(f"ChatGPT [{chat_idx+1}/{len(chats)}] ERR: not found")
                    continue

                try:
                    record = adapter.extract_chat(chat)
                    if record:
                        path = self._chatgpt_writer.write(record.to_dict(), chat_order=chat_idx)
                        if path:
                            n = len(record.messages)
                            results.append({"ok": True, "path": str(path), "count": n})
                            self.log.add(f"[CHATGPT] [{chat_idx+1}/{len(chats)}] {n} msgs -> {path.name}")
                            self._push_log(f"ChatGPT [{chat_idx+1}/{len(chats)}] OK: {n} msgs")
                        else:
                            self._push_log(f"ChatGPT [{chat_idx+1}/{len(chats)}] ERR: write failed")
                    else:
                        self._push_log(f"ChatGPT [{chat_idx+1}/{len(chats)}] ERR: no data")
                except Exception as e:
                    self.log.add(f"[CHATGPT] [{chat_idx+1}/{len(chats)}] error: {e}")
                    self._push_log(f"ChatGPT [{chat_idx+1}/{len(chats)}] ERR: {e}")

            total_msgs = sum(r["count"] for r in results) if results else 0
            self.set_sync_state("chatgpt", "done")
            self.log.add(f"[INFO] ChatGPT batch done: {total_msgs} msgs from {len(results)}/{len(chats)} chats")
            self._push_log(f"ChatGPT batch: {total_msgs} msgs — {len(results)}/{len(chats)}")

            try:
                self.window.evaluate_js("copyLogContent()")
            except Exception:
                pass

        except Cancelled:
            self.set_sync_state("chatgpt", "failed")
            self.log.add("[INFO] ChatGPT batch cancelled by user")
            self._push_log("⏹ ChatGPT sync cancelled")
        finally:
            self._chatgpt_export_active = False
            self._cancel_flag = False
            self._cancel_version = 0
            self._locks["chatgpt"].release()

    # ── Claude (CDP page in same browser as Gemini) ──

    def _do_connect_claude(self):
        self.log.add("[INFO] Connecting to Claude via CDP...")
        if self.cdp.state != "running":
            raise RuntimeError("CDP not running — launch Chrome first")

        if self.claude_page:
            try:
                self.claude_page.close()
            except Exception:
                pass
            self.claude_page = None

        try:
            browser = self._cdp_browser()
            page = browser.contexts[0].new_page()
            page.goto("https://claude.ai/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_load_state("domcontentloaded")

            if not self._is_page_alive(page):
                raise RuntimeError("Page not alive after navigation")

            self.claude_page = page
            self._claude_session_epoch += 1
            self._claude_connected = True
            try:
                self.window.evaluate_js("setClaudeConnected()")
            except Exception:
                pass
            self.log.add("[INFO] Claude connected via CDP")
        except Exception as e:
            self._claude_connected = False
            self.claude_page = None
            self.log.add(f"[ERROR] Claude CDP attach failed: {e}")
            raise

    def ensure_claude_alive(self):
        if not self.claude_page:
            self._claude_connected = False
            return False
        try:
            self.claude_page.url
            return True
        except Exception:
            self.claude_page = None
            self._claude_connected = False
            self._push_log("Claude page lost — reconnect required")
            return False

    def add_claude_account(self):
        if self._claude_connect_lock:
            return "BUSY"
        if self._claude_connected:
            return "OK"
        self._claude_connect_lock = True
        try:
            self._connect_claude_done.clear()
            self._gw_queue.put(("connect_claude", ""))
            if not self._connect_claude_done.wait(timeout=30):
                raise RuntimeError("Claude CDP connection timeout")
            if not self._claude_connected:
                raise RuntimeError("Claude CDP connection failed")
            return "OK"
        finally:
            self._claude_connect_lock = False

    def sync_claude(self, urls_json):
        if not self._claude_connected:
            raise RuntimeError("Claude not connected")
        if not _check_cdp_alive():
            raise RuntimeError("CDP not available")
        if self._claude_export_active:
            raise RuntimeError("Claude export already in progress")
        urls = json.loads(urls_json)
        self.log.add(f"[INFO] Enqueuing Claude batch export ({len(urls)} urls)")
        self._gw_queue.put(("export_claude_batch", urls))
        return "STARTED"

    def reconnect_claude(self):
        self.log.add("[INFO] Reconnecting Claude...")
        if self.claude_page:
            try:
                self.claude_page.close()
            except Exception:
                pass
            self.claude_page = None
        self._claude_connected = False
        self.add_claude_account()

    def _export_claude(self, url, epoch=0, chat_order=0):
        if epoch and self._claude_session_epoch != epoch:
            self.log.add(f"[EXPORT][RACE] epoch_mismatch expected={epoch} current={self._claude_session_epoch}")
            return None

        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url

        self.log.add(f"[INFO] Exporting Claude {url[:60]}...")
        self._check_cancel()

        try:
            with self._cdp_lock:
                self.pw_call(self.claude_page.goto, url, wait_until="domcontentloaded", timeout=30)
                self.claude_page.wait_for_timeout(2000)

            data = extract_claude_hybrid(
                self.claude_page, url,
                log_progress=self._push_log,
                cancel_check=lambda: self._cancel_flag,
            )

            if not data:
                self.log.add("[ERROR] Claude extraction returned no data")
                self._push_log("ERR: Claude no data")
                return None

            if not data.get("messages"):
                self.log.add("[ERROR] Claude extraction returned 0 messages, skipping write")
                self._push_log("Claude ERR: 0 messages")
                return None

            path = self._claude_writer.write(data, chat_order=chat_order)
            if not path:
                self._push_log("Claude ERR: write failed")
                return None

            n = len(data.get("messages", []))
            self.log.add(f"[SUCCESS] Claude {n} msgs → {path.name}")
            self._push_log(f"Claude OK: {n} msgs — {data.get('title', '?')}")
            return {"ok": True, "path": str(path), "count": n}

        except Exception as e:
            self.log.add(f"[ERROR] Claude export failed: {e}")
            self._push_log(f"Claude ERR: {e}")
            return None

    def _do_export_claude_batch(self, urls):
        if not self._locks["claude"].acquire(blocking=False):
            self.log.add("[WARN] Claude sync BUSY")
            self._push_log("Claude BUSY")
            return
        self.set_sync_state("claude", "running")
        self.log.add(f"[CLAUDE] batch start user_urls={urls}")
        batch_epoch = self._claude_session_epoch

        self._claude_export_active = True
        self._claude_writer = ExportWriter(out_dir=self._output_folders["claude"])

        try:
            if urls:
                urls = list(dict.fromkeys(urls))
                results = []
                for idx, url in enumerate(urls, 1):
                    self._check_cancel()
                    if self._claude_session_epoch != batch_epoch:
                        self.log.add(f"[BATCH][RACE] epoch_mismatch")
                        break
                    if not _check_cdp_alive():
                        self.log.add("[WARN] CDP not available, stopping batch")
                        self._push_log("Claude ERR: CDP lost")
                        break

                    self.log.add(f"[CLAUDE] [{idx}/{len(urls)}] exporting {url[:40]}")
                    self._push_log(f"Claude [{idx}/{len(urls)}]...")
                    r = self._export_claude(url, epoch=batch_epoch, chat_order=idx - 1)
                    if r:
                        results.append(r)
                        if len(urls) == 1:
                            break
                    else:
                        self.log.add(f"[CLAUDE] export returned None for {url[:60]}")

                total = sum(r["count"] for r in results) if results else 0
                self.set_sync_state("claude", "done")
                self.log.add(f"[INFO] Claude batch done: {total} msgs from {len(results)}/{len(urls)} chats")
                self._push_log(f"Claude batch: {total} msgs — {len(results)}/{len(urls)}")
                return

            adapter = ClaudeAdapter(
                self.claude_page, self._cdp_lock,
                self._push_log,
                lambda: self._cancel_flag,
            )
            if not adapter.healthcheck():
                self.log.add("[WARN] Claude adapter healthcheck failed")
                self._push_log("Claude ERR: page not available")
                return

            chats = adapter.list_chats()
            self.log.add(f"[CLAUDE] sidebar: {len(chats)} chats")
            self._push_log(f"Claude sidebar: {len(chats)} chats")

            if not chats:
                self.log.add("[ERROR] No Claude chats found in sidebar")
                self._push_log("Claude ERR: no chats in sidebar")
                return

            results = []
            for chat_idx, chat in enumerate(chats):
                self._check_cancel()
                if self._claude_session_epoch != batch_epoch:
                    break
                if not _check_cdp_alive():
                    self._push_log("Claude ERR: CDP lost")
                    break

                self._push_log(f"Claude [{chat_idx+1}/{len(chats)}] {chat['title'][:40]}")
                ok = adapter.open_chat(chat)
                if not ok:
                    self._push_log(f"Claude [{chat_idx+1}/{len(chats)}] ERR: not found")
                    continue

                try:
                    record = adapter.extract_chat(chat)
                    if record:
                        path = self._claude_writer.write(record.to_dict(), chat_order=chat_idx)
                        if path:
                            n = len(record.messages)
                            results.append({"ok": True, "path": str(path), "count": n})
                            self.log.add(f"[CLAUDE] [{chat_idx+1}/{len(chats)}] {n} msgs -> {path.name}")
                            self._push_log(f"Claude [{chat_idx+1}/{len(chats)}] OK: {n} msgs")
                        else:
                            self._push_log(f"Claude [{chat_idx+1}/{len(chats)}] ERR: write failed")
                    else:
                        self._push_log(f"Claude [{chat_idx+1}/{len(chats)}] ERR: no data")
                except Exception as e:
                    self.log.add(f"[CLAUDE] [{chat_idx+1}/{len(chats)}] error: {e}")
                    self._push_log(f"Claude [{chat_idx+1}/{len(chats)}] ERR: {e}")

            total_msgs = sum(r["count"] for r in results) if results else 0
            self.set_sync_state("claude", "done")
            self.log.add(f"[INFO] Claude batch done: {total_msgs} msgs from {len(results)}/{len(chats)} chats")
            self._push_log(f"Claude batch: {total_msgs} msgs — {len(results)}/{len(chats)}")

            try:
                self.window.evaluate_js("copyLogContent()")
            except Exception:
                pass

        except Cancelled:
            self.set_sync_state("claude", "failed")
            self.log.add("[INFO] Claude batch cancelled by user")
            self._push_log("⏹ Claude sync cancelled")
        finally:
            self._claude_export_active = False
            self._cancel_flag = False
            self._cancel_version = 0
            self._locks["claude"].release()

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
        if self._gemini_connect_lock:
            return "BUSY"
        if self._gemini_connect_state == "connected":
            try:
                if self.gemini_page and self.gemini_page.url:
                    self.log.add("[INFO] Gemini already connected")
                    return "OK"
            except Exception:
                self._gemini_connect_state = "idle"
        self._gemini_connect_lock = True
        try:
            self._connect_gemini_done.clear()
            self._gw_queue.put(("connect_gemini", url))
            if not self._connect_gemini_done.wait(timeout=30):
                raise RuntimeError("Gemini CDP connection timeout")
            if not self.gemini_page or not self._is_page_alive(self.gemini_page):
                raise RuntimeError("Gemini not actually connected")
            self._gemini_connect_state = "connected"
            return "OK"
        finally:
            self._gemini_connect_lock = False

    def sync_gemini(self, urls_json):
        if not self.gemini_page:
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
        if self._gemini_connect_lock:
            return "BUSY"
        self._gemini_connect_lock = True
        self._gemini_connect_state = "idle"
        try:
            self.log.add("[INFO] Reconnecting Gemini...")
            if self.gemini_page:
                try:
                    self.gemini_page.close()
                except Exception:
                    pass
                self.gemini_page = None
            self.add_gemini_account("cdp")
        finally:
            self._gemini_connect_lock = False

    def _close_cdp_browser(self):
        self.log.add("[CDP] Closing browser session...")
        for page in [self.gemini_page, self.qwen_page, self.chatgpt_page, self.claude_page]:
            try:
                if page: page.close()
            except Exception:
                pass
        if self.pw:
            try:
                self.pw.close()
            except Exception:
                pass
        self.cdp.stop()
        self._gw_browser = None
        self.gemini_page = None
        self.qwen_page = None
        self.chatgpt_page = None
        self.claude_page = None
        self.pw = None
        self._gemini_connect_state = "idle"
        self._qwen_connected = False
        self._chatgpt_connected = False
        self._claude_connected = False
        self._seen_urls.clear()
        self._last_watched_url = ""
        try:
            self.window.evaluate_js("""
                requestAnimationFrame(() => {
                    document.querySelectorAll('.pv-dot').forEach(d => d.className = 'pv-dot dot-off');
                    ['deepseek','gemini','qwen','chatgpt','claude'].forEach(p => {
                        const b = document.querySelector('.'+p+' .badge');
                        if(b) { b.textContent='не подключено'; b.className='badge'; }
                    });
                    document.querySelectorAll('[id$="Urls"]').forEach(e => e.classList.add('hidden'));
                    document.querySelectorAll('[id$="Account"]').forEach(e => e.classList.add('hidden'));
                });
            """)
        except Exception:
            pass
        self.log.add("[CDP] Browser closed")

    def _is_provider_connected(self, name):
        return {
            "deepseek": self.pw is not None,
            "gemini": self._gemini_connect_state == "connected",
            "qwen": self._qwen_connected,
            "chatgpt": self._chatgpt_connected,
            "claude": self._claude_connected,
        }.get(name, False)

    def set_sync_state(self, provider, state):
        self._sync_state[provider] = state
        try:
            self.window.evaluate_js(f"setProviderSync('{provider}', '{state}')")
        except Exception:
            pass

    def _do_sync_all(self):
        try:
            self.window.evaluate_js("setSyncRunning(true)")
        except Exception:
            pass
        self.log.add("[INFO] Sync All — запуск всех провайдеров")
        providers = [
            ("deepseek", self.sync_provider, "[]"),
            ("gemini",   self.sync_gemini, "[]"),
            ("qwen",     self.sync_qwen, "[]"),
            ("chatgpt",  self.sync_chatgpt, "[]"),
            ("claude",   self.sync_claude, "[]"),
        ]
        active = [(n, fn, arg) for n, fn, arg in providers if self._is_provider_connected(n)]
        if not active:
            self.log.add("[INFO] No providers connected — nothing to sync")
            try:
                self.window.evaluate_js("setSyncRunning(false)")
            except Exception:
                pass
            return
        threads = []
        for name, fn, arg in active:
            t = threading.Thread(target=self._safe_run_provider, args=(name, fn, arg), daemon=True)
            t.start()
            threads.append((name, t))
        for name, t in threads:
            t.join(timeout=600)
            if t.is_alive():
                self.log.add(f"[WARN] {name} provider thread did not finish")
        self.log.add(f"[INFO] Sync All — запущено {len(active)} провайдеров")
        try:
            self.window.evaluate_js("setSyncRunning(false)")
        except Exception:
            pass

    def _safe_run_provider(self, name, fn, arg):
        self.log.add(f"[SYNC] Запуск {name}...")
        try:
            fn(arg)
        except Cancelled:
            self.set_sync_state(name, "failed")
        except Exception as e:
            self.log.add(f"[ERROR] {name}: {e}")
            self.set_sync_state(name, "failed")

    def launch_chrome_cdp(self):
        if self.cdp.healthcheck():
            self.log.add("[INFO] CDP alive — force restart Chrome session")
            self._close_cdp_browser()
            time_module.sleep(2)
        self.cdp.start()
        return "OK"

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
    webview.start(debug=True, icon='ui/icon.ico')
