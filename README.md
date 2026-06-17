# AI Chat Exporter

Десктопное приложение для экспорта диалогов DeepSeek в Markdown.  
Без API-ключей, без встроенной AI-обработки, без HTTP-сервера.

## Быстрый старт

```bash
pip install -r requirements.txt
playwright install chromium
python main.py
```

## Использование

1. Вставьте URL чата DeepSeek (`https://chat.deepseek.com/a/chat/s/{uuid}`)
2. Нажмите **Export**
3. В открывшемся Playwright Chromium окне войдите в DeepSeek
4. После загрузки чата экстракция запустится автоматически
5. Файл сохраняется в `raw/`

## Формат выходного файла

```
#### 👤 Вы
{текст пользователя}

#### 🤖 AI
{текст ассистента}
```

Имя файла: `{title}_{source}_{date}_{hash10}.md`

## Структура проекта

```
├── main.py                      # Точка входа (pywebview UI)
├── exporters/
│   ├── base.py                  # Browser (mode=webview|playwright), Exporter
│   ├── playwright_browser.py    # Управление Playwright Chromium
│   ├── deepseek.py              # Scroll Engine v2 + EXTRACT_JS
│   └── writer.py                # ExportWriter (raw/*.md)
├── raw/                         # Выходные .md файлы
├── requirements.txt
└── pipeline-app.py              # Legacy (PyQt5 reference)
```

## Зависимости

- Python 3.10+
- pywebview>=4.0
- playwright>=1.40
