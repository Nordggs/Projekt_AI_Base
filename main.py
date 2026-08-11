import json
import os
import queue
import re
import sys
import threading
import time as time_module
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

import webview

from core.logger import LogBuffer
from adapters.gemini import GeminiAdapter
from adapters.deepseek import DeepSeekAdapter
from adapters.qwen import QwenAdapter
from adapters.chatgpt import ChatGPTAdapter
from adapters.claude import ClaudeAdapter
from adapters.cdp_manager import CDPManager
from conversation.enrichment import Enricher, RuntimeData, BlobEntry, TraceEntry, AnchorData
from exporters.writer import ExportWriter


def _resolve_ui_url():
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for candidate in (
        os.path.join(exe_dir, "ui", "app.html"),
        os.path.join(exe_dir, "_internal", "ui", "app.html"),
        os.path.join(os.getcwd(), "ui", "app.html"),
    ):
        if os.path.isfile(candidate):
            return "file:///" + candidate.replace("\\", "/")
    return "ui/app.html"


@dataclass
class CaptureContext:
    cdp_assets: list = field(default_factory=list)
    runtime: RuntimeData = field(default_factory=RuntimeData)
    anchor: AnchorData = field(default_factory=AnchorData)


def capture_cdp_if_needed(provider_name, page, push_log=None):
    if provider_name != "chatgpt":
        return CaptureContext()
    from exporters.attachment_capture import AttachmentCDPCapture as ACC
    import time as _t

    capture_cdp = ACC(page)
    capture_cdp.start()

    anchor = page.evaluate("""(() => ({ perf: performance.now(), epoch: Date.now() }))()""")
    anchor_data = AnchorData(
        perf_zero=anchor["perf"] / 1000,
        epoch_zero=anchor["epoch"] / 1000,
    )

    page.evaluate("""(() => {
        const trace = [];
        new MutationObserver(ms => {
            for (const m of ms) {
                if (m.type === 'childList')
                    for (const n of m.addedNodes)
                        if (n.tagName === 'IMG' && n.src)
                            trace.push({ src: n.src, t: performance.now(), kind: 'added' });
                if (m.type === 'attributes' && m.target.tagName === 'IMG' && m.target.src)
                    trace.push({ src: m.target.src, t: performance.now(), kind: 'attr' });
            }
            if (trace.length > 2000) trace.splice(0, trace.length - 2000);
        }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
        window.__img_trace = trace;
    })()""")

    page.evaluate("""(async () => {
        const c = document.querySelector('[data-testid="conversation-scroll"]')
               || document.querySelector('[data-testid="conversation-turns"]')
               || document.scrollingElement;
        if (!c) return;
        c.scrollTop = c.scrollHeight;
        c.dispatchEvent(new Event('scroll'));
        await new Promise(r => setTimeout(r, 3000));
    })()""")

    if push_log:
        push_log("[CDP] capturing trace + blob assets from runtime...")
    raw = page.evaluate("""(async () => {
        const trace = (window.__img_trace || []).slice();
        const blobs = [];
        for (const entry of trace) {
            if (entry.src.startsWith('blob:')) {
                try {
                    const r = await fetch(entry.src);
                    const buf = await r.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    let binary = '';
                    for (let j = 0; j < bytes.length; j++)
                        binary += String.fromCharCode(bytes[j]);
                    blobs.push({ src: entry.src, b64: btoa(binary), ctype: r.headers.get('content-type') || 'image/png', t: entry.t });
                } catch(e) {}
            }
        }
        return { trace, blobs };
    })()""")
    page.evaluate("window.__img_trace = []")

    capture_cdp.stop()

    runtime_data = RuntimeData(
        trace=[TraceEntry(**e) for e in raw["trace"]],
        blobs=[BlobEntry(**b) for b in raw["blobs"]],
    )
    if push_log:
        push_log(f"[CDP] trace={len(runtime_data.trace)} entries, blobs={len(runtime_data.blobs)}")

    return CaptureContext(cdp_assets=capture_cdp.assets, runtime=runtime_data, anchor=anchor_data)


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

    def connect_deepseek(self):
        return self._app.add_account("https://chat.deepseek.com/")

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
            url=_resolve_ui_url(),
            js_api=self.api,
            width=1300,
            height=750,
        )
        self.log = LogBuffer()
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
        self._gemini_connect_lock = False
        self._gemini_connect_state = "idle"  # idle | connecting | connected
        self._cdp_lock = threading.Lock()

        # Qwen (CDP page in same browser as Gemini)
        self.qwen_page = None
        self._qwen_connected = False
        self._qwen_connect_lock = False
        self._connect_qwen_done = threading.Event()
        self._qwen_export_active = False
        self._qwen_session_epoch = 0

        # ChatGPT (CDP page in same browser as Gemini)
        self.chatgpt_page = None
        self._chatgpt_connected = False
        self._chatgpt_connect_lock = False
        self._connect_chatgpt_done = threading.Event()
        self._chatgpt_export_active = False
        self._chatgpt_session_epoch = 0

        # Claude (CDP page in same browser as Gemini)
        self.claude_page = None
        self._claude_connected = False
        self._claude_connect_lock = False
        self._connect_claude_done = threading.Event()
        self._claude_export_active = False
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
                elif cmd == "export_provider":
                    name, urls = arg
                    try:
                        self._export_provider(name, urls)
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
                elif cmd == "export_provider":
                    name, urls = arg
                    try:
                        self._export_provider(name, urls)
                    finally:
                        reset = {"gemini": "_gemini_export_active", "qwen": "_qwen_export_active",
                                 "chatgpt": "_chatgpt_export_active", "claude": "_claude_export_active"}
                        flag = reset.get(name)
                        if flag:
                            setattr(self, flag, False)
                elif cmd == "connect_qwen":
                    with self._cdp_lock:
                        self._do_connect_qwen()
                    self._connect_qwen_done.set()
                elif cmd == "connect_chatgpt":
                    with self._cdp_lock:
                        self._do_connect_chatgpt()
                    self._connect_chatgpt_done.set()
                elif cmd == "connect_claude":
                    with self._cdp_lock:
                        self._do_connect_claude()
                    self._connect_claude_done.set()
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
            self._connect_gemini_done.set()
            try:
                self.window.evaluate_js("setGeminiConnected()")
            except Exception:
                pass
            self.log.add("[INFO] Gemini connected via CDP")
        except Exception as e:
            self._gemini_connect_state = "idle"
            self.log.add(f"[ERROR] CDP attach failed: {e}")
            raise

    def _ensure_gemini_page(self):
        if self.gemini_page and self._is_page_alive(self.gemini_page):
            return self.gemini_page
        browser = self._cdp_browser()
        self.gemini_page = browser.contexts[0].new_page()
        return self.gemini_page

    def _gemini_nav_home(self, page):
        try:
            page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

    # ── Unified adapter + pipeline layer (replaces all per-provider batch methods) ──

    def _page_for(self, name):
        return {
            "deepseek": self.pw.page if self.pw else None,
            "gemini": self.gemini_page,
            "qwen": self.qwen_page,
            "chatgpt": self.chatgpt_page,
            "claude": self.claude_page,
        }.get(name)

    def _adapter_for(self, name, page):
        switch = {
            "deepseek": lambda: DeepSeekAdapter(page, self.log, self._check_cancel),
            "gemini": lambda: GeminiAdapter(page, self._push_log, self._check_cancel, self._cancel_version),
            "qwen": lambda: QwenAdapter(page, self._cdp_lock, self._push_log, lambda: self._cancel_flag),
            "chatgpt": lambda: ChatGPTAdapter(page, self._cdp_lock, self._push_log, lambda: self._cancel_flag),
            "claude": lambda: ClaudeAdapter(page, self._cdp_lock, self._push_log, lambda: self._cancel_flag),
        }
        fn = switch.get(name)
        if fn is None:
            raise ValueError(f"Unknown adapter: {name}")
        return fn()

    def _export_provider(self, name, urls=None):
        lock = self._locks[name]
        if not lock.acquire(blocking=False):
            self.log.add(f"[WARN] {name} sync BUSY")
            self._push_log(f"{name} BUSY")
            return

        self.set_sync_state(name, "running")
        self.log.add(f"[{name.upper()}] batch start urls={urls}")
        writer = ExportWriter(out_dir=self._output_folders[name])

        try:
            if urls:
                chats = [{"url": u} for u in dict.fromkeys(urls)]
            elif name == "deepseek":
                urls = self._discover_sidebar_urls()
                chats = [{"url": u} for u in urls]
            else:
                page = self._page_for(name)
                adapter = self._adapter_for(name, page)
                if not adapter.healthcheck():
                    raise RuntimeError(f"{name} page not available")
                chats = adapter.list_chats()

            if not chats:
                raise RuntimeError(f"No {name} chat URLs found")

            self.log.add(f"[{name.upper()}] batch: {len(chats)} chats")
            results = []

            for idx, chat in enumerate(chats):
                self._check_cancel()

                if not _check_cdp_alive():
                    self._push_log(f"{name} ERR: CDP disconnected")
                    break

                title = chat.get("title", "")[:40] or chat.get("url", "")[:40]
                self.log.add(f"[{name.upper()}] [{idx+1}/{len(chats)}] {title}")
                self._push_log(f"{name} [{idx+1}/{len(chats)}] {title}")

                page = self._page_for(name)
                if page is None:
                    self._push_log(f"{name} ERR: page lost")
                    break

                adapter = self._adapter_for(name, page)
                if not adapter.healthcheck():
                    self._push_log(f"{name} ERR: page not available")
                    break

                ok = adapter.open_chat(chat)
                if not ok:
                    self._push_log(f"{name} [{idx+1}/{len(chats)}] ERR: not found")
                    continue

                model = adapter.extract_chat(chat)
                if not model:
                    self._push_log(f"{name} [{idx+1}/{len(chats)}] ERR: no data")
                    continue

                ctx = capture_cdp_if_needed(name, page, self._push_log)
                model, enrich_stats = Enricher.enrich(
                    model, ctx.cdp_assets, ctx.runtime, ctx.anchor,
                    log_func=self._push_log,
                )
                if ctx.cdp_assets or ctx.runtime.blobs:
                    writer.flush_media(model)
                    self.log.add(f"[{name.upper()}] enrich: cdp={enrich_stats.cdp_strict + enrich_stats.cdp_temporal} runtime={enrich_stats.runtime} partial={enrich_stats.partial}")

                path = writer.write(model, chat_order=idx)
                if path:
                    n = len(model.messages)
                    results.append({"ok": True, "path": str(path), "count": n})
                    self.log.add(f"[{name.upper()}] [{idx+1}/{len(chats)}] {n} msgs -> {path.name}")
                    self._push_log(f"{name} [{idx+1}/{len(chats)}] OK: {n} msgs")
                else:
                    self._push_log(f"{name} [{idx+1}/{len(chats)}] ERR: write failed")

            total = sum(r["count"] for r in results) if results else 0
            self.set_sync_state(name, "done")
            self.log.add(f"[INFO] {name} batch done: {total} msgs from {len(results)}/{len(chats)} chats")
            self._push_log(f"{name} batch: {total} msgs — {len(results)}/{len(chats)}")
            try:
                self.window.evaluate_js("copyLogContent()")
            except Exception:
                pass

        except Cancelled:
            self.set_sync_state(name, "failed")
            self.log.add(f"[INFO] {name} batch cancelled by user")
            self._push_log(f"⏹ {name} sync cancelled")
        except Exception as e:
            self.set_sync_state(name, "failed")
            self.log.add(f"[ERROR] {name}: {e}")
            self._push_log(f"{name} ERR: {e}")
        finally:
            self._cancel_flag = False
            self._cancel_version = 0
            lock.release()


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
        self._gw_queue.put(("export_provider", ("qwen", urls)))
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
        self._gw_queue.put(("export_provider", ("chatgpt", urls)))
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
        self._gw_queue.put(("export_provider", ("claude", urls)))
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
        self._pw_queue.put(("export_provider", ("deepseek", urls)))
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
        self._gw_queue.put(("export_provider", ("gemini", urls)))
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
        self.log.add("[INFO] Sync All — последовательно: ChatGPT → Gemini → Claude → Qwen → DeepSeek")
        order = ["chatgpt", "gemini", "claude", "qwen", "deepseek"]
        for name in order:
            if not self._is_provider_connected(name):
                continue
            self.log.add(f"[SYNC] Старт {name}...")
            try:
                q = self._pw_queue if name == "deepseek" else self._gw_queue
                q.put(("export_provider", (name, [])))
            except Exception as e:
                self.log.add(f"[ERROR] {name}: {e}")
                self.set_sync_state(name, "failed")
        self.log.add(f"[INFO] Sync All — выполнено")
        try:
            self.window.evaluate_js("setSyncRunning(false)")
        except Exception:
            pass

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

    def _write_model(self, model, writer, label, chat_order):
        path = writer.write(model, chat_order=chat_order)
        if not path:
            self._push_log(f"{label} ERR: write failed")
            return None
        n = len(model.messages)
        source = model.metadata.get("source", "?")
        self.log.add(f"[SUCCESS] {label} {n} msgs ({source}) → {path.name}")
        self._push_log(f"{label} OK: {n} msgs — {model.title}")
        return {"ok": True, "path": str(path), "count": n}

    def _push_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            self.window.evaluate_js(f"pushLog({json.dumps(f'[{ts}] {msg}')})")
        except Exception:
            pass


if __name__ == "__main__":
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    bundled_browsers = os.path.join(exe_dir, "ms-playwright")
    if os.path.isdir(bundled_browsers):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bundled_browsers
    app = App()
    webview.start(debug=False, icon='ui/icon.ico')
