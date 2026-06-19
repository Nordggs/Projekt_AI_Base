# Architecture — AI Chat Saver v0.3.1

## Overview

Desktop application for exporting AI chat conversations (Gemini, DeepSeek) to local Markdown files. No API keys, no cloud dependencies — works through browser automation (Playwright) and Chrome DevTools Protocol (CDP) for Gemini.

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
┌──────────────┐                        ┌──────────────────────┐
│  _pw_worker  │                        │    _gw_worker        │
│  (thread)    │                        │    (thread)          │
│              │                        │                      │
│  DeepSeek    │                        │  Gemini (CDP only)   │
│  Playwright  │                        │  sync_playwright()   │
│  Browser     │                        │  connect_over_cdp()  │
└──────┬───────┘                        └──────┬───────────────┘
       │                                       │
       ▼                                       ▼
┌──────────────┐                        ┌──────────────────────┐
│ DeepSeek     │                        │ Gemini Extraction    │
│ Exporter     │                        │ RPC + DOM fallback   │
│ Scroll Engine│                        │ Scroll-loop ingestor │
│ (legacy)     │                        │ (v0.3.1)             │
└──────┬───────┘                        └──────┬───────────────┘
       │                                       │
       └──────────────┬────────────────────────┘
                      ▼
             ┌────────────────┐
             │  ExportWriter  │
             │  raw/*.md      │
             └────────────────┘
```

---

## Components

### 1. Frontend (`ui/`)

- **app.html** — layout: provider cards (Gemini, DeepSeek, ChatGPT, Claude), CDP modal, log panel
- **app.js** — bridges Python backend via `window.pywebview.api.*`:
  - `addAccount(provider)` → opens modal for auth URL
  - `syncGeminiAll/SyncSelected` → sends URLs to `sync_gemini()`
  - `launchChromeCDP()` → calls `launch_chrome()`, polls CDP availability
  - `checkGeminiCDP()` → calls `add_gemini_account('cdp')`, shows status
- **app.css** — dark UI theme, modal styles, CDP status indicators

### 2. Backend (`main.py`)

#### Two isolated workers (threads)

**`_pw_worker`** — DeepSeek export:
- Owns `self.pw` (Playwright browser)
- Commands: `connect`, `reconnect`, `export_batch`
- Uses `DeepSeekExporter` (scroll engine v2)

**`_gw_worker`** — Gemini export:
- Owns `self.gemini_pw`, `self.gemini_page` (CDP-connected)
- Commands: `connect_gemini`, `export_gemini_batch`
- Uses inline extraction (RPC + DOM fallback)

#### Key methods

| Method | Purpose |
|--------|---------|
| `_do_connect_gemini()` | CDP attach: HTTP probe → `connect_over_cdp()` → `ctx.new_page()` |
| `_ensure_gemini_page()` | Ping page; recreate via CDP context if closed |
| `_goto_gemini(url)` | Navigate with auto-retry on "Target closed" |
| `_export_gemini(url)` | Full export pipeline: goto → RPC → DOM → write |
| `_do_export_gemini_batch(urls)` | Loop over URLs, collect results, log totals |
| `launch_chrome_cdp()` | Launch Chrome with `--remote-debugging-port=9222`, persistent profile |
| `launch_chrome()` | API bridge |
| `consolidate_gemini_account()` | Guard: raises if CDP failed |
| `sync_gemini()` | Raises `RuntimeError` on failure (never returns string errors) |

### 3. Extraction (`exporters/gemini_extract.py`)

#### Two extraction strategies (tried in order)

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

#### Deduplication
- `seen` set tracks `content[:120]` — only for merging, NOT for stop decisions

### 4. CDP Connection Flow

```
User clicks "🚀 Запустить Chrome"
  → launch_chrome_cdp()
    → _check_cdp_alive()? → "ALREADY_RUNNING" skip
    → kill? NO — we never kill user's Chrome
    → subprocess.Popen(chrome --remote-debugging-port=9222 --user-data-dir=~/.ai_pipeline/chrome_gemini)
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
      → _connect_gemini_done.set()
    → guard: if not gemini_pw → raise

On export:
  → _ensure_gemini_page() — ping; recreate if dead
  → _goto_gemini(url) — retry once on "Target closed"
  → extract
  → writer.write()
  → return {"ok": true, "path": ..., "count": n}
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
    → return "STARTED"

_gw_worker:
  → _do_export_gemini_batch(urls)
    → log batch start
    → urls = urls or discover_gemini_sidebar_urls()   ← user input always wins
    → ExportWriter(out_dir="raw/gemini")
    → for each url:
        → _export_gemini(url)
          → _goto_gemini(url)
            → _ensure_gemini_page()  ← ping or recreate
            → goto()
            → if "closed" → recreate + retry once
          → extract_gemini_rpc()  ← fast path
          → if not data → extract_gemini_dom()  ← scroll-loop
          → if data → ExportWriter.write()
          → return {"ok": true, "path": ..., "count": n}
    → log "X msgs from Y/Z chats"

UI .then(resp):
  → resp.ok → log "exported: N msgs → path"
  → error → .catch() → log error
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

---

## Chrome Profile

- **Location**: `~/.ai_pipeline/chrome_gemini`
- **Persistent**: login is preserved between sessions
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
│   ├── gemini_extract.py        # GEMINI_EXTRACT_JS, SCROLL_BOTTOM_JS,
│   │                            # extract_gemini_rpc, extract_gemini_dom,
│   │                            # discover functions
│   ├── playwright_browser.py    # BrowserPlaywright wrapper
│   └── writer.py                # ExportWriter (Markdown output)
├── ui/
│   ├── app.html                 # UI layout
│   ├── app.js                   # UI logic + pywebview bridge
│   └── app.css                  # Styles + CDP modal
├── raw/                         # Output directory (gitignored)
│   ├── deepseek/
│   └── gemini/
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
| CDP connection | 30s | add_gemini_account() wait timeout |

---

## Dependencies

- Python 3.10+
- `pywebview>=4.0` — native desktop window with web UI
- `playwright>=1.40` — browser automation (only for DeepSeek)
