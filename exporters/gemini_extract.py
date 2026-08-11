import json
import time
import re
import os
import gzip
from datetime import datetime

GEMINI_EXTRACT_JS = """
() => {
    const messages = [];
    document.querySelectorAll("message-content, model-response, user-query").forEach(el => {
        let text;
        try {
            text = el.innerText?.trim();
        } catch(e) {
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
            const parts = [];
            while (walker.nextNode()) {
                const v = walker.currentNode.textContent.trim();
                if (v) parts.push(v);
            }
            text = parts.join('\\n');
        }
        if (!text) return;
        // Strip Gemini UI labels from content
        text = text.replace(/^(Ответ Gemini|Ваш запрос|Your query|Gemini's response)\\s*\\n*/i, '').trim();
        if (!text) return;
        const role = el.tagName === "USER-QUERY" ? "user"
                  : el.tagName === "MESSAGE-CONTENT" ? "assistant"
                  : "assistant";
        let timestamp = null;
        const te = el.querySelector('time, [datetime], [class*="time"], [data-timestamp]');
        if (te) {
            const dt = te.getAttribute('datetime') || te.getAttribute('data-timestamp') || te.getAttribute('title') || te.innerText;
            if (dt) timestamp = dt.trim().slice(0, 19).replace('T', ' ');
        }
        if (!timestamp) {
            // Fallback: scan for timestamp-like text in shallow children
            const kids = el.children;
            for (let i = 0; i < kids.length; i++) {
                const t = (kids[i].innerText || '').replace(/\\s+/g, ' ').trim();
                if (t && t.length < 30 && t.length > 5 && /^\\d/.test(t)) {
                    timestamp = t;
                    break;
                }
            }
        }
        messages.push({ role, content: text, timestamp });
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

SCROLL_JS = """
(dy) => {
    const el = document.querySelector('cdk-virtual-scroll-viewport')
        || document.querySelector('main')
        || document.scrollingElement;
    if (!el) return false;
    el.scrollBy(0, dy);
    el.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('resize'));
    return true;
}
"""

TOKEN_REGEX = r'"SNlM0e":"([^"]+)"'

# Set True to attempt Batchexecute RPC for Gemini (experimental — endpoint returns 404).
# When False, extract_gemini_rpc returns None immediately → DOM fallback.
GEMINI_ENABLE_RPC = False

# Set True to write observatory/probe files to raw_responses/gemini/ during RPC attempts.
# Requires GEMINI_ENABLE_RPC = True to have any effect.
GEMINI_DEBUG = False

GEMINI_TITLE_JS = """
() => {
    const t = (document.title || '').replace(/\\s*[–-]\\s*Gemini.*/i, '').trim();
    return t || 'Gemini Chat';
}
"""


def extract_gemini_dom(page, url, log_progress=None, cancel_check=None, cancel_epoch=0) -> dict | None:
    # Quick check: if no message elements exist on page, bail fast
    has_msgs = page.evaluate("""() => !!document.querySelector('message-content, model-response, user-query')""")
    if not has_msgs:
        return None

    merged, seen = [], set()
    title_val, chat_id = '', ''
    same_count = 0
    last_len = 0

    chat_id = url.rstrip("/").rsplit("/", 1)[-1]

    # Phase 1: scroll UP (negative) — most chats have newest at bottom
    for _ in range(200):
        if cancel_check and cancel_check():
            if cancel_epoch and (time.time() - cancel_epoch) < 0.3:
                pass
            else:
                break

        data = page.evaluate(GEMINI_EXTRACT_JS)
        new_count = 0
        if not title_val:
            title_val = data.get("title", "")

        for msg in data.get("messages", []):
            c = str(msg.get("content") or "").strip()
            if not c:
                continue
            key = c[:120]
            if key not in seen:
                seen.add(key)
                merged.append({"role": msg.get("role", "assistant"), "content": c})
                new_count += 1

        if len(merged) == last_len:
            same_count += 1
        else:
            same_count = 0
        last_len = len(merged)

        if same_count >= 8:
            break

        if len(merged) > 0 and len(merged) % 20 == 0 and log_progress:
            log_progress(f"scroll UP: {len(merged)} msgs")

        page.evaluate(SCROLL_JS, -2000)
        page.wait_for_timeout(800)

    # Phase 2: scroll DOWN (positive) as fallback (only if we found anything)
    if merged:
        down_stable = 0
        for i in range(80):
            if cancel_check and cancel_check():
                break
            data = page.evaluate(GEMINI_EXTRACT_JS)
            added = 0
            for msg in data.get("messages", []):
                c = str(msg.get("content") or "").strip()
                if not c:
                    continue
                key = c[:120]
                if key not in seen:
                    seen.add(key)
                    merged.append({"role": msg.get("role", "assistant"), "content": c})
                    added += 1
            if added == 0:
                down_stable += 1
            else:
                down_stable = 0
            if down_stable >= 5 and i > 5:
                break
            if i % 10 == 0 and log_progress and added > 0:
                log_progress(f"scroll DOWN {i}: +{added} → {len(merged)} msgs")
            page.evaluate(SCROLL_JS, 2000)
            page.wait_for_timeout(800)

    if not merged:
        return None

    # Merge consecutive same-role messages (Gemini splits AI responses across DOM elements)
    deduped = []
    for msg in merged:
        if deduped and deduped[-1]["role"] == msg["role"]:
            deduped[-1]["content"] += "\n\n" + msg["content"]
        else:
            deduped.append({"role": msg["role"], "content": msg["content"]})

    return {
        "schema_version": 1,
        "source": "gemini",
        "chat_id": chat_id or "",
        "title": title_val or "Gemini Chat",
        "source_url": url,
        "messages": deduped,
    }


def _parse_gemini_batchexecute(text: str, chat_id: str, url: str) -> dict | None:
    """Parse Gemini /_/Batchexecute response. Strips prefix, walks nested arrays for message-like strings."""
    raw = text.strip()
    # Strip )]}' prefix
    if raw.startswith(")"):
        idx = raw.find("\n")
        if idx != -1:
            raw = raw[idx + 1:]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[[")
        if start == -1:
            return None
        end = raw.rfind("]]") + 2
        try:
            data = json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None

    messages = []
    seen_texts = set()

    def walk(obj, depth=0):
        if depth > 8:
            return
        if isinstance(obj, str):
            txt = obj.strip()
            if 20 < len(txt) < 50000 and txt not in seen_texts:
                seen_texts.add(txt)
                role = "assistant" if len(messages) % 2 == 1 else "user"
                messages.append({"role": role, "content": txt})
        elif isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)
        elif isinstance(obj, dict):
            for val in obj.values():
                walk(val, depth + 1)

    walk(data)

    if len(messages) < 2:
        return None

    # Try to get title from page (passed via url)
    title = f"Gemini Chat {chat_id[:8]}"

    return {
        "schema_version": 2,
        "source": "api",
        "chat_id": chat_id,
        "title": title,
        "source_url": url,
        "messages": messages,
    }


def _save_gemini_observation(raw_text: str, chat_id: str, url: str, parsed):
    """Pure observatory — saves raw + fingerprint + parsed ALWAYS. Never affects pipeline."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_size = len(raw_text)

    # Fingerprint on raw text only (not parsed)
    depth = 0
    max_depth = 0
    for ch in raw_text[:100000]:
        if ch in "[{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch in "]}":
            depth = max(0, depth - 1)

    preview = raw_text[:500]
    # Sanitize for JSON: strip surrogates
    preview = preview.encode("utf-8", errors="replace").decode("utf-8")
    alpha = float(sum(1 for c in preview if c.isprintable() and not c.isspace()))
    density = alpha / max(1, len(preview))

    observation = {
        "meta": {
            "url": url,
            "chat_id": chat_id,
            "timestamp": ts,
            "f_req_payload": '[["hNvQHb","[[]]",null,"1"]]',
        },
        "raw": {
            "type": type(raw_text).__name__,
            "size": raw_size,
            "preview": preview,
        },
        "fingerprint_raw": {
            "depth_estimate": max_depth,
            "string_density": round(density, 4),
            "has_prefix_newline": raw_text.startswith(")"),
            "starts_with_json": raw_text.lstrip()[:1] in ("[", "{"),
        },
        "parsed": parsed if parsed is not None else {"_error": "parse returned None"},
    }

    obs_dir = os.path.join("raw_responses", "gemini")
    os.makedirs(obs_dir, exist_ok=True)

    name = f"chat_{chat_id[:12]}_{ts}"

    path = os.path.join(obs_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(observation, f, indent=2, ensure_ascii=False)

    raw_safe = raw_text.encode("utf-8", errors="replace").decode("utf-8")
    raw_path = os.path.join(obs_dir, f"{name}_raw.txt")
    if raw_size > 5 * 1024 * 1024:
        raw_path += ".gz"
        with gzip.open(raw_path, "wt", encoding="utf-8") as f:
            f.write(raw_safe)
    else:
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_safe)


