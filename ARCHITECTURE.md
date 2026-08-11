# Architecture — AI Chat Exporter v0.5.0

## Overview

Desktop application that exports AI chat dialogs from five providers (**ChatGPT, Gemini, Claude, Qwen, DeepSeek**) into local Markdown files. No API keys and no cloud services — the app drives a real browser through Chrome DevTools Protocol (CDP) and Playwright, reads the rendered page, and normalizes the content into a unified conversation model.

```
UI (pywebview) ── window.pywebview.api.* ──> main.py App
                                              │
                    ┌─────────────────────────┴──────────────────────────┐
                    ▼                                                     ▼
             _pw_worker (thread)                                  _gw_worker (thread)
             DeepSeekAdapter                                        Gemini / Qwen / ChatGPT / Claude
             own Playwright + CDPContext                            shared CDP browser (own Playwright)
                    │                                                     │
                    ▼                                                     ▼
             DeepSeekExporter                                 adapters (list_chats → open_chat → extract_chat)
                    │                                                     │
                    └──────────────┬──────────────────────────────────────┘
                                   ▼
                          conversation/ pipeline
                    IRBuilder → ConversationModel → Enricher → Validator
                                   │
                                   ▼
                           ExportWriter → raw/<service>/*.md
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `ui/` | Desktop UI (HTML/JS/CSS) rendered by pywebview |
| `main.py` | Application orchestration: workers, queues, sync state, cancel |
| `adapters/` | Browser lifecycle (`cdp_manager.py`) + per-provider adapters |
| `conversation/` | Unified data model and enrichment pipeline |
| `exporters/` | Page extraction scripts + markdown writer + attachment capture |
| `core/` | Thread-safe logging (`LogBuffer`) |

## Runtime model

- **CDPManager** (`adapters/cdp_manager.py`) owns the Chrome process only — no Playwright, no pages. It resolves Chrome in order **bundled → system → `RuntimeError`**, launches it with `--remote-debugging-port=9222` and a persistent profile (`~/.ai_pipeline/chrome_gemini`), so logins survive restarts.
- **Two worker threads**: `_pw_worker` runs DeepSeek with its own Playwright instance and a `CDPContext` wrapper (single window); `_gw_worker` runs Gemini, Qwen, ChatGPT and Claude against the shared CDP browser.
- Each provider has its own lock (`_locks[provider]`, non-blocking) — exporting one provider never blocks another.
- Cancellation is a single global flag (`_cancel_flag` + `_cancel_version` token) applied in three layers: cooperative `_check_cancel()` in loops → `_soft_stop()` (`window.stop()` on all pages) → versioned `pw_call()` wrapper for Playwright I/O.

## Provider integration

| Provider | Adapter | Navigation & extraction |
|----------|---------|--------------------------|
| ChatGPT | `ChatGPTAdapter` | Adapter path: `list_chats()` → `open_chat()` → `extract_chat()` (sidebar scan + click-through). Extraction pipeline: API → `NEXT_DATA` → DOM |
| Gemini | `GeminiAdapter` | Sidebar link click (no deep-link hydration in the SPA). Extraction: RPC (`/_/Batchexecute`) first, DOM scroll-loop as fallback |
| Claude | `ClaudeAdapter` | Adapter path: sidebar scan + click-through. DOM extraction with a fallback pass when assistant messages lack marker attributes |
| Qwen | `QwenAdapter` | Sidebar DOM FSM (click → wait for messages in URL). Extraction: DOM scroll-loop with CDP `DOMSnapshot` fallback |
| DeepSeek | `DeepSeekAdapter` | Own worker thread; `DeepSeekExporter` scroll engine over the conversation page |

ChatGPT and Claude connect through the shared CDP context (`_cdp_browser().contexts[0]`); Gemini and Qwen share the same browser context. A `_cdp_lock` protects browser-level operations (`new_page` + `goto`) only — not the whole export.

## Conversation pipeline (`conversation/`)

- **IRBuilder** (`irbuilder.py`) — converts raw provider output into a unified `ConversationModel`.
- **ConversationModel** (`models.py`) — the core representation: messages, attachment nodes, validation results.
- **Enricher** (`enrichment.py`) — attaches runtime data and CDP-captured assets (`CapturedAsset`) and blobs to messages. Attachments that cannot be fetched (CDN/blob) are marked as `partial`; the chat is still exported.
- **Serializer** (`serializer.py`) / **Validator** (`validator.py`) — tree conversion (`tree_to_dict` / `dict_to_tree`) and consistency checks.

## Export path (`exporters/writer.py`)

- **ExportWriter** writes to `raw/<service>/{service}_{stable_id[:8]}_{hash6}.md`.
- `stable_id` chain: `data["chat_id"]` → URL last segment → `sha1(title|source)[:12]`.
- **Atomic write**: `.md.tmp` → `.replace()` (NTFS/ext4). Existing files are never overwritten.
- **Dedup**: a hash comment inside each file (`<!-- hash: … title: … chat_order: … -->`).

```markdown
<!-- hash: e7f89a title: chat title chat_order: 0 -->

