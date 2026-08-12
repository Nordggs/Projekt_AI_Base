# Changelog

## v0.5.4 — Help text fix (2026-08-12)

### Changed
- **Help/About**: убран устаревший абзац про DeepSeek («подключается отдельным окном»). DeepSeek, как и остальные сервисы, открывается во вкладке общего окна Chrome.

---

## v0.5.3 — CDP startup fix, updated Help (2026-08-12)

### Fixed
- **Chrome «мелькает и падает» при нажатии «Запустить Chrome»**: битый V8 `Code Cache` в профиле (`~/.ai_pipeline/chrome_gemini\Default\Code Cache`, повреждался при `taskkill /F`) вызывал мгновенный вылет Chromium. `CDPManager.start()` теперь:
  - делает до 2 попыток запуска;
  - при неудаче чистит повреждённые кэши (`Code Cache`, `GPUCache`, `DawnGraphiteCache`, `DawnWebGPUCache`, `GrShaderCache`, `ShaderCache`, `GraphiteDawnCache`) и повторяет;
  - перебирает кандидатов Chrome: bundled (`ms-playwright`) → системный (`Program Files`);
  - если CDP endpoint `127.0.0.1:9222` уже занят живым Chrome (single-instance), не запускает второй экземпляр, а переиспользует существующий.
- **Асинхронный запуск CDP**: `launch_chrome_cdp()` не блокирует UI — работает в фоновом потоке; ошибки идут в `logs/app.log` + UI-журнал.
- **Подробные логи CDP**: `[CDP] launching …`, `exited immediately`, `purged corrupt caches …`, `Chrome ready` — в UI-журнале и `logs/app.log`.

### Changed
- **Help/About обновлён**: обязательный первый шаг «Запустить Chrome», блок «Важно: если Chrome/CDP не запущен…», отдельная инструкция для DeepSeek.

---

## v0.5.2 — Update check, Help/About, Sync Summary, no-console GUI (2026-08-12)

### New
- **Уведомление о новой версии**: тихая фоновая проверка GitHub Releases (`releases/latest`, сравнение `tag_name`), зелёная точка `●` при наличии более новой **стабильной** версии (draft/prerelease игнорируются), окно с кнопкой «Открыть страницу релиза». Без автообновления. Источник истины — `APP_VERSION` в `main.py`.
- **Help/About**: кнопка «?» в верхней панели — краткая инструкция, папка экспорта, ссылки GitHub/Releases/License (открываются в браузере по умолчанию).
- **Финальный Summary операции**: после каждого провайдера (`✓ Qwen: завершено — 20 чатов, 150 сообщений, ошибок: 0`) и после «Синхронизировать всё» (`Готово: 5 провайдеров · 136 чатов · 6 676 сообщений · ошибок: 0` + partial/пропущено) — итоговая строка в логе. Кнопка «Синхронизировать всё» разблокируется только после полного завершения.

### Changed
- **GUI build без консоли**: репозиторный `AIChatExporter.spec` (`console=False`, onedir, `collect_all` playwright/pywebview, `excludes=PyQt5`). Сборка: `pyinstaller AIChatExporter.spec --distpath dist --workpath build`.
- **stdout/stderr → `logs/app.log`**: все traceback (включая pywebview `_call` и Playwright-колбэки) сохраняются рядом с ui_session-логами; `sys.excepthook` — туда же. Для пользователя ошибки остаются в UI-журнале.
- **`webview.FOLDER_DIALOG` → `webview.FileDialog.FOLDER`** — устранён deprecation warning.
- `_do_sync_all` теперь дожидается завершения всех провайдеров через `threading.Event` (итог и сброс кнопки — только после).

### Fixed
- **`greenlet.error: cannot switch to a different thread`** — все операции с Playwright (закрытие страниц, `window.stop()`, `page.close()`, `pw.close()`) вынесены из UI/pywebview-потока в владеющие worker-очереди (`soft_stop`/`close_cdp` команды). Аудит cross-thread вызовов (`reconnect_gemini`, `_close_cdp_browser`, `_soft_stop`, `_is_page_alive` в `add_gemina_account`).

