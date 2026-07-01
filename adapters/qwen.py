import json

from adapters.base import BaseAdapter, ChatRecord
from adapters.normalize import normalize_messages
from exporters.qwen_extract import extract_qwen_hybrid


QWEN_SIDEBAR_SCAN_JS = """
() => [...document.querySelectorAll('div.chat-item-drag a.chat-item-drag-link')]
    .map((el, i) => ({
        id: `qwen-${i}`,
        title: (el.innerText || el.textContent || '').trim().substring(0, 200),
        selector: `div.chat-item-drag:nth-of-type(${i+1}) a.chat-item-drag-link`
    }))
"""

QWEN_WAIT_MESSAGES_JS = """
() => {
    const msgs = document.querySelectorAll('[class*="message"]');
    return msgs.length > 0 && location.href.includes('/c/');
}
"""

QWEN_CLICK_JS = """
(sel) => {
    const el = document.querySelector(sel);
    if (el) el.click();
    return !!el;
}
"""


class QwenAdapter(BaseAdapter):
    name = "qwen"

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
                self.page.goto("https://chat.qwen.ai/",
                               wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.page.wait_for_timeout(3000)

            items = self.page.evaluate(QWEN_SIDEBAR_SCAN_JS)
            if len(items) < 5:
                self.page.wait_for_timeout(3000)
                items = self.page.evaluate(QWEN_SIDEBAR_SCAN_JS)

        return items

    def open_chat(self, chat: dict) -> bool:
        with self.cdp_lock:
            ok = self.page.evaluate(QWEN_CLICK_JS, chat["selector"])
            if not ok:
                return False
            self.page.wait_for_function(QWEN_WAIT_MESSAGES_JS, timeout=30000)
            self.page.wait_for_timeout(500)
        return True

    def extract_chat(self, chat: dict) -> ChatRecord:
        url = self.page.url
        data = extract_qwen_hybrid(
            self.page, url,
            log_progress=self.log,
            cancel_check=self.cancel_check,
        )
        if not data or not data.get("messages"):
            return None

        return ChatRecord(
            id=chat["id"],
            title=chat["title"],
            messages=normalize_messages(data["messages"]),
            source="qwen",
            url=url,
        )