#### 👤 Вы (2024-01-15 14:30)
user message

#### 🤖 AI
assistant message
```

## UI bridge (pywebview)

JavaScript calls `window.pywebview.api.*`; the backend always raises `RuntimeError` on failure (never returns an error string).

| Method | Purpose |
|--------|---------|
| `launch_chrome()` / `close_chrome()` | Start/stop the CDP Chrome instance |
| `connect_gemini()` / `connect_qwen()` / `connect_chatgpt()` / `connect_claude()` | Open the provider page in the shared CDP browser |
| `add_account(url)` | DeepSeek login / chat URL registration |
| `sync_gemini("[]")` / `sync_qwen("[]")` / … | Enqueue a batch export for one provider |
| `sync_all()` | One thread per connected provider, joined after completion |
| `cancel_all()` | Set the cancel flag + bump the cancel version |
| `get_providers_status()` | `{gemini: bool, qwen: bool, …}` |

Sync state per provider (`idle` → `running` → `done` / `failed`) is pushed to the UI as `setProviderSync(provider, status)`.

## Error handling

| Layer | Success | Error |
|-------|---------|-------|
| Backend | `return value` / `dict` | `raise RuntimeError(...)` |
| API bridge | value via pywebview | exception → Promise rejection |
| UI | `.then(resp)` | `.catch(err)` |

## Bundled runtime

Release builds are PyInstaller `onedir` packages that ship their own Chromium:
- `PLAYWRIGHT_BROWSERS_PATH` is pointed at `<exe_dir>/ms-playwright` before the first `sync_playwright().start()`.
- Chrome resolution in `CDPManager`: bundled → system → `RuntimeError`.
- No system Python or Chrome is required at runtime.

## File structure

```
├── main.py                      # Entry point (pywebview UI + 2 workers)
├── adapters/
│   ├── base.py                  # BaseAdapter ABC
│   ├── cdp_manager.py           # CDPManager (browser lifecycle) + CDPContext
│   ├── chatgpt.py               # ChatGPTAdapter
│   ├── claude.py                # ClaudeAdapter
│   ├── qwen.py                  # QwenAdapter
│   ├── gemini.py                # GeminiAdapter
│   └── deepseek.py              # DeepSeekAdapter
├── conversation/
│   ├── models.py                # ConversationModel (core)
│   ├── irbuilder.py             # IRBuilder: adapter → ConversationModel
│   ├── enrichment.py            # Enricher: CDP assets + runtime + blobs
│   ├── serializer.py            # serialization
│   └── validator.py             # validation
├── exporters/
│   ├── writer.py                # ExportWriter (atomic write, dedup, timestamps)
│   ├── gemini_extract.py        # Gemini DOM extraction
│   ├── chatgpt_extract.py       # ChatGPT DOM extraction
│   ├── claude_extract.py        # Claude DOM extraction
│   ├── qwen_extract.py          # Qwen DOM extraction
│   ├── deepseek.py              # DeepSeekExporter
│   └── attachment_capture.py    # CDP attachment capture
├── core/
│   └── logger.py                # LogBuffer (thread-safe)
├── ui/
│   ├── app.html                 # UI layout
│   ├── app.js                   # UI logic + pywebview bridge
│   ├── app.css                  # Styles + splash
│   └── icon.ico                 # Window icon
└── raw/                         # Output .md files (gitignored)
```

## Dependencies

- Python 3.10+
- `pywebview>=4.0` — native desktop window with a web UI
- `playwright>=1.40` — browser automation and CDP connection
