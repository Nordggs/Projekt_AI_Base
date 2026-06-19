import json
import time

QWEN_DEBUG_JS = """
() => {
    const info = {
        url: location.href,
        title: document.title,
        bodyPreview: document.body?.innerHTML?.substring(0, 3000) || '',
        selectors: {}
    };
    const candidates = ['[class*="message"]', '[class*="chat"]', '[class*="conversation"]', 'main', 'article', '[role="main"]'];
    candidates.forEach(sel => {
        const els = document.querySelectorAll(sel);
        info.selectors[sel] = els.length;
    });
    return JSON.stringify(info);
}
"""

QWEN_EXTRACT_JS = """
() => {
    const messages = [];
    const selectors = [
        '.message-item', '[class*="message-item"]',
        '.chat-message', '[class*="chat-message"]',
        '[class*="conversation-item"]',
        '[data-role]',
        '.qwen-message', '[class*="qwen-message"]',
        '.user-message', '.assistant-message',
        '[class*="user-message"]', '[class*="assistant-message"]',
        '.msg-item', '[class*="msg-item"]',
    ];
    let els = [];
    for (const sel of selectors) {
        const found = document.querySelectorAll(sel);
        if (found.length > 0) { els = found; break; }
    }
    if (!els || els.length === 0) {
        const container = document.querySelector('main') || document.querySelector('[role="main"]') || document.querySelector('.chat-container') || document.querySelector('[class*="chat"]');
        if (container) {
            els = container.querySelectorAll(':scope > div, :scope > section > div');
        }
    }
    els.forEach(el => {
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
        let role = 'assistant';
        const cls = (el.className || '').toLowerCase();
        const dataRole = el.getAttribute('data-role') || '';
        if (cls.includes('user') || dataRole === 'user') role = 'user';
        if (cls.includes('assistant') || dataRole === 'assistant') role = 'assistant';
        messages.push({ role, content: text });
    });
    if (messages.length === 0) {
        const allText = document.body?.innerText || '';
        const parts = allText.split('\\n').filter(l => l.trim().length > 20);
        messages.push({ role: 'assistant', content: parts.slice(0, 5).join('\\n\\n') || 'No structured messages found' });
    }
    const title = document.title || 'Qwen Chat';
    const chatId = (location.pathname.match(/\\/([^\\/?#]+)$/) || [])[1] || '';
    return JSON.parse(JSON.stringify({ title, chatId, messages, sourceUrl: location.href }));
}
"""

QWEN_SCROLL_JS = """
() => {
    const el = document.querySelector('[class*="virtual"]')
        || document.querySelector('[class*="scroll"]')
        || document.querySelector('main')
        || document.scrollingElement;
    if (!el) return false;
    const before = el.scrollTop;
    el.scrollBy(0, 1000);
    el.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));
    return el.scrollTop > before;
}
"""


def extract_qwen_dom(page, url, log_progress=None, cancel_check=None, cancel_epoch=0) -> dict | None:
    page.wait_for_timeout(2000)

    debug_info = page.evaluate(QWEN_DEBUG_JS)
    debug = json.loads(debug_info)

    log_progress(f"[QWEN_DEBUG] title={debug['title']}, selectors={json.dumps(debug['selectors'])}")

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
            if cancel_epoch and (time.time() - cancel_epoch) < 0.3:
                pass
            else:
                break

        raw = page.evaluate(QWEN_EXTRACT_JS)
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
            log_progress(f"Qwen scroll {i}/{MAX_ITER}: {len(merged)} msgs")

        page.evaluate(QWEN_SCROLL_JS)
        page.wait_for_timeout(1500)

    if not merged:
        return None

    return {
        "schema_version": 1,
        "source": "qwen",
        "chat_id": chat_id or "",
        "title": title or "Qwen Chat",
        "source_url": url,
        "messages": merged,
    }


def discover_qwen_sidebar_urls(page) -> list[str]:
    try:
        urls = page.evaluate("""
            () => [...new Set(
                [...document.querySelectorAll('a[href*="/c/"], a[href*="/chat/"], a[href*="/conversation/"]')]
                    .map(a => {
                        let h = a.getAttribute('href');
                        if (!h) return null;
                        if (h.startsWith('/')) return 'https://chat.qwen.ai' + h;
                        if (h.includes('chat.qwen.ai')) return h;
                        return null;
                    })
                    .filter(Boolean)
            )]
        """)
        return urls or []
    except Exception:
        return []


def discover_all_qwen_urls(page) -> list[str]:
    try:
        all_urls = set()
        seen_set = set()
        stable = 0
        MAX_STABLE = 5
        MAX_ITER = 40

        for _ in range(MAX_ITER):
            urls = page.evaluate("""
                () => [...new Set(
                    [...document.querySelectorAll('a[href*="/c/"], a[href*="/chat/"], a[href*="/conversation/"]')]
                        .map(a => {
                            let h = a.getAttribute('href');
                            if (!h) return null;
                            if (h.startsWith('/')) return 'https://chat.qwen.ai' + h;
                            if (h.includes('chat.qwen.ai')) return h;
                            return null;
                        })
                        .filter(Boolean)
                )]
            """)
            current = set(urls or [])
            if current.issubset(seen_set):
                stable += 1
                if stable >= MAX_STABLE:
                    break
            else:
                stable = 0
                seen_set |= current
                all_urls |= current

            page.evaluate("""
                () => {
                    const c = document.querySelector('[class*="sidebar"], [class*="nav"], [class*="menu"], nav, [class*="list"]');
                    if (c) c.scrollTop += 300;
                }
            """)
            page.wait_for_timeout(300)

        return list(all_urls)
    except Exception:
        return []
