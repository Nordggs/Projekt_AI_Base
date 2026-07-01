import json
import re
import time

from exporters.cdp_snapshot import CdpSnapshotExtractor

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
        let timestamp = null;
        const te = el.querySelector('time, [datetime], [class*="time"], [data-timestamp]');
        if (te) {
            const dt = te.getAttribute('datetime') || te.getAttribute('data-timestamp') || te.getAttribute('title') || te.innerText;
            if (dt) timestamp = dt.trim().slice(0, 19).replace('T', ' ');
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
    if (messages.length === 0) {
        const allText = document.body?.innerText || '';
        const parts = allText.split('\\n').filter(l => l.trim().length > 20);
        messages.push({ role: 'assistant', content: parts.slice(0, 5).join('\\n\\n') || 'No structured messages found' });
    }
    const title = (() => {
        const links = document.querySelectorAll('a[href*="/c/"]');
        for (const el of links) {
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length > 1 && t.length < 200) return t;
        }
        const fallbacks = document.querySelectorAll('[class*="sidebar"] [class*="active"], [class*="menu"] [class*="active"], [class*="conversation"] [class*="active"]');
        for (const el of fallbacks) {
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length > 1 && t.length < 200) return t;
        }
        return document.title || 'Qwen Chat';
    })();
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

    title_diag = page.evaluate("""
        () => {
            const sel = ['[class*="title"]', '[class*="name"]', 'h1', '[class*="heading"]', '[class*="tip-text"]', '[class*="chat-item-active"]'];
            const out = [];
            for (const s of sel) {
                const el = document.querySelector(s);
                if (el) {
                    const t = (el.innerText || el.textContent || '').trim().substring(0, 100);
                    out.push({sel, tag: el.tagName, cls: (el.className || '').substring(0, 80), text: t});
                }
            }
            return JSON.stringify(out);
        }
    """)
    log_progress(f"[QWEN_TITLE] {title_diag}")

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


def normalize_snapshot(messages, url):
    if not messages:
        return None
    chat_id = ""
    m = __import__("re").search(r"/([^/?]+)$", url)
    if m:
        chat_id = m.group(1)
    return {
        "schema_version": 1,
        "source": "qwen",
        "chat_id": chat_id or "",
        "title": "Qwen Chat",
        "source_url": url,
        "messages": messages,
    }


def extract_qwen_hybrid(page, url, log_progress=None, cancel_check=None, cancel_epoch=0):
    try:
        data = extract_qwen_dom(page, url, log_progress=log_progress, cancel_check=cancel_check, cancel_epoch=cancel_epoch)
        if data and data.get("messages"):
            return data
    except Exception as e:
        if log_progress:
            try:
                url_info = page.url
            except Exception:
                url_info = "<unavailable>"
            log_progress(f"[QWEN] evaluate failed: {e} — page.url={url_info}")
        pass

    if log_progress:
        log_progress("[QWEN] falling back to DOMSnapshot...")

    snap = CdpSnapshotExtractor(page)
    try:
        raw = snap.capture_conversation()
        if not raw:
            return None
        data = normalize_snapshot(raw, url)
        if log_progress:
            log_progress(f"[QWEN] snapshot: {len(raw)} msgs")
        return data
    finally:
        snap.close()
