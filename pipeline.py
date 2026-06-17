#!/usr/bin/env python3
"""
AI-Chats Pipeline — следит за inbox, обрабатывает .md файлы:
  - извлекает SOURCE_URL, вычисляет хеш
  - определяет сервис (deepseek, gemini, glm5, qwen)
  - проверяет дубли по chat_id
  - отправляет текст в Ollama для тегов и саммари
  - добавляет YAML-шапку
  - сохраняет в Obsidian vault
"""

import os
import re
import time
import json
import hashlib
import argparse
import logging
import threading
import yaml
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import httpx
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# ─── Константы ────────────────────────────────────────────────────────────────
CONFIG_VERSION = 2
PROMPT_CONTENT_LIMIT = 6000
TEST_CONTENT_LIMIT = 10000

DEFAULT_PROMPT = """Ты анализируешь переписку пользователя с AI-ассистентом.

Выполни два действия:
1. Напиши краткое саммари (1-2 предложения) — о чём чат, какие вопросы задавал пользователь, что обсуждали. Будь конкретным.
2. Предложи 3-5 тегов (ключевых слов), отражающих тематику и контекст разговора.

Ответ формулируй на русском языке. Названия технологий, библиотек, языков программирования и продуктов оставляй в оригинальном написании (английском).

Формат ответа строго:

SUMMARY:
<саммари>

TAGS:
тег1, тег2, тег3

Переписка:
---
{content}
---"""


