# AI Chat Saver

Десктопное приложение для экспорта диалогов AI (Gemini, DeepSeek) в локальные Markdown-файлы.  
Без API-ключей, без облачных сервисов, без HTTP-сервера.

## Быстрый старт

```bash
pip install -r requirements.txt
playwright install chromium
python main.py
```

## Использование

### DeepSeek
1. Нажмите **Add Account** → выберите DeepSeek
2. Вставьте URL (`https://chat.deepseek.com/a/chat/s/{uuid}`)
3. Нажмите **Sync** — откроется Playwright Chromium, войдите в DeepSeek
4. Файл сохраняется в `raw/deepseek/`

### Gemini
1. Нажмите **Add Account** → выберите Gemini → откроется CDP-модалка
2. Нажмите **🚀 Запустить Chrome** — Chrome запустится с портом 9222
3. В запущенном Chrome войдите в Gemini Google
4. Нажмите **Проверить подключение**
5. Вставьте URL чата Gemini, нажмите **Sync**
6. Файл сохраняется в `raw/gemini/`

## Формат выходного файла

```
#### 👤 Вы
{текст пользователя}

#### 🤖 AI
{текст ассистента}
```

Имя файла: `{title}_{service}_{date}_{hash10}.md`

## Экстракция Gemini (v0.3.1)

Два уровня:
1. **RPC** — `/_/Batchexecute` (быстрый, не всегда доступен)
2. **DOM scroll-loop** (fallback) — скролл до 50 итераций, Angular-селекторы `user-query`/`model-response`, дедупликация по `content[:120]`

Подключение через **CDP** (`connect_over_cdp`) — пользовательский Chrome с `--remote-debugging-port=9222`, профиль `~/.ai_pipeline/chrome_gemini` сохраняет логин.

## Структура проекта

```
├── main.py                      # Точка входа (pywebview UI + 2 workers)
├── exporters/
│   ├── base.py                  # Browser (mode=webview|playwright)
│   ├── playwright_browser.py    # Управление Playwright Chromium
│   ├── deepseek.py              # DeepSeekExporter (scroll engine v2)
│   ├── gemini_extract.py        # GEMINI_EXTRACT_JS, RPC + DOM fallback
│   └── writer.py                # ExportWriter (raw/*.md)
├── core/
│   └── logger.py                # LogBuffer (thread-safe)
├── ui/
│   ├── app.html                 # UI layout
│   ├── app.js                   # UI logic + pywebview bridge
│   └── app.css                  # Styles
├── raw/                         # Выходные .md файлы
├── requirements.txt
└── ARCHITECTURE.md              # Полная документация архитектуры
```

## Зависимости

- Python 3.10+
- `pywebview>=4.0`
- `playwright>=1.40`
