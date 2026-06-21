# Architecture — AI Chat Exporter v0.5.0-dev

## Overview

Desktop application for exporting AI chat conversations (Gemini, DeepSeek, Qwen) to local Markdown files. No API keys, no cloud dependencies — works through browser automation (Playwright) and Chrome DevTools Protocol (CDP) for Gemini and Qwen.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                        UI (pywebview)                       │
│                   ui/app.html + app.js                      │
│              Two-way bridge via pywebview API                │
└──────┬───────────────────────────────────────────┬───────────┘
       │                                           │
       ▼                                           ▼
┌──────────────┐                        ┌──────────────────────────┐
│  _pw_worker  │                        │    _gw_worker            │
│  (thread)    │                        │    (thread)              │
│              │                        │                          │
│  DeepSeek    │                        │  Gemini (CDP)            │
│  Playwright  │                        │  Qwen (CDP, same tab)    │
│  Browser     │                        │  sync_playwright()       │
└──────┬───────┘                        └──────┬───────────────────┘
       │                                       │
       ▼                                       ▼
┌──────────────┐                        ┌──────────────────────┐
│ DeepSeek     │                        │ Gemini Extraction    │
│ Exporter     │                        │ RPC + DOM fallback   │
│ Scroll Engine│                        │ Scroll-loop ingestor │
│ (legacy)     │                        │                      │
└──────┬───────┘                        │ Qwen Extraction      │
       │                                │ DOM scroll-loop      │
       │                                │ Soft page recovery   │
       │                                │ _cdp_lock            │
       └──────────────┬─────────────────┴──────────────┬───────┘
                      ▼                                ▼
              ┌────────────────┐              ┌────────────────┐
              │  ExportWriter  │              │  ExportWriter   │
              │  raw/gemini/   │              │  raw/qwen/      │
              └────────────────┘              └────────────────┘
