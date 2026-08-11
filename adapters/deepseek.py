from adapters.base import BaseAdapter
from conversation.irbuilder import IRBuilder, Provider
from conversation.models import ConversationModel
from exporters.deepseek import DeepSeekExporter
from exporters.base import Browser


DEEPSEEK_SIDEBAR_SCAN_JS = """
() => [...document.querySelectorAll('a[href*="/chat/s/"]')]
    .map(a => ({ url: 'https://chat.deepseek.com' + a.getAttribute('href') }))
"""

DEEPSEEK_SCROLL_SIDEBAR_JS = """
() => {
    const c = document.querySelector('.ds-virtual-list');
    if (c) c.scrollTop += 300;
}
"""


class DeepSeekAdapter(BaseAdapter):
    name = "deepseek"

    def __init__(self, page, log_func, cancel_check):
        self.page = page
        self.log = log_func
        self.cancel_check = cancel_check

    def healthcheck(self) -> bool:
        try:
            self.page.url
            return True
        except Exception:
            return False

    def list_chats(self) -> list[dict]:
        items = self.page.evaluate(DEEPSEEK_SIDEBAR_SCAN_JS)
        for i, item in enumerate(items):
            item["id"] = f"deepseek-{i}"
            title = item.get("url", "").split("/")[-1][:40]
            item["title"] = title
        return items

    def open_chat(self, chat: dict) -> bool:
        target_url = chat.get("url", "").rstrip("/")
        if not target_url:
            return False

        for _ in range(60):
            self.cancel_check()
            urls = self.page.evaluate(DEEPSEEK_SIDEBAR_SCAN_JS)
            if any(target_url in (u.get("url") or "") for u in (urls or [])):
                return True
            self.page.evaluate(DEEPSEEK_SCROLL_SIDEBAR_JS)
            import time
            time.sleep(0.2)
        return False

    def extract_chat(self, chat: dict) -> ConversationModel | None:
        url = chat.get("url", "")
        if not url:
            return None

        browser = Browser(window=None, mode="playwright", log=self.log)
        browser._playwright_page = self.page

        result = []
        exporter = DeepSeekExporter(
            browser, url=url,
            capture_ssr=False, capture_cache=False,
            capture_bundles=False, capture_state=False,
        )
        exporter.start(lambda r: result.append(r))

        data = result[0] if result else None
        if not data or "error" in data:
            return None

        model = IRBuilder.build(Provider.DEEPSEEK, data)
        return model
