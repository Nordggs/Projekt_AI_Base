import json

GEMINI_EXTRACT_JS = """
() => {
    const messages = [];
    document.querySelectorAll("user-query, model-response").forEach(el => {
        const text = el.innerText?.trim();
        if (!text) return;
        messages.push({
            role: el.tagName === "USER-QUERY" ? "user" : "assistant",
            content: text
        });
    });
    const title = (document.title || '').replace(/\\s*[–-]\\s*Gemini.*/i, '').trim() || 'Gemini Chat';
    const chatId = (location.pathname.match(/\\/app\\/([^\\/?#]+)/) || [])[1] || '';
    return JSON.parse(JSON.stringify({
        title,
        chatId,
        messages,
        sourceUrl: location.href
    }));
}
"""

SCROLL_BOTTOM_JS = """
() => {
    const el = document.scrollingElement
        || document.querySelector('main')
        || document.body;
    if (!el) return false;
    el.scrollTop = el.scrollHeight;
    return el.scrollTop;
}
"""

def extract_gemini_dom(page, url) -> dict | None:
    page.wait_for_timeout(1500)

    merged, seen = [], set()
    title, chat_id = '', ''
    stable = 0
    no_new_rounds = 0
    last_msg_fingerprint = ""
    MAX_STABLE = 6
    MAX_NO_NEW = 10
    MAX_ITER = 50

    for i in range(MAX_ITER):
        data = page.evaluate(GEMINI_EXTRACT_JS)
        new_count = 0

        if not title:
            title = data.get("title", "")
        if not chat_id:
            chat_id = data.get("chatId", "")

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

        messages = data.get("messages", [])
        current_fp = ""
        if messages:
            last = messages[-1]
            current_fp = (last.get("content") or "")[:200]
        if current_fp == last_msg_fingerprint:
            no_new_rounds += 1
        else:
            no_new_rounds = 0
        last_msg_fingerprint = current_fp

        if stable >= MAX_STABLE and i > 10:
            break
        if no_new_rounds >= MAX_NO_NEW and i > 10:
            break

        page.evaluate(SCROLL_BOTTOM_JS)
        page.wait_for_timeout(1200)
        page.evaluate("""
            window.dispatchEvent(new Event('scroll'));
            window.dispatchEvent(new Event('resize'));
        """)
        page.wait_for_timeout(300)

    if not merged:
        return None

    return {
        "schema_version": 1,
        "source": "gemini",
        "chat_id": chat_id or "",
        "title": title or "Gemini Chat",
        "source_url": url,
        "messages": merged,
    }


def extract_gemini_rpc(page, url) -> dict | None:
    try:
        token = page.evaluate("""
            () => {
                const html = document.documentElement.innerHTML;
                const m = html.match(/"SNlM0e":"([^"]+)"/);
                return m ? m[1] : null;
            }
        """)
        if not token:
            return None

        chat_id = url.rstrip("/").rsplit("/", 1)[-1]

        result = page.evaluate("""
            async (token, cid) => {
                const body = new URLSearchParams();
                body.set('f.req', JSON.stringify([["hNvQHb","[[]]",null,"1"]]));
                body.set('at', token);
                try {
                    const r = await fetch('/_/Batchexecute', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body});
                    return await r.text();
                } catch(e) { return null; }
            }
        """, token, chat_id)
        if not result:
            return None

        return _parse_rpc_response(result, chat_id, url)
    except Exception:
        return None


def _parse_rpc_response(text: str, chat_id: str, url: str) -> dict | None:
    try:
        start = text.find('[[')
        if start == -1:
            return None
        end = text.rfind(']]') + 2
        data = json.loads(text[start:end])
        turns = data[0] if isinstance(data[0], list) else data
        messages = []
        for turn in turns:
            if not isinstance(turn, list) or len(turn) < 4:
                continue
            turn_data = turn[4] if len(turn) > 4 and isinstance(turn[4], list) else turn
            if isinstance(turn_data, list) and len(turn_data) > 0:
                content = turn_data[0]
            else:
                content = str(turn_data) if turn_data else ''
            role = 'assistant'
            if isinstance(content, str) and len(content) < 200:
                if not content.strip():
                    continue
            messages.append({'role': 'user' if i % 2 == 0 else 'assistant', 'content': str(content)})

        if not messages:
            return None

        return {
            "schema_version": 1,
            "source": "gemini",
            "chat_id": chat_id,
            "title": f"Gemini Chat {chat_id[:8]}",
            "source_url": url,
            "messages": messages,
        }
    except Exception:
        return None


def discover_gemini_sidebar_urls(page) -> list[str]:
    try:
        urls = page.evaluate("""
            () => [...new Set(
                [...document.querySelectorAll('a[href*="/app/"]')]
                    .map(a => {
                        let h = a.getAttribute('href');
                        if (!h) return null;
                        if (h.startsWith('/')) return 'https://gemini.google.com' + h;
                        if (h.includes('gemini.google.com')) return h;
                        return null;
                    })
                    .filter(Boolean)
            )]
        """)
        return urls or []
    except Exception:
        return []


def discover_all_gemini_urls(page) -> list[str]:
    try:
        all_urls = set()
        seen_set = set()
        stable = 0
        MAX_STABLE = 5
        MAX_ITER = 40

        for _ in range(MAX_ITER):
            urls = page.evaluate("""
                () => [...new Set(
                    [...document.querySelectorAll('a[href*="/app/"]')]
                        .map(a => {
                            let h = a.getAttribute('href');
                            if (!h) return null;
                            if (h.startsWith('/')) return 'https://gemini.google.com' + h;
                            if (h.includes('gemini.google.com')) return h;
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
                    const c = document.querySelector('[class*="sidebar"], [class*="nav"], [class*="menu"], nav');
                    if (c) c.scrollTop += 300;
                }
            """)
            page.wait_for_timeout(300)

        return list(all_urls)
    except Exception:
        return []
