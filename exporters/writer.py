import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path


class ExportWriter:
    def __init__(self, out_dir="raw"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _hash(self, data: dict) -> str:
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def _sanitize_filename(self, name: str) -> str:
        name = str(name or "")
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        name = name.rstrip(". ")
        return name or "untitled"

    def _safe_path(self, path: str) -> str:
        if len(path) > 200:
            base, ext = os.path.splitext(path)
            path = base[:180] + ext
        return path

    def write(self, data: dict) -> Path:
        if not data.get("messages"):
            return None
        title = self._sanitize_filename(data.get("title", "untitled"))
        title = title[:120]
        service = data.get("source", "deepseek").lower()
        date = datetime.now().strftime("%Y-%m-%d")
        h = self._hash(data)
        sub_dir = self.out_dir / service
        sub_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{title}_{service}_{date}_{h}.md"
        filename = self._safe_path(filename)
        path = sub_dir / filename
        md = self._to_markdown(data)
        path.write_text(md, encoding="utf-8")
        return path

    def _to_markdown(self, data: dict) -> str:
        parts = []
        for msg in data.get("messages", []):
            role = msg.get("role", "unknown")
            if role == "user":
                parts.append("#### 👤 Вы")
            else:
                parts.append("#### 🤖 AI")
            parts.append(msg.get("content", ""))
            parts.append("")
        return "\n".join(parts)