def extract_gemini_via_api_intercept(page, url, log_progress=None, cancel_check=None, timeout_ms=15000):
    """Reload page and intercept Batchexecute response with conversation data."""
    chat_id = url.rstrip("/").rsplit("/", 1)[-1]
    captured = {"data": None}

    def on_response(response):
        if captured["data"] is not None:
            return
        try:
            path = response.url.split("?")[0]
            if "batchexecute" not in path.lower():
                return
            if response.status != 200:
                return
            body = response.text()
            if len(body) < 2000:
                return
            data = _parse_gemini_batchexecute(body, chat_id, url)
            if data and len(data.get("messages", [])) >= 2:
                captured["data"] = data
                if log_progress:
                    log_progress(f"[GEMINI] API intercept: {len(data['messages'])} msgs ({len(body)} bytes)")
        except Exception as e:
            if log_progress:
                log_progress(f"[GEMINI] API intercept parse error: {e}")

    page.on("response", on_response)
    page.reload()

    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        if cancel_check and cancel_check():
            break
        if captured["data"] is not None:
            break
        page.wait_for_timeout(500)

    page.remove_listener("response", on_response)
    if captured["data"] is None and log_progress:
        log_progress("[GEMINI] API intercept: no conversation data captured")
    return captured["data"]


def _save_token_probe(
    page,
    url,
    chat_id,
    *,
    exit_reason: str,
    exception: Exception | None = None,
):
    """Observe Gemini page state when RPC fails. Pure diagnostic, never affects pipeline."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        data = page.evaluate(f"""
            () => {{
                const html = document.documentElement.innerHTML;
                const scripts = [...document.querySelectorAll('script')];

                const markers = ['/_/Batchexecute', 'SNlM0e', 'f.req', 'rpcids', 'hNvQHb'];

                /* Similar keys */
                const simSet = new Set();
                const simRe = /SNlM0[a-z]/g;
                let mm;
                while ((mm = simRe.exec(html)) !== null) simSet.add(mm[0]);

                /* Script scan */
                const matched = scripts
                    .map((s, i) => {{
                        const t = s.textContent || '';
                        const m = markers.filter(x => t.includes(x));
                        return {{ index: i, length: t.length, matched_markers: m, preview: m.length > 0 ? t.replace(/\\s+/g, ' ').substring(0, 300) : '' }};
                    }})
                    .filter(s => s.matched_markers.length > 0)
                    .sort((a, b) => b.length - a.length)
                    .slice(0, 5);

                /* Token regex */
                const tr = /{TOKEN_REGEX}/;
                const tm = html.match(tr);

                return {{
                    page_url: location.href,
                    page_title: document.title,
                    probe: {{
                        token_found_by_regex: tm !== null,
                        html_size: html.length,
                        script_count: scripts.length
                    }},
                    markers: {{
                        has_batchexecute: html.includes('/_/Batchexecute'),
                        has_SNlM0e: html.includes('SNlM0e'),
                        similar_keys: [...simSet].sort(),
                        has_f_req: html.includes('f.req'),
                        has_rpcids: html.includes('rpcids'),
                        has_hNvQHb: html.includes('hNvQHb')
                    }},
                    matching_scripts: matched
                }};
            }}
        """)

        probe_dir = os.path.join("raw_responses", "gemini", "token_probe")
        os.makedirs(probe_dir, exist_ok=True)

        name = f"chat_{chat_id[:12]}_{ts}"

        meta = {
            "url": url,
            "chat_id": chat_id[:12],
            "page_url": data.get("page_url", ""),
            "page_title": data.get("page_title", ""),
            "timestamp": ts,
            "exit_reason": exit_reason,
        }
        if exception is not None:
            meta["exception_type"] = type(exception).__name__
            meta["exception_message"] = str(exception)[:500]

        record = {
            "meta": meta,
            "probe": data.get("probe", {}),
            "markers": data.get("markers", {}),
            "matching_scripts": data.get("matching_scripts", []),
        }

        path = os.path.join(probe_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def extract_gemini_rpc(page, url) -> dict | None:
    if not GEMINI_ENABLE_RPC:
        return None
    chat_id = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        token = page.evaluate(f"""
            () => {{
                const html = document.documentElement.innerHTML;
                const m = html.match(/{TOKEN_REGEX}/);
                return m ? m[1] : null;
            }}
        """)
        if not token:
            if GEMINI_DEBUG:
                _save_token_probe(page, url, chat_id, exit_reason="token_not_found")
            return None

        result = page.evaluate("""
            async ({token}) => {
                const body = new URLSearchParams();
                body.set('f.req', JSON.stringify([["hNvQHb","[[]]",null,"1"]]));
                body.set('at', token);
                try {
                    const r = await fetch('/_/Batchexecute', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body});
                    return await r.text();
                } catch(e) { return null; }
            }
        """, {"token": token})
        if not result:
            return None

        parsed = _parse_gemini_batchexecute(result, chat_id, url)
        if GEMINI_DEBUG:
            try:
                _save_gemini_observation(result, chat_id, url, parsed)
            except Exception:
                pass
        return parsed
    except Exception as exc:
        if GEMINI_DEBUG:
            try:
                _save_token_probe(
                    page, url, chat_id,
                    exit_reason="exception",
                    exception=exc,
                )
            except Exception:
                pass
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
        # Open sidebar via keyboard shortcut (Ctrl+Shift+H)
        page.keyboard.press("Control+Shift+h")
        page.wait_for_timeout(3000)

        all_urls = set()
        seen_set = set()
        stable = 0
        MAX_STABLE = 8
        MAX_ITER = 80

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

            # Scroll the history container (Angular cdk-virtual-scroll / infinite-scroller)
            page.evaluate("""
                () => {
                    let el = document.querySelector('.chat-history-scroll-container');
                    if (!el) el = document.querySelector('infinite-scroller, [class*="history"]');
                    if (!el) {
                        const links = document.querySelectorAll('a[href*="/app/"]');
                        if (links.length > 0) {
                            let p = links[links.length - 1].parentElement;
                            while (p && p !== document.body) {
                                if (p.scrollHeight > p.clientHeight + 5) { el = p; break; }
                                p = p.parentElement;
                            }
                        }
                    }
                    if (el) { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll')); }
                }
            """)
            page.wait_for_timeout(400)

        return list(all_urls)
    except Exception:
        return []
