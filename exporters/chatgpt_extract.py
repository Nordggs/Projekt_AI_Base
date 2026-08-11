import json
import os
import re
import time
from pathlib import Path

from config import CHATGPT_DIAGNOSE, DIAGNOSE_DIR
from conversation.models import ConversationModel, ValidationResult
from conversation.adapters import from_next_data
from conversation.irbuilder import IRBuilder, Provider
from conversation.validator import validate_all


class ApiCapture:
    """Context manager that passively captures ChatGPT API response during navigation."""

    def __init__(self, page):
        self.page = page
        self.result = None
        self._raw = None
        self._listener = None

    def _on_response(self, response):
        try:
            if response.status != 200:
                return
            ct = response.headers.get("content-type", "")
            if not ct.startswith("application/json"):
                return
            body = response.text()
            if len(body) < 1000:
                return
            data = json.loads(body)
            if isinstance(data, dict) and "mapping" in data and "current_node" in data:
                self._raw = body
                self.result = data
        except Exception:
            pass

    def __enter__(self):
        self._listener = lambda r: self._on_response(r)
        self.page.on("response", self._listener)
        return self

    def __exit__(self, *args):
        if self._listener:
            try:
                self.page.remove_listener("response", self._listener)
            except Exception:
                pass

    def wait(self, timeout=15):
        start = time.time()
        while (time.time() - start) < timeout:
            if self.result is not None:
                return self.result
            time.sleep(0.2)
            self.page.wait_for_timeout(200)
        return None