# ─── Settings — единый источник истины ────────────────────────────────────────
class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()
        changed = False
        changed |= self._migrate()
        changed |= self._ensure_defaults()
        if changed:
            self._save()

    def _load(self) -> dict:
        with open(self.path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _migrate(self) -> bool:
        v = self.data.get("config_version", 0)
        if v >= CONFIG_VERSION:
            return False
        while v < CONFIG_VERSION:
            if v == 1:
                self.data.setdefault("ollama", {})
                self.data["ollama"].setdefault("prompt", DEFAULT_PROMPT)
                self.data["ollama"].setdefault("options", {})
                self.data["ollama"]["options"].setdefault("temperature", 0.2)
                self.data["ollama"]["options"].setdefault("num_ctx", 8192)
            v += 1
        self.data["config_version"] = CONFIG_VERSION
        return True

    def _ensure_defaults(self) -> bool:
        d = self.data
        orig = yaml.dump(d, default_flow_style=False)
        d.setdefault("inbox", "D:\\Main\\OpenCode\\Projekt_AI_Base\\inbox")
        d.setdefault("outbox", "D:\\Main\\OpenCode\\Obsidian\\AI-Chats")
        oll = d.setdefault("ollama", {})
        oll.setdefault("endpoint", "http://localhost:11434")
        oll.setdefault("model", "qwen2.5")
        oll.setdefault("prompt", DEFAULT_PROMPT)
        opts = oll.setdefault("options", {})
        opts.setdefault("temperature", 0.2)
        opts.setdefault("num_ctx", 8192)
        d.setdefault("services", {
            "deepseek.com": "deepseek",
            "gemini.google.com": "gemini",
            "glm5.ai": "glm5",
            "qwen.ai": "qwen",
        })
        d.setdefault("server", {}).setdefault("port", 18888)
        d.setdefault("timeout", {}).setdefault("ollama", 180)
        return yaml.dump(d, default_flow_style=False) != orig

    def get(self, *keys, default=None):
        cur = self.data
        for k in keys:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                return default
        return cur if cur is not None else default

    @staticmethod
    def _deep_merge(src: dict, update: dict):
        for k, v in update.items():
            if k in src and isinstance(src[k], dict) and isinstance(v, dict):
                Settings._deep_merge(src[k], v)
            else:
                src[k] = v

    def apply_patch(self, patch: dict):
        with self.lock:
            self._deep_merge(self.data, patch)
            self._save()

    def _save(self):
        with self.lock:
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.dump(self.data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # хелперы для частых полей
    @property
    def ollama_endpoint(self) -> str:
        return self.get("ollama", "endpoint", default="http://localhost:11434").rstrip("/")

    @property
    def ollama_model(self) -> str:
        return self.get("ollama", "model", default="qwen2.5")

    @property
    def ollama_timeout(self) -> int:
        return self.get("timeout", "ollama", default=180)

    @property
    def temperature(self) -> float:
        return self.get("ollama", "options", "temperature", default=0.2)

    @property
    def num_ctx(self) -> int:
        return self.get("ollama", "options", "num_ctx", default=8192)

    @property
    def prompt_text(self) -> str:
        return self.get("ollama", "prompt", default=DEFAULT_PROMPT)

    @property
    def inbox(self) -> Path:
        return Path(self.get("inbox", default="D:\\Main\\OpenCode\\Projekt_AI_Base\\inbox"))

    @property
    def outbox(self) -> Path:
        return Path(self.get("outbox", default="D:\\Main\\OpenCode\\Obsidian\\AI-Chats"))

    @property
    def server_port(self) -> int:
        return self.get("server", "port", default=18888)

    @property
    def services(self) -> dict:
        return self.get("services", default={
            "deepseek.com": "deepseek",
            "gemini.google.com": "gemini",
            "glm5.ai": "glm5",
            "qwen.ai": "qwen",
        })


CONFIG_PATH = Path(__file__).parent / "config.yml"
settings = Settings(CONFIG_PATH)


# ─── Утилиты ─────────────────────────────────────────────────────────────────
def short_hash(url: str, length: int = 6) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:length]


def detect_service(url: str) -> str:
    svc = settings.services
    for domain, name in svc.items():
        if domain in url:
            return name
    return "unknown"


def extract_url(content: str) -> str | None:
    m = re.search(r'<!--\s*SOURCE_URL:\s*(\S+)\s*-->', content)
    return m.group(1) if m else None


def extract_chat_id(content: str) -> str | None:
    m = re.search(r'<!--\s*CHAT_ID:\s*(\S+)\s*-->', content)
    return m.group(1) if m else None


def strip_service_comments(content: str) -> str:
    return re.sub(r'<!--\s*(?:SOURCE_URL|CHAT_ID):\s*\S+\s*-->\n?', '', content).strip()


def find_existing(chat_id: str) -> Path | None:
    if not chat_id:
        return None
    for f in sorted(settings.outbox.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            if f"chat_id: {chat_id}" in text:
                return f
        except Exception:
            continue
    return None


def build_filename(date_str: str, service: str, url_hash: str) -> str:
    return f"{date_str}_{service}_{url_hash}.md"


def extract_title(clean_content: str) -> str:
    lines = clean_content.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            return re.sub(r'^#\s+', '', line)[:80]
    return "AI Chat"


# ─── YAML-шапка ──────────────────────────────────────────────────────────────
def make_yaml(chat_id: str, service: str, title: str, tags: list[str], summary: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    data = {
        "chat_id": chat_id,
        "service": service,
        "date": today,
        "title": title,
        "tags": tags or [],
        "summary": summary or "",
    }
    return "---\n" + yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False).strip() + "\n---\n\n"


# ─── Парсинг ответа LLM ─────────────────────────────────────────────────────
def parse_llm_response(text: str) -> tuple[str, list[str]]:
    summary = ""
    tags: list[str] = []
    m = re.search(r"SUMMARY:\s*(.+?)(?=\nTAGS:|\Z)", text, re.DOTALL)
    if m:
        summary = m.group(1).strip()
    m = re.search(r"TAGS:\s*(.+?)$", text, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        tags = re.split(r"[,;\n]", raw)
        tags = [t.strip(" -*•\t") for t in tags if t.strip()]
    return summary, tags


# ─── AI-постобработка (Ollama) ──────────────────────────────────────────────
def ai_process(content: str, filename="", title="", chat_id="") -> tuple[list[str], str]:
    if not content:
        return [], ""

    prompt = settings.prompt_text
    subs = {
        "{content}": content[:PROMPT_CONTENT_LIMIT],
        "{filename}": filename or "unknown.md",
        "{title}": title or "AI Chat",
        "{chat_id}": chat_id or "unknown",
    }
    for k, v in subs.items():
        prompt = prompt.replace(k, v)

    try:
        resp = httpx.post(
            f"{settings.ollama_endpoint}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_ctx": settings.num_ctx, "temperature": settings.temperature},
            },
            timeout=settings.ollama_timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")

        summary, tags = parse_llm_response(text)

        if not tags and not summary:
            logging.warning("Ollama вернул пустой ответ, проверь модель/промпт")

        return tags, summary

    except httpx.ConnectError:
        logging.warning("Ollama не запущен — пропускаю AI-обработку")
        return [], ""
    except Exception as e:
        logging.warning(f"Ошибка Ollama: {e}")
        return [], ""


# ─── Тест промпта (без сохранения) ─────────────────────────────────────────
def test_prompt(prompt: str, content: str) -> str:
    subs = {
        "{content}": content[:TEST_CONTENT_LIMIT],
        "{filename}": "test.md",
        "{title}": "Тестовый чат",
        "{chat_id}": "test_preview",
    }
    for k, v in subs.items():
        prompt = prompt.replace(k, v)

    try:
        resp = httpx.post(
            f"{settings.ollama_endpoint}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_ctx": settings.num_ctx, "temperature": settings.temperature},
            },
            timeout=settings.ollama_timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        return f"Ошибка: {e}"


# ─── Основная обработка файла ────────────────────────────────────────────────
def process_file(filepath: Path) -> None:
    logging.info(f"Обработка: {filepath.name}")

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        logging.error(f"Не могу прочитать {filepath}: {e}")
        return

    if not content.strip():
        logging.warning(f"Пустой файл: {filepath.name}")
        return

    source_url = extract_url(content)
    chat_id_full = extract_chat_id(content)

    if source_url:
        url_hash = short_hash(source_url)
        service = detect_service(source_url)
    else:
        url_hash = short_hash(str(filepath.name))
        service = detect_service(str(filepath))
        if service == "unknown":
            service = "ai-chat"

    if not chat_id_full:
        chat_id_full = f"{service}_{url_hash}"

    # Проверка дублей
    existing = find_existing(chat_id_full)
    if existing:
        logging.info(f"Дубль по chat_id: {existing.name} — обновляю")

    # Очищаем контент от служебных комментариев
    clean_content = strip_service_comments(content)
    title = extract_title(clean_content)

    # AI-обработка
    tags, summary = ai_process(clean_content, filename=filepath.name, title=title, chat_id=chat_id_full)

    # Финальный файл
    yaml_header = make_yaml(chat_id_full, service, title, tags, summary)
    final_content = yaml_header + clean_content

    date_str = datetime.now().strftime("%Y-%m-%d")
    title_slug = re.sub(r'[<>:"/\\|?*]', '', title)
    title_slug = re.sub(r'\s+', '-', title_slug).strip('-').lower() or 'untitled'
    filename = f"{title_slug}_{service}_{date_str}_{url_hash}.md"
    outpath = settings.outbox / filename

    if existing and existing.resolve() != outpath.resolve():
        logging.info(f"Удаляю старый файл: {existing.name}")
        try:
            existing.unlink()
        except Exception as e:
            logging.warning(f"Не удалось удалить {existing.name}: {e}")

    outpath.write_text(final_content, encoding="utf-8")
    logging.info(f"Сохранено: {outpath.name}")

    # Удаляем исходный файл из inbox
    try:
        filepath.unlink()
        logging.info(f"Удалён из inbox: {filepath.name}")
    except Exception as e:
        logging.warning(f"Не удалось удалить {filepath.name}: {e}")


# ─── HTTP-сервер для приёма файлов ──────────────────────────────────────────
class SaveHandler(BaseHTTPRequestHandler):
    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/settings":
            self._json_response(settings.data)
        elif path == "/model":
            self._json_response({"model": settings.ollama_model})
        else:
            self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._json_response({"error": "empty body"}, 400)
            return

        try:
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._json_response({"error": f"invalid json: {e}"}, 400)
            return

        if path == "/save":
            filename = body.get("filename", "unknown_raw.md")
            content = body.get("content", "")

            if not content:
                self._json_response({"error": "empty content"}, 400)
                return

            inbox = settings.inbox
            inbox.mkdir(parents=True, exist_ok=True)
            filepath = inbox / filename

            filepath.write_text(content, encoding="utf-8")
            logging.info(f"HTTP получен: {filename} (ожидает watchdog)")

            self._json_response({"ok": True})

        elif path == "/model":
            new_model = body.get("model", "")
            if new_model:
                settings.apply_patch({"ollama": {"model": new_model}})
                logging.info(f"Модель сменена на: {new_model}")
            self._json_response({"model": settings.ollama_model})

        elif path == "/outbox":
            new_path = body.get("path", "")
            if new_path:
                settings.apply_patch({"outbox": new_path})
                Path(new_path).mkdir(parents=True, exist_ok=True)
                logging.info(f"Outbox сменён на: {new_path}")
            self._json_response({"path": settings.get("outbox", default="")})

        elif path == "/settings":
            settings.apply_patch(body)
            logging.info(f"Settings updated via API: temperature={settings.temperature}")
            self._json_response({"ok": True})

        elif path == "/prompt/test":
            prompt = body.get("prompt", settings.prompt_text)
            content = body.get("content", "")
            result = test_prompt(prompt, content)
            logging.info(f"Prompt test: {len(content)} chars")
            self._json_response({"ok": True, "result": result})

        else:
            self._json_response({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        logging.info("HTTP: " + fmt % args)


def run_server(port: int):
    server = HTTPServer(("127.0.0.1", port), SaveHandler)
    logging.info(f"HTTP-сервер на http://127.0.0.1:{port}/save")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


# ─── Watchdog-обработчик ─────────────────────────────────────────────────────
class InboxHandler(FileSystemEventHandler):
    def __init__(self, cooldown=5):
        self._processed = {}
        self._cooldown = cooldown

    def _handle(self, event):
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        now = time.time()
        last = self._processed.get(event.src_path, 0)
        if now - last < self._cooldown:
            return
        self._processed[event.src_path] = now
        time.sleep(1.5)
        if not Path(event.src_path).exists():
            return
        process_file(Path(event.src_path))

    def on_created(self, event):
        self._handle(event)

    def on_modified(self, event):
        self._handle(event)


# ─── CLI ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="AI-Chats Pipeline")
    parser.add_argument("--once", action="store_true", help="Обработать существующие файлы и выйти")
    parser.add_argument("--serve", action="store_true", help="Запустить HTTP-сервер для приёма файлов")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    inbox = settings.inbox
    outbox = settings.outbox
    model = settings.ollama_model
    port = settings.server_port

    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)

    # Обработка существующих
    files = sorted(inbox.glob("*.md"))
    if files:
        logging.info(f"Найдено {len(files)} файлов в inbox — обрабатываю")
        for f in files:
            process_file(f)

    if args.once:
        logging.info("--once: готово")
        return

    # HTTP-сервер (если --serve)
    if args.serve:
        from threading import Thread
        server_thread = Thread(target=run_server, args=(port,), daemon=True)
        server_thread.start()

    # Watchdog-режим
    event_handler = InboxHandler()
    observer = Observer()
    observer.schedule(event_handler, str(inbox), recursive=False)
    observer.start()
    logging.info(f"Слежу за: {inbox}")
    logging.info(f"Сохраняю в: {outbox}")
    logging.info(f"Модель Ollama: {model}")
    if args.serve:
        logging.info(f"HTTP-сервер: http://127.0.0.1:{port}/save")
    logging.info("Ожидание новых файлов (Ctrl+C для выхода)...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Завершение...")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
