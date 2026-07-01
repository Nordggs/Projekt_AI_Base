import json
import time

CLAUDE_EXTRACT_JS = """
() => {
    const container = document.querySelector('#main-content')
        || document.querySelector('[class*="overflow-y-auto"][class*="overflow-x-hidden"]')
        || document.body;
    const messages = [];
    const seen = new Set();

    const userEls = container.querySelectorAll('div[data-testid="user-message"]');
    const assistantEls = container.querySelectorAll('div.font-claude-response');

    const all = [];
    userEls.forEach(el => { all.push({ el, role: 'user' }); });
    assistantEls.forEach(el => { all.push({ el, role: 'assistant' }); });

    all.sort((a, b) => {
        if (a.el === b.el) return 0;
        const pos = a.el.compareDocumentPosition(b.el);
        if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
        if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
        return 0;
    });

    all.forEach(({ el, role }) => {
        let text;
        try { text = el.innerText?.trim(); } catch (e) {
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
            const parts = [];
            while (walker.nextNode()) {
                const v = walker.currentNode.textContent.trim();
                if (v) parts.push(v);
            }
            text = parts.join('\\n').trim();
        }
        if (!text) return;
        const key = text.slice(0, 200);
        if (seen.has(key)) return;
        seen.add(key);

        let timestamp = null;
        const group = el.closest('.group');
        if (group) {
            const timeEl = group.querySelector('time, [datetime], [data-timestamp]');
            if (timeEl) {
                const dt = timeEl.getAttribute('datetime') || timeEl.getAttribute('data-timestamp') || timeEl.innerText;
                if (dt) timestamp = dt.trim().slice(0, 19).replace('T', ' ');
            }
        }
        if (!timestamp) {
            const small = el.querySelector('span, small, [class*="ts"], [class*="time"]');
            if (small) {
                const t = (small.innerText || '').trim();
                if (t && t.length < 30 && /^\\d/.test(t)) timestamp = t;
            }
        }
        messages.push({ role, content: text, timestamp });
    });

    const title = (() => {
        const titleEl = container.querySelector('[data-testid="chat-title-split"]');
        if (titleEl) {
            const t = (titleEl.innerText || '').trim();
            if (t) return t;
        }
        const sidebar = document.querySelector('[class*="sidebar"] [class*="active"], a[href*=\"/chat/\"][class*=\"active\"]');
        if (sidebar) {
            const t = (sidebar.innerText || '').trim();
            if (t) return t;
        }
        return document.title || 'Claude Chat';
    })();

    const chatId = (location.pathname.match(/\\/chat\\/([^\\/?#]+)/) || [])[1] || '';
    return JSON.parse(JSON.stringify({ title, chatId, messages, sourceUrl: location.href }));
}
"""

CLAUDE_SCROLL_TOP_JS = """
() => {
    const el = document.querySelector('#main-content [class*="overflow-y-auto"]')
        || document.querySelector('[class*="overflow-y-auto"][class*="overflow-x-hidden"]')
        || document.querySelector('#main-content')
        || document.scrollingElement;
    if (!el) return false;
    el.scrollTo(0, -99999);
    el.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));
    return el.scrollTop <= 0;
}
"""

CLAUDE_SCROLL_JS = """
() => {
    const el = document.querySelector('#main-content [class*="overflow-y-auto"]')
        || document.querySelector('[class*="overflow-y-auto"][class*="overflow-x-hidden"]')
        || document.querySelector('#main-content')
        || document.scrollingElement;
    if (!el) return false;
    const before = el.scrollTop;
    el.scrollBy(0, 1200);
    el.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));
    return el.scrollTop > before;
}
"""


def extract_claude_dom(page, url, log_progress=None, cancel_check=None) -> dict | None:
    page.wait_for_timeout(2000)

    # Scroll to top first to trigger virtualization rendering of earliest messages
    try:
        page.evaluate(CLAUDE_SCROLL_TOP_JS)
        page.wait_for_timeout(2000)
    except Exception:
        pass

    merged, seen = [], set()
    title, chat_id = '', ''
    stable = 0
    no_new_rounds = 0
    last_fp = ""
    MAX_STABLE = 6
    MAX_NO_NEW = 10
    MAX_ITER = 50

    for i in range(MAX_ITER):
        if cancel_check and cancel_check():
            break

        raw = page.evaluate(CLAUDE_EXTRACT_JS)
        data = json.loads(raw) if isinstance(raw, str) else raw

        if not title:
            title = data.get("title", "")
        if not chat_id:
            chat_id = data.get("chatId", "")

        new_count = 0
        for msg in data.get("messages", []):
            c = str(msg.get("content") or "").strip()
            if not c:
                continue
            key = c[:120]
            if key not in seen:
                seen.add(key)
                merged.append({"role": msg.get("role", "assistant"), "content": c})
                new_count += 1

        if new_count == 0:
            stable += 1
        else:
            stable = 0

        msgs = data.get("messages", [])
        current_fp = ""
        if msgs:
            last = msgs[-1]
            current_fp = (last.get("content") or "")[:200]
        if current_fp == last_fp:
            no_new_rounds += 1
        else:
            no_new_rounds = 0
        last_fp = current_fp

        if stable >= MAX_STABLE and i > 10:
            break
        if no_new_rounds >= MAX_NO_NEW and i > 10:
            break

        if i % 10 == 0 and i > 0 and log_progress:
            log_progress(f"Claude scroll {i}/{MAX_ITER}: {len(merged)} msgs")

        page.evaluate(CLAUDE_SCROLL_JS)
        page.wait_for_timeout(1500)

    if not merged:
        return None

    return {
        "schema_version": 1,
        "source": "claude",
        "chat_id": chat_id or "",
        "title": title or "Claude Chat",
        "source_url": url,
        "messages": merged,
    }


def extract_claude_hybrid(page, url, log_progress=None, cancel_check=None):
    try:
        data = extract_claude_dom(page, url, log_progress=log_progress, cancel_check=cancel_check)
        if data and data.get("messages"):
            msgs = data["messages"]
            user_count = sum(1 for m in msgs if m.get("role") == "user")
            assistant_count = sum(1 for m in msgs if m.get("role") == "assistant")
            if assistant_count == 0:
                if log_progress:
                    log_progress(f"[CLAUDE] GUARD: 0 assistant messages ({user_count} user) → skip")
                return None
            return data
    except Exception as e:
        if log_progress:
            log_progress(f"[CLAUDE] extract error: {e}")

    if log_progress:
        log_progress("[CLAUDE] DOM extraction failed, no fallback available")
    return None
