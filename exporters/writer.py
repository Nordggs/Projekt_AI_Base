import hashlib
import json
from datetime import datetime
from pathlib import Path


class ExportWriter:
    def __init__(self, out_dir="raw"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _hash(self, data: dict) -> str:
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def write(self, data: dict) -> Path:
        title = data.get("title", "untitled")
        service = data.get("source", "deepseek").lower()
        date = datetime.now().strftime("%Y-%m-%d")
        h = self._hash(data)
        sub_dir = self.out_dir / service
        sub_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{title}_{service}_{date}_{h}.md"
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
