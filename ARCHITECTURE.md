# Architecture — AI Chat Exporter v0.4.0

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
| `_reattach_qwen_page()` | Поиск живой вкладки Qwen в CDP по `chat.qwen.ai` в URL |
| `ensure_qwen_alive()` | Healthcheck с классификацией ошибок и soft recovery |
| `classify_qwen_failure(e)` | Различает `target_closed` / `context_destroyed` / `navigation` |
| `_export_qwen(url)` | Qwen export pipeline: goto → DOM scroll-loop → write |
| `_do_export_qwen_batch(urls)` | Loop с CDP targets logging и soft recovery |
| `add_qwen_account()` | API: постановка в очередь connect_qwen |
| `sync_qwen(urls)` | API: проверка alive → enqueue |
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

#### Qwen (`exporters/qwen_extract.py`)

- DOM scroll-loop ingestor (up to 50 iterations, ~75s total)
- `QWEN_EXTRACT_JS` — multiple selector candidates:
  `.message-item`, `[class*="message"]`, `[data-role]`, `.user-message`, `.assistant-message`
- `QWEN_SCROLL_JS` — scrolls `[class*="virtual"]` → `[class*="scroll"]` → `main` → `scrollingElement`
- `QWEN_DEBUG_JS` — debug mode: dumps DOM structure on first run
- `discover_qwen_sidebar_urls()` / `discover_all_qwen_urls()` — sidebar URL discovery
- Stop conditions: stable 6 rounds | no-progress 10 rounds | max 50 iterations

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
      → _reattach_qwen_page()? ← ищет живую вкладку
      → ctx.new_page() — если reattach не удался
      → goto("https://chat.qwen.ai/")

On export:
  → ensure_qwen_alive() — healthcheck с классификацией
    ├─ ok → continue
    ├─ target_closed → page = None, return
    ├─ navigation / context_destroyed → fail_count++, retry
    └─ 3+ failures → reload page
  → goto(chat_url) (under _cdp_lock)
  → extract_qwen_dom() — scroll-loop
  → writer.write()
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
    → ensure_qwen_alive()  ← healthcheck + soft recovery
    → _gw_queue.put("export_qwen_batch", urls)

_gw_worker:
  → _do_export_qwen_batch(urls)
    → log CDP targets
    → ExportWriter(out_dir="raw/qwen")
    → for each url:
        → _export_qwen(url)
          → ensure_qwen_alive()  ← healthcheck + backoff
          → goto(url) (under _cdp_lock)
          → extract_qwen_dom()  ← scroll-loop
          → ExportWriter.write()
    → log "X msgs from Y/Z chats"
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

## Qwen Soft Recovery (v0.4.0)

Qwen CDP target is inherently unstable (SPA redirects, context regeneration, anti-automation reset). The soft recovery model handles this:

```
evaluate fail → classify_qwen_failure()
  ├─ "target_closed"      → page = None, reconnect required
  ├─ "context_destroyed"  → fail_count++, page KEPT, retry
  ├─ "navigation"         → fail_count++, page KEPT, retry
  └─ "unknown"            → fail_count++, page KEPT, retry
         │
         └─ fail_count >= 3 → page.reload(), backoff

state: _qwen_page_state ∈ { "ok" | "unstable" | "dead" }
```

Additional recovery:
- `_reattach_qwen_page()` — scans `ctx.pages` for existing Qwen tab before creating a new one
- `_cdp_lock` — prevents race conditions between Gemini/Qwen on shared CDP context

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
│   ├── deepseek.py              # DeepSeekExporter (legacy scroll engine)
│   ├── gemini_extract.py        # Gemini RPC + DOM extraction, scroll-loop
│   ├── qwen_extract.py          # Qwen DOM extraction, scroll-loop, URL discovery
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
