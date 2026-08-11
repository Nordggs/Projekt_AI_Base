import time

from adapters.base import BaseAdapter
from conversation.irbuilder import IRBuilder, Provider
from conversation.models import ConversationModel
from exporters.gemini_extract import (
    extract_gemini_dom,
    extract_gemini_rpc,
    discover_all_gemini_urls,
)


GEMINI_CLICK_BY_URL_JS = """
(u) => {
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
}
"""

GEMINI_SIDEBAR_SCROLL_JS = """
() => {
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
}
"""

_GEMINI_PREFLIGHT_JS = """
(target_url) => {
    const links = [...document.querySelectorAll('a[href*="/app/"]')]
        .map(a => {
            const href = a.getAttribute('href') || '';
            const text = (a.textContent || '').trim();
            let full = '';
            if (href.startsWith('/')) full = 'https://gemini.google.com' + href;
            else if (href.includes('gemini.google.com')) full = href;
            else return null;
            return { href: full, text };
        })
        .filter(Boolean);
    return {
        app_links: links.length,
        all_links_count: document.querySelectorAll('a').length,
        listitem_count: document.querySelectorAll('[role="listitem"]').length,
        first_links: links.slice(0, 3),
        sidebar_samples: [...document.querySelectorAll(
            '.chat-history-scroll-container *, infinite-scroller *, [class*="history"] *, nav *'
        )].map(el => (el.textContent || '').trim().slice(0, 80)).filter(Boolean).slice(0, 5),
        url: location.href,
        title: document.title,
    };
}
"""


class GeminiAdapter(BaseAdapter):
    name = "gemini"

    def __init__(self, page, log_func, cancel_check, cancel_version):
        self.page = page
        self.log = log_func
        self.cancel_check = cancel_check
        self.cancel_version = cancel_version

    def healthcheck(self) -> bool:
        try:
            self.page.url
            return True
        except Exception:
            return False

    def list_chats(self) -> list[dict]:
        urls = discover_all_gemini_urls(self.page)
        s = self._snapshot_sidebar_state('a[href*="/app/"]')
        self._log_sidebar_snapshot("list_chats end", s, provider="GEMINI")
        return [{"url": url, "title": ""} for url in urls]

    def open_chat(self, chat: dict) -> bool:
        target_url = chat.get("url", "").rstrip("/")
        if not target_url:
            return False

        s = self._snapshot_sidebar_state('a[href*="/app/"]')
        self._log_sidebar_snapshot("open_chat begin", s, provider="GEMINI",
                                   target_url=target_url)

        self.page.keyboard.press("Control+Shift+h")
        self.page.wait_for_timeout(2000)

        preflight = self.page.evaluate(_GEMINI_PREFLIGHT_JS, target_url)
        deadline = time.time() + 20
        while preflight["app_links"] == 0 and time.time() < deadline:
            self.cancel_check()
            self.page.wait_for_timeout(500)
            preflight = self.page.evaluate(_GEMINI_PREFLIGHT_JS, target_url)

        if preflight["app_links"] == 0:
            self.log(
                f"[GEMINI][DIAG] preflight empty after wait: "
                f"target_url={target_url} "
                f"app_links=0 "
                f"all_links={preflight['all_links_count']} "
                f"listitem={preflight['listitem_count']} "
                f"sidebar_samples={preflight['sidebar_samples']} "
                f"url={preflight['url']} "
                f"title={preflight['title']}"
            )
            return False

        first_seen = None
        last_seen = None

        for iteration in range(60):
            self.cancel_check()
            current = self.page.evaluate("location.href.replace(/\\/+$/, '')")
            if current == target_url:
                self.page.wait_for_timeout(2000)
                return True

            clicked = self.page.evaluate(GEMINI_CLICK_BY_URL_JS, target_url)
            if clicked:
                if first_seen is None:
                    first_seen = iteration
                last_seen = iteration
                try:
                    self.page.wait_for_function(
                        "(u) => location.href.replace(/\\/+$/, '') === u",
                        target_url, timeout=10000,
                    )
                    self.page.wait_for_timeout(1000)
                    return True
                except Exception:
                    pass

            self.page.evaluate(GEMINI_SIDEBAR_SCROLL_JS)
            self.page.wait_for_timeout(300)

        diag = self.page.evaluate("""
            () => {
                const links = [...document.querySelectorAll('a[href*="/app/"]')]
                    .map(a => {
                        let h = a.getAttribute('href');
                        if (!h) return null;
                        if (h.startsWith('/')) return 'https://gemini.google.com' + h;
                        if (h.includes('gemini.google.com')) return h;
                        return null;
                    })
                    .filter(Boolean);
                const unique = new Set(links);
                const sidebar = document.querySelector(
                    '.chat-history-scroll-container, infinite-scroller, [class*="history"], nav'
                );
                return {
                    unique_links: unique.size,
                    links_count: links.length,
                    scroll_top: sidebar ? sidebar.scrollTop : null,
                    scroll_height: sidebar ? sidebar.scrollHeight : null,
                    sidebar_open: sidebar !== null,
                };
            }
        """)
        self.log(
            f"[GEMINI][DIAG] open_chat failed: "
            f"iterations={iteration + 1} "
            f"target_url={target_url} "
            f"target_ever_seen={'true' if first_seen is not None else 'false'} "
            f"first_seen={first_seen} "
            f"last_seen={last_seen} "
            f"unique_links={diag['unique_links']} "
            f"links_count={diag['links_count']} "
            f"scroll={diag['scroll_top']}/{diag['scroll_height']} "
            f"sidebar_open={str(diag['sidebar_open']).lower()}"
        )
        return False

    def extract_chat(self, chat: dict) -> ConversationModel | None:
        url = chat.get("url", "")

        # Phase 1: RPC via JS fetch (fast)
        source_label = "dom"
        data = extract_gemini_rpc(self.page, url)
        if data:
            source_label = "rpc"
            if self.log:
                self.log(f"[GEMINI] RPC: {len(data['messages'])} msgs")

        # Phase 2: DOM scroll fallback
        if not data:
            if self.log:
                self.log("[INFO] RPC failed, using DOM extraction")
            for retry in range(2):
                if self.log:
                    self.log(f"[INFO] DOM extraction attempt {retry + 1}")
                try:
                    data = extract_gemini_dom(
                        self.page, url,
                        log_progress=self.log,
                        cancel_check=self.cancel_check,
                        cancel_epoch=self.cancel_version,
                    )
                except Exception as e:
                    if self.log:
                        self.log(f"[GEMINI][EXTRACT][FATAL] {e}")
                    data = None
                if data and data.get("messages"):
                    break
                if retry == 0:
                    if self.log:
                        self.log("[INFO] DOM returned 0 msgs, re-clicking sidebar...")
                    try:
                        self.page.evaluate(GEMINI_CLICK_BY_URL_JS, url.rstrip("/"))
                        self.page.wait_for_timeout(3000)
                    except Exception:
                        pass

        if not data or not data.get("messages"):
            return None

        model = IRBuilder.build(Provider.GEMINI, data)
        model.metadata["source"] = source_label
        return model