def _save_diagnostics(page, conv_id, api_raw=None, next_data_raw=None):
    if not CHATGPT_DIAGNOSE:
        return
    if not conv_id:
        conv_id = str(int(time.time()))
    out = DIAGNOSE_DIR / str(conv_id)
    out.mkdir(parents=True, exist_ok=True)
    try:
        html = page.content()
        (out / "page.html").write_text(html, encoding="utf-8")
    except Exception:
        pass
    if next_data_raw:
        try:
            (out / "next_data.json").write_text(
                json.dumps(next_data_raw, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass
    if api_raw:
        try:
            (out / "api.json").write_text(api_raw, encoding="utf-8")
        except Exception:
            pass


CHATGPT_EXTRACT_JS = """
() => {
    const messages = [];
    const items = document.querySelectorAll('[data-message-author-role]');
    items.forEach(el => {
        const role = el.getAttribute('data-message-author-role');
        let text;
        try { text = el.innerText?.trim(); } catch(e) {
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
            const parts = [];
            while (walker.nextNode()) {
                const v = walker.currentNode.textContent.trim();
                if (v) parts.push(v);
            }
            text = parts.join('\\n');
        }
        if (!text || text.length < 5) return;
        let timestamp = null;
        const te = el.querySelector('time, [datetime], [data-time]');
        if (te) {
            const dt = te.getAttribute('datetime') || te.getAttribute('data-time') || te.innerText;
            if (dt) timestamp = dt.trim().slice(0, 19).replace('T', ' ');
        }
        messages.push({ role, content: text, timestamp });
    });
    const title = (() => {
        const links = document.querySelectorAll('a[href*="/c/"], a[href*="/chat/"], nav a, [class*="sidebar"] a');
        for (const el of links) {
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length > 1 && t.length < 200) return t;
        }
        const fallbacks = document.querySelectorAll('[class*="sidebar"] [class*="active"], [class*="title"]');
        for (const el of fallbacks) {
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length > 1 && t.length < 200) return t;
        }
        return document.title || 'ChatGPT Chat';
    })();
    const chatId = (location.pathname.match(/\\/([^\\/?#]+)$/) || [])[1] || '';
    return JSON.parse(JSON.stringify({ title, chatId, messages, sourceUrl: location.href }));
}
"""

SCROLL_CONTAINER_JS = """
() => {
    const el = document.querySelector('nav[class*="scrollport"]')
        || document.querySelector('[class*="virtual"]')
        || document.querySelector('[class*="overflow-y-auto"]')
        || document.querySelector('main')
        || document.scrollingElement;
    if (!el) return null;
    return { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
}
"""

SCROLL_TO_TOP_JS = """
() => {
    const el = document.querySelector('nav[class*="scrollport"]')
        || document.querySelector('[class*="virtual"]')
        || document.querySelector('[class*="overflow-y-auto"]')
        || document.querySelector('main')
        || document.scrollingElement;
    if (!el) return false;
    el.scrollTop = 0;
    el.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));
    return el.scrollTop === 0;
}
"""

FIND_LOAD_MORE_JS = """
() => {
    const keywords = ['Show older', 'Continue', 'Load more', 'Load More',
                      'Show more', 'Показать ещё', 'Продолжить', 'Загрузить ещё'];
    const buttons = document.querySelectorAll('button, a, [role="button"]');
    for (const el of buttons) {
        const text = (el.innerText || el.textContent || '').trim();
        if (keywords.some(kw => text.includes(kw)) && text.length < 60) {
            el.scrollIntoView({block:'center'});
            el.click();
            return text;
        }
    }
    return null;
}
"""

MESSAGE_COUNT_JS = """
() => document.querySelectorAll('[data-message-author-role]').length;
"""


GPT4O_FILTER_PATTERNS = [
    "GPT-4o returned",
    "returned 1 images",
    "returned 1 image",
    "do not say or show ANYTHING",
    "Please end this turn now",
]


def _validate_messages(messages):
    if not messages:
        return messages
    cleaned = []
    for m in messages:
        c = m.get("content", "").strip()
        if not c or len(c) < 5:
            continue
        if m.get("role") == "user" and len(c) < 10:
            continue
        if any(p in c for p in GPT4O_FILTER_PATTERNS):
            continue
        cleaned.append(m)
    return cleaned


def _wait_for_stable(page, cancel_check, timeout_ms=20000):
    """Wait until page stops changing (height + msg count stable)."""
    prev_height = -1
    prev_count = -1
    stable_rounds = 0
    waited = 0
    while waited < timeout_ms:
        if cancel_check and cancel_check():
            return False
        ch = page.evaluate(SCROLL_CONTAINER_JS)
        height = ch["scrollHeight"] if ch else 0
        count = page.evaluate(MESSAGE_COUNT_JS)
        if height == prev_height and count == prev_count:
            stable_rounds += 1
            if stable_rounds >= 3:
                return True
        else:
            stable_rounds = 0
        prev_height = height
        prev_count = count
        page.wait_for_timeout(1000)
        waited += 1000
    return stable_rounds >= 3


def ensure_conversation_loaded(page, log_progress=None, cancel_check=None):
    """Phase 1: scroll-up + Load More until full history is loaded."""
    prev_height = 0
    prev_count = 0
    stable = 0
    max_iter = 60

    page.wait_for_timeout(2000)

    for i in range(max_iter):
        if cancel_check and cancel_check():
            break

        page.evaluate(SCROLL_TO_TOP_JS)

        btn_text = page.evaluate(FIND_LOAD_MORE_JS)
        if btn_text and log_progress:
            log_progress(f"[CHATGPT] clicked '{btn_text}'")

        page.wait_for_timeout(1500)

        ch = page.evaluate(SCROLL_CONTAINER_JS)
        height = ch["scrollHeight"] if ch else 0
        count = page.evaluate(MESSAGE_COUNT_JS)

        if log_progress and i % 5 == 0:
            log_progress(f"[CHATGPT] loading iter {i}: scrollH={height} msgs={count}")

        if height == prev_height and count == prev_count:
            stable += 1
            if stable >= 3:
                if log_progress:
                    log_progress(f"[CHATGPT] conversation stable ({i+1} iters, {count} msgs)")
                return True
        else:
            stable = 0

        prev_height = height
        prev_count = count

    if log_progress:
        log_progress(f"[CHATGPT] max iterations reached ({prev_count} msgs)")
    return prev_count > 0


def extract_chatgpt_dom(page, url, log_progress=None, cancel_check=None) -> dict | None:
    page.wait_for_timeout(3000)

    merged, seen = [], set()
    title, chat_id = '', ''

    for i in range(60):
        if cancel_check and cancel_check():
            break

        raw = page.evaluate(CHATGPT_EXTRACT_JS)
        data = json.loads(raw) if isinstance(raw, str) else raw

        if not title:
            title = data.get("title", "")
        if not chat_id:
            chat_id = data.get("chatId", "")

        new_count = 0
        for msg in data.get("messages", []):
            c = str(msg.get("content") or "").strip()
            if not c or len(c) < 5:
                continue
            key = c[:120]
            if key not in seen:
                seen.add(key)
                merged.append({"role": msg.get("role", "assistant"), "content": c})
                new_count += 1

        if new_count == 0:
            break

        if i % 10 == 0 and i > 0 and log_progress:
            log_progress(f"ChatGPT scroll {i}: {len(merged)} msgs")

        page.evaluate(SCROLL_TO_TOP_JS)
        page.wait_for_timeout(1500)

    if not merged:
        return None

    cleaned = _validate_messages(merged)
    if not cleaned:
        return None

    return {
        "schema_version": 1,
        "source": "dom",
        "chat_id": chat_id or "",
        "title": title or "ChatGPT Chat",
        "source_url": url,
        "messages": cleaned,
    }


def _get_conv_id(url):
    match = re.search(r'/c/([a-f0-9-]+)', url)
    return match.group(1) if match else None


def extract_chatgpt_pipeline(page, url, capture_result=None, log_progress=None, cancel_check=None) -> ConversationModel | None:
    """Three-tier pipeline: API → NEXT_DATA → DOM."""
    try:
        conv_id = _get_conv_id(url)

        # Tier 1: passive API capture
        model = None
        source_label = "api"
        if capture_result:
            if log_progress:
                log_progress(f"[CHATGPT] API captured: {len(capture_result.get('mapping', {}))} nodes")
            model = IRBuilder.build(Provider.CHATGPT_API, capture_result, url=url, log_func=log_progress)
            _save_diagnostics(page, conv_id, api_raw=json.dumps(capture_result, ensure_ascii=False))

        # Tier 2: __NEXT_DATA__
        if not model or not model.messages or len(model.messages) < 2:
            if log_progress:
                log_progress("[CHATGPT] API failed, trying __NEXT_DATA__")
            next_data = from_next_data(page)
            if next_data and next_data.messages and len(next_data.messages) >= 2:
                model = next_data
                source_label = "next_data"
                _save_diagnostics(page, conv_id, next_data_raw=model.metadata)
                if log_progress:
                    log_progress(f"[CHATGPT] __NEXT_DATA__: {len(model.messages)} msgs")

        # Tier 3: DOM fallback
        if not model or not model.messages or len(model.messages) < 2:
            if log_progress:
                log_progress("[CHATGPT] API/NEXT_DATA failed, falling back to DOM")
            ok = ensure_conversation_loaded(page, log_progress=log_progress, cancel_check=cancel_check)
            if not ok and log_progress:
                log_progress("[CHATGPT] WARN: conversation may not be fully loaded")
            dom_data = extract_chatgpt_dom(page, url, log_progress=log_progress, cancel_check=cancel_check)
            if dom_data and dom_data.get("messages"):
                model = IRBuilder.build(Provider.CHATGPT_DOM, dom_data)
                source_label = "dom"
                if log_progress:
                    log_progress(f"[CHATGPT] DOM source: {len(dom_data['messages'])} msgs")

        if not model:
            if log_progress:
                log_progress("[CHATGPT] all sources failed")
            return None

        model.metadata["source"] = source_label

        if log_progress:
            log_progress(f"[CHATGPT] selected source '{source_label}' ({len(model.messages)} msgs)")
        return model

    except Exception as e:
        if log_progress:
            log_progress(f"[CHATGPT] pipeline error: {e}")
        return None
