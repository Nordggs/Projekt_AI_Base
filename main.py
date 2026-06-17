import json
import os
import queue
import re
import shutil
import threading
import time as time_module
from datetime import datetime

import webview

from core.logger import LogBuffer
from exporters.base import Browser
from exporters.deepseek import DeepSeekExporter
from exporters.playwright_browser import BrowserPlaywright
from exporters.writer import ExportWriter


MAX_LOGIN_SOFT = 600
MAX_LOGIN_HARD = 1800
MIN_EXPECTED_CHATS = 2


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
        if not urls:
            urls = self._discover_sidebar_urls()
        if len(urls) < MIN_EXPECTED_CHATS:
            urls = self._discover_all_chat_urls()
        if not urls:
            self.log.add("[ERROR] No chat URLs found in sidebar")
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