```

---

## Components

### 1. Frontend (`ui/`)

- **app.html** — layout: provider cards (Gemini, DeepSeek, Qwen, ChatGPT, Claude), CDP modal, log panel, splash screen
- **app.js** — bridges Python backend via `window.pywebview.api.*`:
  - `addQwenAccount()` → calls `connect_qwen()` — создаёт страницу Qwen в общем CDP
  - `syncQwenAll/SyncSelected` → sends URLs to `sync_qwen()`
  - `cancelQwen()` → per-provider cancel
  - `launchChromeCDP()`, `checkGeminiCDP()` — CDP lifecycle
  - `fadeSplash()` — скрывает splash при готовности bridge
- **app.css** — dark UI theme, modal styles, CDP status indicators, splash overlay
- **icon.png** — window icon (pywebview) + favicon
- **splash.png** — splash screen изображение

### 2. Backend (`main.py`)

#### Two isolated workers (threads)

**`_pw_worker`** — DeepSeek export:
- Owns `self.pw` (Playwright browser)
- Commands: `connect`, `reconnect`, `export_batch`
- Uses `DeepSeekExporter` (scroll engine v2)

**`_gw_worker`** — Gemini + Qwen export:
- Owns `self.gemini_pw`, `self.gemini_page` (CDP-connected)
- Owns `self.qwen_page` (CDP page in same browser context)
- Commands: `connect_gemini`, `export_gemini_batch`, `connect_qwen`, `export_qwen_batch`
- Thread-safe via `_cdp_lock` for browser-level ops (`new_page`, `goto`)

#### Key methods

| Method | Purpose |
|--------|---------|
| `_do_connect_gemini()` | CDP attach: HTTP probe → `connect_over_cdp()` → `ctx.new_page()` |
| `_ensure_gemini_page()` | Ping page; recreate via CDP context if closed |
| `_goto_gemini(url)` | Navigate with auto-retry on "Target closed" |
| `_export_gemini(url)` | Full export pipeline: goto → RPC → DOM → write |
| `_do_export_gemini_batch(urls)` | Loop over URLs, collect results, log totals |
| `_do_connect_qwen()` | Создаёт страницу Qwen в существующем CDP контексте |
| `ensure_qwen_alive()` | Проверяет доступность page (`.url` без evaluate) |
| `_load_qwen_sidebar()` | Goto `chat.qwen.ai/` + wait for sidebar render, return item count |
| `_click_qwen_chat(index)` | Re-query DOM + click `div.chat-item-drag a.chat-item-drag-link[i]` |
| `_wait_qwen_messages()` | Post-click guard: `messages > 0 && /c/ in URL` |
| `_export_qwen(url)` | Sync Selected: load sidebar → click-by-index until URL match → extract |
| `_do_export_qwen_batch(urls)` | Sync All (empty urls): sidebar-only FSM, iterate all items; Sync Selected (urls): delegate to `_export_qwen()` |
| `add_qwen_account()` | API: постановка в очередь connect_qwen |
| `sync_qwen(urls)` | API: проверка CDP alive → enqueue |
| `reconnect_qwen()` | Close + re-create Qwen page |
| `launch_chrome_cdp()` | Launch Chrome with `--remote-debugging-port=9222`, persistent profile |
| `launch_chrome()` | API bridge |

### 3. Extraction

#### Gemini (`exporters/gemini_extract.py`)

**A. RPC extraction** (`extract_gemini_rpc`):
- Extracts `"SNlM0e"` token from page HTML
- POSTs to `/_/Batchexecute` with `hNvQHb` request key
- Parses JSON response into message list
- Fast, but requires token (not always available)

**B. DOM extraction** (`extract_gemini_dom`):
- Scroll-loop ingestor (up to 50 iterations, ~60s total)
- `GEMINI_EXTRACT_JS` — selects Angular components:
  `<user-query>` → `role: "user"`, `<model-response>` → `role: "assistant"`
- `SCROLL_BOTTOM_JS` — scrolls `scrollingElement → main → body` to bottom
- Forced `scroll` + `resize` events to trigger Gemini lazy-load
- Stop conditions (both must pass `i > 10`):
  1. **Stable**: 6 consecutive iterations with zero new deduped messages
  2. **No progress**: 10 iterations with same last-message fingerprint (first 200 chars)

#### Qwen (`exporters/qwen_extract.py` + `exporters/cdp_snapshot.py`)

**Navigation (sidebar-only FSM)** — Qwen uses a React SPA without deep-link hydration or API-based chat list. Navigation is purely UI-driven:
- Source of truth: sidebar DOM (`div.chat-item-drag`), not API, not URL
- Discovery: `document.querySelectorAll('div.chat-item-drag').length` — counts all available chats
- Navigation: `document.querySelectorAll('div.chat-item-drag a.chat-item-drag-link')[i].click()` — re-query DOM per click to avoid stale NodeList
- Validation: `messages > 0 && location.href.includes('/c/')` — wait for post-click invariant
- No `goto(/c/{id})`, no `location.href`, no `a[href]` selectors — SPA ignores direct URL navigation

**Message extraction** — две стратегии, попытка evaluate → fallback на DOMSnapshot:
- **Fast path** (`extract_qwen_dom`): DOM scroll-loop ingestor с `page.evaluate()`
  - `QWEN_EXTRACT_JS` — multiple selector candidates:
    `.message-item`, `[class*="message"]`, `[data-role]`, `.user-message`, `.assistant-message`
  - `QWEN_SCROLL_JS` — scrolls `[class*="virtual"]` → `[class*="scroll"]` → `main` → `scrollingElement`
  - `QWEN_DEBUG_JS` — debug mode: dumps DOM structure on first run (diagnostic only)
- **Snap path** (`CdpSnapshotExtractor`): `DOMSnapshot.captureSnapshot` — renderer-level
  - 1 CDP session per extractor lifecycle
  - Incremental scroll + snapshot loop (convergence-based, not fixed steps)
  - Position-aware dedupe: `(role, content, dom_position)`
  - `capture_conversation()`: scroll → snapshot → merge → repeat until stable
- `extract_qwen_hybrid()` — точка входа: evaluate → snapshot fallback

#### Deduplication
- `seen` set tracks `content[:120]` — only for merging, NOT for stop decisions

### 4. CDP Connection Flow

#### Gemini
```
User clicks "🚀 Запустить Chrome"
  → launch_chrome_cdp()
    → _check_cdp_alive()? → "ALREADY_RUNNING" skip
    → subprocess.Popen(chrome --remote-debugging-port=9222 --user-data-dir=...)
    → return "OK"

