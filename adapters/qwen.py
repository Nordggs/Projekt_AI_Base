import json

from adapters.base import BaseAdapter
from conversation.irbuilder import IRBuilder, Provider
from conversation.models import ConversationModel
from exporters.qwen_extract import extract_qwen_hybrid


QWEN_SIDEBAR_SCAN_JS = """
() => [...document.querySelectorAll('div.chat-item-drag a.chat-item-drag-link')]
    .map((el, i) => ({
        id: `qwen-${i}`,
        title: (el.innerText || el.textContent || '').trim().substring(0, 200),
        index: i
    }))
"""

QWEN_WAIT_MESSAGES_JS = """
() => {
    const msgs = document.querySelectorAll('[class*="message"]');
    return msgs.length > 0 && location.href.includes('/c/');
}
"""

QWEN_CLICK_JS = """
(index) => {
    const els = document.querySelectorAll('a.chat-item-drag-link');
    if (els[index]) { els[index].click(); return true; }
    return false;
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

        s = self._snapshot_sidebar_state('div.chat-item-drag a.chat-item-drag-link')
        self._log_sidebar_snapshot("list_chats end", s, provider="QWEN")
        return items

    def open_chat(self, chat: dict) -> bool:
        s = self._snapshot_sidebar_state('div.chat-item-drag a.chat-item-drag-link')
        self._log_sidebar_snapshot("open_chat begin", s, provider="QWEN",
                                   chat_index=chat.get("index", 0))
        with self.cdp_lock:
            index = chat.get("index", 0)
            ok = self.page.evaluate(QWEN_CLICK_JS, index)
            if not ok:
                return False
            self.page.wait_for_function(QWEN_WAIT_MESSAGES_JS, timeout=30000)
            self.page.wait_for_timeout(500)
        return True

    def extract_chat(self, chat: dict) -> ConversationModel | None:
        url = self.page.url
        data = extract_qwen_hybrid(
            self.page, url,
            log_progress=self.log,
            cancel_check=self.cancel_check,
        )
        if not data or not data.get("messages"):
            return None

        model = IRBuilder.build(Provider.QWEN, data)
        model.metadata["provider"] = "qwen"
        model.metadata["source"] = "dom"
        return model
