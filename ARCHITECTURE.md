# Архитектура AI Chat Exporter

## Слои

```
┌─────────────────────────────────────────────┐
│  UI Layer  (pywebview)                      │
│  Форма: URL input + Export button           │
│  Связь: js_api → API class → App controller │
├─────────────────────────────────────────────┤
│  API Layer  (main.py: API)                  │
│  Тонкий прослойка: run_export(url)          │
│  Чистый объект, без Playwright ссылок       │
├─────────────────────────────────────────────┤
│  Controller  (main.py: App)                 │
│  BrowserPlaywright → Browser → Exporter     │
│  Writer.save → callback → UI.update         │
├─────────────────────────────────────────────┤
│  Execution  (Playwright Chromium)           │
│  Отдельный процесс, изолирован от UI        │
│  goto → wait → scroll loop → extract        │
├─────────────────────────────────────────────┤
│  DOM Contract  (EXTRACT_JS)                 │
│  immutable v1, pipeline-app.py:55           │
│  Выдача: {role,content,navId}[]             │
├─────────────────────────────────────────────┤
│  Persistence  (ExportWriter)                │
│  raw/{title}_{source}_{date}_{hash10}.md    │
│  Формат: #### 👤 Вы / #### 🤖 AI            │
└─────────────────────────────────────────────┘
```

## Почему не один бэкенд?

### WebView2 — стабилен для лёгких SPA (Google), падает на DeepSeek

Тест `test_render.py` подтвердил: WebView2 JS engine и renderer исправны.  
DeepSeek (React + heavy virtualization + streaming UI) вешает WebView2 renderer при hydration.

**Вывод**: DeepSeek требует отдельного Chromium процесса.

### Playwright, не CEF, не PyQt5

- CEF — тот же Chromium, что и WebView2, с теми же GPU crash
- PyQt5 QWebEngine — legacy, два GUI фреймворка в одном процессе
- Playwright — отдельный процесс, изоляция crash-domain, синхронный API

## Ключевые решения

### API-класс (js_api)

pywebview 6.2.1 WinForms introspection падает на "жирных" объектах  
с Playwright/Browser/Exporter references.

**Решение**: отдельный класс `API(app)` с единственным публичным методом `run_export(url)`.  
В `js_api` передаётся `self.api`, не `self`.

### Browser-абстракция

```python
class Browser:
    mode = "webview" | "playwright"
    capabilities = {"scroll": bool, "eval": True, "multi_tab": bool}
```

Экспортёр не знает, какой бэкенд — `browser.execute(js)` работает в обоих режимах.

### Scroll Engine v2

Stateful crawler для virtualized React list (`.ds-virtual-list`):

- **Stabilization barrier**: 300ms wait после scroll — React commit window
- **Dedup**: `data-nav-id` приоритет, `sha1(role|content)` fallback
- **Stagnation guard**: scrollTop не меняется 3 раза → hard scroll
- **Catchup**: MAX_NO_GROWTH=5 → scroll to bottom → retry
- **Bounded loop**: MAX_STEPS=200 — аварийный стоп

### EXTRACT_JS

Контракт извлечения данных. Единая точка истины для всех  
рендер-бэкендов (PyQt5, Playwright, будущие).  
Версионируется через commit hash, не через schema_version.

## Data contract

```json
{
    "schema_version": 1,
    "source": "deepseek",
    "chat_id": "uuid",
    "title": "Название чата",
    "source_url": "https://chat.deepseek.com/a/chat/s/{uuid}",
    "messages": [
        {"role": "user|assistant", "content": "...", "navId": "..."}
    ]
}
```

## Почему нет встроенной AI-обработки

Ollama/tags/summary вынесены за пределы приложения.  
Приложение только экспортирует чистый Markdown.  

**Причина**: AI-постобработка — отдельная задача, не блокирующая  
экспорт. Разделение позволяет:
- менять AI-пайплайн без правки экстрактора
- экспортировать без Ollama (если не запущен)
- использовать любой AI (не только локальный)
