[English](README.md) | **Русский**

# AI Chat Exporter

> *Личный open-source проект. Создан в первую очередь для себя и опубликован на случай, если окажется полезен другим.*

![AI Chat Exporter](ui/splash.png)

Десктопное приложение для экспорта диалогов AI (**ChatGPT, Gemini, Claude, Qwen, DeepSeek**) в локальные Markdown-файлы.
Без API-ключей, без облачных сервисов, без HTTP-сервера.

## Возможности

- Экспорт диалогов **ChatGPT, Gemini, Claude, Qwen, DeepSeek**
- Файлы сохраняются в `raw/<service>/*.md` — кириллица, вложения, метаданные
- Без API-ключей — работа через CDP с bundled Chromium (системный Google Chrome не требуется)
- Экспорт всех диалогов (**Sync All**) или выбранных чатов; независимая остановка каждого провайдера
- Автономные сборки — Python и Playwright внутри приложения, ничего доустанавливать не нужно

## Скачать

- **[AIChatExporter-Setup-0.5.0.exe](https://github.com/Nordggs/Project_AI_Base/releases)** — установщик с ярлыками Desktop и Start Menu (рекомендуется)
- **[AIChatExporter-Portable-0.5.0.zip](https://github.com/Nordggs/Project_AI_Base/releases)** — портативная сборка, работает без установки

## Установка

### Вариант 1 — Установщик (рекомендуется)

1. Запустите `AIChatExporter-Setup-0.5.0.exe`
2. Следуйте инструкциям — создаются ярлыки Desktop и Start Menu
3. Запустите **AI Chat Exporter**

### Вариант 2 — Portable

Распакуйте `AIChatExporter-Portable-0.5.0.zip` в любую папку и запустите `AIChatExporter.exe`. Работает без установки.

### Из исходников

```bash
pip install -r requirements.txt
playwright install chromium
python main.py
```

При запуске из исходников используется системный **Google Chrome** через CDP; в релизных сборках поставляется собственный bundled Chromium.

## Использование

### Общий механизм

Приложение запускает браузер (bundled Chromium или системный Chrome) с `--remote-debugging-port=9222`.
Вход в аккаунты провайдеров выполняется в открывшемся окне браузера — сессия сохраняется в профиле
(`~/.ai_pipeline/chrome_gemini`), повторный вход не требуется.

### ChatGPT / Gemini / Claude / Qwen

1. Запустите Chrome через карточку провайдера (**🚀 Запустить Chrome**)
2. Войдите в аккаунт в открывшемся окне браузера
3. Нажмите **Проверить подключение**
4. **Синхронизировать** — автоматическое сканирование сайдбара и экспорт всех диалогов

### DeepSeek

1. **Add Account** → вставьте URL чата (`https://chat.deepseek.com/a/chat/s/{uuid}`)
2. Войдите в аккаунт в открывшемся окне
3. **Sync** — экспорт диалога в `raw/deepseek/`

## Формат выходного файла

```markdown
<!-- hash: e7f89a title: название чата chat_order: 0 -->

#### 👤 Вы (2024-01-15 14:30)
текст пользователя

#### 🤖 AI
текст ассистента
```

Имя файла: `{service}_{stable_id[:8]}_{hash6}.md`

## Структура проекта

```
├── main.py                      # Точка входа (pywebview UI + 2 воркера)
├── adapters/
│   ├── base.py                  # BaseAdapter ABC
│   ├── cdp_manager.py           # CDPManager (жизненный цикл браузера) + CDPContext
│   ├── chatgpt.py               # ChatGPTAdapter
│   ├── claude.py                # ClaudeAdapter
│   ├── qwen.py                  # QwenAdapter
│   ├── gemini.py                # GeminiAdapter
│   └── deepseek.py              # DeepSeekAdapter
├── conversation/
│   ├── models.py                # ConversationModel (ядро)
│   ├── irbuilder.py             # IRBuilder: adapter → ConversationModel
│   ├── enrichment.py            # Enricher: CDP-активы + runtime + blobs
│   ├── serializer.py            # сериализация
│   └── validator.py             # валидация
├── exporters/
│   ├── writer.py                # ExportWriter (atomic write, dedup, timestamps)
│   ├── gemini_extract.py        # DOM-экстракция Gemini
│   ├── chatgpt_extract.py       # DOM-экстракция ChatGPT
│   ├── claude_extract.py        # DOM-экстракция Claude
│   ├── qwen_extract.py          # DOM-экстракция Qwen
│   ├── deepseek.py              # DeepSeekExporter
│   └── attachment_capture.py    # CDP-захват вложений
├── core/
│   └── logger.py                # LogBuffer (thread-safe)
├── ui/
│   ├── app.html                 # UI layout
│   ├── app.js                   # UI logic + pywebview bridge
│   ├── app.css                  # Styles + splash
│   └── icon.ico                 # Window icon
└── raw/                         # Выходные .md файлы
```

## Зависимости

- Python 3.10+ (запуск из исходников)
- `pywebview>=4.0`
- `playwright>=1.40`
- Google Chrome (только для dev-запуска; в релизных сборках — bundled Chromium)

## Известные ограничения

- CDN/blob-вложения, которые не удаётся получить, помечаются как `partial`; сам чат при этом экспортируется

## Лицензия

Проект распространяется под лицензией MIT — см. [LICENSE](LICENSE).
Сторонние компоненты (Chromium, Playwright, PyWebView и др.) — на их собственных лицензиях, см. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
