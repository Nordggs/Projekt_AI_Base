# Changelog

## v0.4.0 — Qwen CDP + Soft Recovery (2026-06-19)

### New
- **Qwen** — новый провайдер, экспорт через CDP (общий Chrome с Gemini)
  - `exporters/qwen_extract.py` — DOM extraction + scroll-loop + URL discovery
  - UI карточка Qwen (5 колонок в сетке)
  - Независимый cancel/stop
- **Страница-заставка (splash)** при запуске приложения
- **Window icon + favicon** — брендирование (`ui/icon.png:`, `ui/splash.png`)
- **CHANGELOG.md** — история версий

### Fixed
- **Soft page recovery** — `ensure_qwen_alive()` больше не убивает page на navigation/context_destroyed
  - Классификация ошибок: `target_closed` / `context_destroyed` / `navigation` / `unknown`
  - `_reattach_qwen_page()` — поиск живой вкладки Qwen в CDP контексте
  - Backoff: 3+ последовательных failures → reload, не reconnect
- **Диагностика** — `[QWEN][HEALTHCHECK]` с типом ошибки, URL страницы, CDP alive
  - `[QWEN][CDP TARGETS]` — список открытых вкладок перед export
- UI название изменено на "AI Chat Exporter"

### Infrastructure
- `_cdp_lock` — thread-safe доступ к CDP для Gemini и Qwen
- `_qwen_page_state` — трёхуровневый статус: `ok | unstable | dead`
- `_qwen_fail_count` / `_qwen_last_fail_time` — backoff guard

---

## v0.3.2 — State Machine + Per-Provider Cancel (2026-06-18)

### New
- State-machine orchestration для Gemini (idle → connecting → connected)
- Per-provider cancel flags + session epoch guards
- Cancel propagation в `_export_one()`, `_export_gemini()`, `extract_gemini_dom()`
- UI stop buttons для DeepSeek и Gemini
- `save_log()` — дамп лога при cancel в `logs/debug_*.log`

### Changed
- `check_cdp_status()` — pure-read, без side-эффектов
- Single-shot connect через `start_gemini_connect()` + `_gw_worker` thread

---

## v0.3.1 — Gemini CDP Extraction (2026-06-17)

### New
- Gemini extraction через CDP (`connect_over_cdp`)
- Два уровня: RPC (`/_/Batchexecute`) + DOM scroll-loop fallback
- `GEMINI_EXTRACT_JS` — Angular-селекторы `user-query`/`model-response`
- `SCROLL_BOTTOM_JS` — скролл с dispatchEvent для триггера lazy-load
- `_gw_worker` — изолированный thread для Gemini
- CDP Health Check (`_check_cdp_alive`)
- Chrome auto-launch с `--remote-debugging-port=9222`
- `discover_gemini_sidebar_urls()` — поиск URL чатов в боковой панели
- `_ensure_gemini_page()` / `_goto_gemini()` — auto-retry на "Target closed"

### Fixed
- Дедупликация по `content[:120]` для stop-условий
- Сессия сохраняется через `--user-data-dir`

---

## v0.2.2 — DeepSeek Scroll Engine (2026-06-17)

### New
- Бейдж версии в левом нижнем углу
- DeepSeek: scroll engine v2 — `QWEN_SCROLL_JS` / `QWEN_EXTRACT_JS`
- Многослойная тёмная тема
- Per-service папки (`raw/deepseek/`, `raw/gemini/`)
- `ExportWriter(out_dir)` — гибкая маршрутизация выхода

### Changed
- Hybrid URL discovery: sidebar + inline

---

## v0.2.1 — MVP (2026-06-16)

### New
- Первая версия: DeepSeek экспорт через Playwright
- Базовая тёмная тема
- CDP modal для Gemini
- pywebview UI с карточками провайдеров
