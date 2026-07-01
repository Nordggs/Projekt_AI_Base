import json

from adapters.base import BaseAdapter, ChatRecord
from adapters.normalize import normalize_messages
from exporters.claude_extract import extract_claude_hybrid


CLAUDE_SIDEBAR_SCAN_JS = """
() => {
    const links = document.querySelectorAll('a[href*="/chat/"]');
    const items = [];
    links.forEach((el, i) => {
        const href = el.getAttribute('href');
        const title = (el.innerText || el.textContent || '').trim().substring(0, 200);
        if (href && title) {
            items.push({
                id: `claude-${i}`,
                title: title,
                url: href.startsWith('http') ? href : 'https://claude.ai' + href,
                selector: `a[href="${href.replace(/"/g, '\\"')}"]`,
            });
        }
    });
    if (items.length === 0) {
        document.querySelectorAll('[class*="sidebar"] [class*="item"], [class*="conversation"], [class*="history"] div').forEach((el, i) => {
            const title = (el.innerText || el.textContent || '').trim().substring(0, 200);
            if (title && title.length > 1) {
                items.push({
                    id: `claude-${i}`,
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

CLAUDE_CLICK_JS = """
(sel) => {
    const el = document.querySelector(sel);
    if (el) { el.click(); return true; }
    return false;
}
"""


class ClaudeAdapter(BaseAdapter):
    name = "claude"

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
                self.page.goto("https://claude.ai/",
                               wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self.page.wait_for_timeout(3000)
            items = self.page.evaluate(CLAUDE_SIDEBAR_SCAN_JS)
            if len(items) < 3:
                self.page.wait_for_timeout(3000)
                items = self.page.evaluate(CLAUDE_SIDEBAR_SCAN_JS)
        return items

    def open_chat(self, chat: dict) -> bool:
        url = chat.get("url", "")
        if url and url.startswith("http"):
            with self.cdp_lock:
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self.page.wait_for_timeout(2000)
            return True
        with self.cdp_lock:
            ok = self.page.evaluate(CLAUDE_CLICK_JS, chat["selector"])
            if not ok:
                return False
            self.page.wait_for_timeout(2000)
        return True

    def extract_chat(self, chat: dict) -> ChatRecord:
        url = self.page.url
        data = extract_claude_hybrid(
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
            source="claude",
            url=url,
        )