---

## v0.5.1 — Output directory selection (2026-08-12)

### New
- **Выбор папки экспорта**: строка «Папка экспорта» в верхней панели с кнопками «Изменить…» (системный диалог выбора папки) и «Открыть» (проводник)
- **Запоминание выбора**: `config.json` рядом с exe (Portable) или в `%LOCALAPPDATA%\AIChatExporter\` (установленная версия, где exe-каталог не писабелен)
- **Папка по умолчанию**: `<exe>/raw` (Portable) / `<LOCALAPPDATA>\AIChatExporter\raw` (installed), создаётся автоматически
- **Полный путь в журнале**: при старте `[INFO] Output directory: …`, при экспорте `[OK] DeepSeek [1/20] → C:\…\raw\deepseek\….md`

### Changed
- Убран словарь `_output_folders` — единый корень вывода `App._output_dir`
- Логи записи файлов показывают полный путь вместо имени файла

---

## v0.5.0 — Stable release (2026-08-12)

### New
- **Пять провайдеров**: ChatGPT, Gemini, Claude, Qwen, DeepSeek — полный экспортный контур через CDP
- **ConversationModel** — единое ядро диалога (`conversation/`): IRBuilder, Enricher, Serializer, Validator
- **Enricher + attachment_capture** — захват CDN/blob-вложений и runtime-метаданных при экспорте
- **Релизная упаковка**: PyInstaller onedir + bundled Chromium (`ms-playwright/`)
- **Установщик** (Inno Setup) и **Portable ZIP** — работают без установленного Python
- **`PLAYWRIGHT_BROWSERS_PATH`** указывает на bundled браузер; CDPManager ищет Chrome: bundled → системный → RuntimeError
- **LICENSE** (MIT) + **THIRD_PARTY_NOTICES.md**

### Fixed
- Gemini: устранена гонка перезагрузки между экспортами (34/34)
- Qwen: устранены обе регрессии — ложный preflight и привязка чат ↔ файл (20/20)
- ChatGPT 28/28, DeepSeek 66/66, Claude 8/8 — контрольные прогоны
- **Portable/Setup**: `AIChatExporter.exe.config` с `<loadFromRemoteSources enabled="true"/>` — устранён краш `Failed to resolve Python.Runtime.Loader.Initialize` при запуске из ZIP, скачанного через браузер (Mark-of-the-Web)

### Changed
- `debug=True` → `False` в `webview.start()` (релизный запуск)
- README актуализирован под актуальную структуру (adapters/conversation/exporters)

---
## v0.4.5 — Qwen Sidebar FSM (2026-06-22)

### Changed
- **Qwen navigation rebuilt**: removed SPA router (FAST/SAFE/FALLBACK), replaced with click-based sidebar FSM
- `_export_qwen()` — uses index-based click iteration over sidebar items instead of `goto(/c/{id})` / `location.href`
- `_do_export_qwen_batch()` — Sync All: sidebar DOM scan (12/12 chats) instead of API discovery (3/12); Sync Selected: click-by-index URL match
- Diagnostics confirmed: `div.chat-item-drag` = stable source of truth, no API dependency

### Added
- `_click_qwen_chat(index)` — re-query DOM + click sidebar item `a.chat-item-drag-link`
- `_wait_qwen_messages()` — post-click invariant: `messages > 0 && /c/ in URL`
- `_load_qwen_sidebar()` — goto `chat.qwen.ai/` + wait for render

### Removed
- `_navigate_to_chat()` — 3-path SPA state machine (FAST/SAFE/FALLBACK)
- `_is_chat_view()` — DOM-based chat view detector
- `discover_qwen_sidebar_urls()` — dead code (API discovery via cdp_network)
- `discover_all_qwen_urls()` — dead code (old DOM href-based discovery)
- `window.stop()` call in Qwen export

---

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