User clicks "Проверить подключение"
  → checkGeminiCDP() → add_gemini_account('cdp')
    → _gw_queue.put("connect_gemini")
    → _do_connect_gemini()
      → _check_cdp_alive() ← HTTP GET /json/version
      → connect_over_cdp("http://127.0.0.1:9222")
      → ctx.new_page()
      → goto("https://gemini.google.com/")
      → set gemini_pw, gemini_page

On export:
  → _ensure_gemini_page() — ping; recreate if dead
  → _goto_gemini(url) — retry once on "Target closed"
  → extract → writer.write()
```

#### Qwen (shared CDP with Gemini)
```
User clicks "+ аккаунт" on Qwen card
  → addQwenAccount()
    → _gw_queue.put("connect_qwen")
    → _do_connect_qwen()
      → ctx.new_page()
      → goto("https://chat.qwen.ai/")

Sync All (sidebar-only FSM):
  → goto("https://chat.qwen.ai/") (under _cdp_lock)
  → total = querySelectorAll('div.chat-item-drag').length
  → for i in range(total):
      → click(a.chat-item-drag-link[i]) — re-query DOM per iteration
      → wait_for_function(messages > 0 && /c/ in URL)
      → extract_qwen_hybrid():
          try:
            → extract_qwen_dom() — evaluate-based scroll-loop
          except:
            → CdpSnapshotExtractor.capture_conversation()
              → scroll → DOMSnapshot → extract → merge (stable loop)
      → writer.write()

Sync Selected (by URL):
  → load sidebar (как выше)
  → for i in range(total):
      → click(i)
      → if chat_id in page.url:
          → extract → write → return
```

### 5. Writer (`exporters/writer.py`)

- `ExportWriter(out_dir)` — creates `out_dir` on init with `parents=True`
- `write(data)` — writes to `{out_dir}/{service}/{title}_{service}_{date}_{hash10}.md`
- `_to_markdown(data)` — formats as:
  ```
  #### 👤 Вы
  {user message}

  #### 🤖 AI
  {assistant message}
  ```

---

## Data Flow

### Gemini (full pipeline)

```
UI paste URL → syncGeminiSelected()
  → sync_gemini([url])
    → raise if gemini_pw is None
    → _gw_queue.put("export_gemini_batch", urls)

_gw_worker:
  → _do_export_gemini_batch(urls)
    → ExportWriter(out_dir="raw/gemini")
    → for each url:
        → _export_gemini(url)
          → _goto_gemini(url)
          → extract_gemini_rpc()  ← fast path
          → if not data → extract_gemini_dom()  ← scroll-loop
          → if data → ExportWriter.write()
    → log "X msgs from Y/Z chats"
```

### Qwen (full pipeline)

```
UI paste URL → syncQwenSelected()
  → sync_qwen([url])
    → raise if not _qwen_connected
    → _gw_queue.put("export_qwen_batch", urls)

_gw_worker:
  → _do_export_qwen_batch(urls)
    → ExportWriter(out_dir="raw/qwen")

    # Sync All (empty urls) — sidebar-only FSM
    if not urls:
      → _load_qwen_sidebar()
        → goto("https://chat.qwen.ai/")
        → total = querySelectorAll('div.chat-item-drag').length
      → for i in range(total):
          → _click_qwen_chat(i)
          → _wait_qwen_messages()
          → extract_qwen_hybrid()  ← scroll-loop
          → ExportWriter.write()
      → log "X msgs from Y/Z total chats"

    # Sync Selected (user-provided urls)
    else:
      → for each url:
          → _export_qwen(url)
            → _load_qwen_sidebar()
            → iterate over items[i]:
                click(i)
                if chat_id in url → extract → write → break
