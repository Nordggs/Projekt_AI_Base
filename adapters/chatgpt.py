import json

from adapters.base import BaseAdapter, ChatRecord
from adapters.normalize import normalize_messages
from exporters.chatgpt_extract import (
    ApiCapture, extract_chatgpt_pipeline, extract_chatgpt_dom, ensure_conversation_loaded,
)
from conversation.adapters import from_next_data, from_dom


CHATGPT_SIDEBAR_SCAN_JS = """
() => {
    const links = document.querySelectorAll('a[href*="/c/"], a[href*="/g/"], nav a[href*="/chat"]');
    const items = [];
    links.forEach((el, i) => {
        const href = el.getAttribute('href');
        const title = (el.innerText || el.textContent || '').trim().substring(0, 200);
        if (href && title) {
            items.push({
                id: `chatgpt-${i}`,
                title: title,
                url: href.startsWith('http') ? href : 'https://chatgpt.com' + href,
                selector: `a[href="${href.replace(/"/g, '\\"')}"]`,
            });
        }
    });
    if (items.length === 0) {
        document.querySelectorAll('[class*="sidebar"] [class*="item"], [class*="conversation"], [class*="history"] [class*="item"]').forEach((el, i) => {
            const title = (el.innerText || el.textContent || '').trim().substring(0, 200);
            if (title && title.length > 1) {
                items.push({
                    id: `chatgpt-${i}`,
                    title: title,
                    url: '',
                    selector: `[class*="item"]:nth-of-type(${i+1})`,
                });
            }
        });
    }
    return items;
}
"""

CHATGPT_CLICK_JS = """
(sel) => {
    const el = document.querySelector(sel);
    if (el) { el.click(); return true; }
    return false;
}
"""


class ChatGPTAdapter(BaseAdapter):
    name = "chatgpt"

    def __init__(self, page, cdp_lock, log_func, cancel_check):
        self.page = page
        self.cdp_lock = cdp_lock
        self.log = log_func
        self.cancel_check = cancel_check

    def healthcheck(self) -> bool:
        try:
            self.page.url
            return True
        except Exception:
            return False

    def list_chats(self) -> list[dict]:
        with self.cdp_lock:
            try:
                self.page.goto("https://chatgpt.com/",
                               wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.page.wait_for_timeout(3000)
            items = self.page.evaluate(CHATGPT_SIDEBAR_SCAN_JS)
            if len(items) < 3:
                self.page.wait_for_timeout(3000)
                items = self.page.evaluate(CHATGPT_SIDEBAR_SCAN_JS)
        return items

    def open_chat(self, chat: dict) -> bool:
        url = chat.get("url", "")
        if url and url.startswith("http"):
            with self.cdp_lock:
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self.page.wait_for_timeout(2000)
            return True
        with self.cdp_lock:
            ok = self.page.evaluate(CHATGPT_CLICK_JS, chat["selector"])
            if not ok:
                return False
            self.page.wait_for_timeout(2000)
        return True

    def extract_chat(self, chat: dict) -> ChatRecord:
        url = self.page.url
        model = None

        # Tier 1: __NEXT_DATA__ (no navigation needed)
        try:
            nd = from_next_data(self.page)
            if nd and nd.messages and len(nd.messages) >= 2:
                model = nd
                model.metadata["source"] = "next_data"
                if self.log:
                    self.log(f"[CHATGPT] __NEXT_DATA__: {len(model.messages)} msgs")
        except Exception:
            pass

        # Tier 2: API capture via reload
        if not model:
            try:
                capture = ApiCapture(self.page)
                capture.__enter__()
                try:
                    self.page.reload()
                    self.page.wait_for_timeout(2000)
                    captured = capture.wait(timeout=10)
                finally:
                    capture.__exit__()

                if captured:
                    model = extract_chatgpt_pipeline(
                        self.page, url,
                        capture_result=captured,
                        log_progress=self.log,
                        cancel_check=self.cancel_check,
                    )
                    if self.log and model:
                        self.log(f"[CHATGPT] API capture: {len(model.messages)} msgs")
            except Exception:
                pass

        # Tier 3: DOM fallback
        if not model:
            try:
                if self.log:
                    self.log("[CHATGPT] API/NEXT_DATA failed, DOM fallback")
                ok = ensure_conversation_loaded(self.page, log_progress=self.log, cancel_check=self.cancel_check)
                dom_data = extract_chatgpt_dom(self.page, url, log_progress=self.log, cancel_check=self.cancel_check)
                if dom_data and dom_data.get("messages"):
                    model = from_dom(dom_data)
                    model.metadata["source"] = "dom"
                    if self.log:
                        self.log(f"[CHATGPT] DOM: {len(dom_data['messages'])} msgs")
            except Exception:
                pass

        if not model or not model.messages:
            if self.log:
                self.log("[CHATGPT] all sources failed")
            return None

        return ChatRecord(
            id=chat["id"],
            title=model.title or chat["title"],
            messages=normalize_messages(
                [{"role": m.role, "content": m.content, "timestamp": m.timestamp,
                  "message_id": m.message_id, "attachments": m.attachments}
                 for m in model.messages]
            ),
            source="chatgpt",
            url=url,
        )