```

---

## Error Handling Contract

| Layer | Success | Error |
|-------|---------|-------|
| Backend Python | `return value` or `dict` | `raise RuntimeError(...)` |
| API bridge | return via pywebview | exception → Promise rejection |
| UI | `.then(resp)` | `.catch(err)` |

**Never** `return "ERR: string"` — always `raise`.

---

## CDP Health Check

```python
def _check_cdp_alive():
    """HTTP GET /json/version → verify webSocketDebuggerUrl exists"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1) as r:
            return "webSocketDebuggerUrl" in json.load(r)
    except Exception:
        return False
```

Used in:
- `launch_chrome_cdp()` — skip launch if already alive
- `_do_connect_gemini()` — fail-fast if CDP not running
- `_do_connect_qwen()` — fail-fast if CDP not running

---

## Qwen Navigation & Extraction Stability (v0.5.0-dev)

Qwen uses a React SPA without deep-link hydration or API-based chat list. Two independent layers: **navigation** (sidebar-only DOM FSM) and **extraction** (hybrid evaluate + snapshot). The hybrid extraction model eliminates dependency on JS runtime:

```
extract_qwen_hybrid():
  try:
    → page.evaluate() — fast path (JS runtime)
  except:
    → CdpSnapshotExtractor.capture_conversation()
      → 1 CDP session per extractor
      → scroll → DOMSnapshot.captureSnapshot → extract
      → repeat until convergence (stable iterations)
      → dedupe by (role, content, dom_position)

Key properties:
  • renderer-level extraction — no JS execution context dependency
  • survives SPA reload, navigation, context destruction
  • virtualized chat support — incremental scroll snapshots
  • no healthchecks, no reconnect loops, no recovery logic
```

---

## Chrome Profile

- **Location**: `~/.ai_pipeline/chrome_gemini`
- **Persistent**: login is preserved between sessions (Gemini + Qwen)
- **Created on first launch**: `os.makedirs(chrome_dir, exist_ok=True)`

---

## File Structure

```
├── main.py                      # Entry point (pywebview UI + backend workers)
├── core/
│   └── logger.py                # LogBuffer (thread-safe, max_size=5000)
├── exporters/
│   ├── __init__.py
│   ├── base.py                  # Browser abstraction (webview/playwright modes)
│   ├── cdp_snapshot.py          # CdpSnapshotExtractor — DOMSnapshot.captureSnapshot
│   ├── deepseek.py              # DeepSeekExporter (legacy scroll engine)
│   ├── gemini_extract.py        # Gemini RPC + DOM extraction, scroll-loop
│   ├── qwen_extract.py          # Qwen hybrid extraction (evaluate + snapshot)
│   ├── playwright_browser.py    # BrowserPlaywright wrapper
│   └── writer.py                # ExportWriter (Markdown output)
├── ui/
│   ├── app.html                 # UI layout + splash screen
│   ├── app.js                   # UI logic + pywebview bridge
│   ├── app.css                  # Styles + splash + CDP modal
│   ├── icon.png                 # Window icon / favicon
│   └── splash.png               # Splash screen image
├── raw/                         # Output directory (gitignored)
│   ├── deepseek/
│   ├── gemini/
│   └── qwen/
├── CHANGELOG.md                 # Version history
├── requirements.txt             # pywebview>=4.0, playwright>=1.40
├── .gitignore
├── README.md
└── ARCHITECTURE.md              # This file
```

---

## Configuration Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_LOGIN_SOFT` | 600 | DeepSeek login soft timeout (s) |
| `MAX_LOGIN_HARD` | 1800 | DeepSeek login hard timeout (s) |
| `MAX_STABLE` | 6 | DOM extraction: stop after N zero-growth iterations |
| `MAX_NO_NEW` | 10 | DOM extraction: stop after N same-fingerprint iterations |
| `MAX_ITER` | 50 | DOM extraction: max scroll iterations |
| Scroll delay | 1200ms | Wait after scroll for lazy-load |
| Event delay | 300ms | Wait after dispatchEvent |
| CDP timeout | 1s | HTTP probe timeout |
| CDP connection | 30s | add_gemini_account() / add_qwen_account() wait timeout |
| Qwen backoff | 3 failures | Reload page before reconnect |

---

## Dependencies

- Python 3.10+
- `pywebview>=4.0` — native desktop window with web UI
- `playwright>=1.40` — browser automation (only for DeepSeek)
