import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from conversation.serializer import tree_to_dict


class ExportWriter:
    def __init__(self, out_dir="raw"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    _DISPLAY_PROVIDER = {
        "chatgpt": "ChatGPT",
        "deepseek": "DeepSeek",
        "claude": "Claude",
        "gemini": "Gemini",
        "qwen": "Qwen",
    }

    _HASH_SEMANTIC_FIELDS = {"title", "role", "content", "timestamp"}

    def _data_for_hash(self, data):
        """Strip technical/download fields — hash only semantic content."""
        d = {"title": data.get("title", ""), "messages": []}
        for msg in data.get("messages", []):
            entry = {}
            for k in self._HASH_SEMANTIC_FIELDS:
                if k in msg:
                    entry[k] = msg[k]
            atts = msg.get("attachments", [])
            if atts:
                entry["attachments"] = []
                for a in atts:
                    ea = {
                        "type": a.get("type"),
                        "mime": a.get("mime"),
                        "name": a.get("name"),
                        "meta": {},
                    }
                    if "prompt" in a.get("meta", {}):
                        ea["meta"]["prompt"] = a["meta"]["prompt"]
                    entry["attachments"].append(ea)
            d["messages"].append(entry)
        return d

    def _hash(self, data):
        semantic = self._data_for_hash(data)
        raw = json.dumps(semantic, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def _stable_id(self, data):
        cid = data.get("chat_id") or data.get("id")
        if cid and not re.match(r'^(claude|chatgpt|qwen|deepseek)-\d+$', str(cid)):
            return self._sanitize_filename(str(cid))[:40]
        url = data.get("url") or data.get("source_url")
        if url:
            seg = url.rstrip("/").split("/")[-1]
            if seg and seg != url:
                return self._sanitize_filename(seg)[:40]
        title = data.get("title", "untitled")
        raw = f"{title}|{data.get('source','')}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def _stable(self, provider, stable_id):
        raw = f"{provider}|{stable_id}"
        return hashlib.sha1(raw.encode()).hexdigest()[:8]

    _GENERIC_TITLES = frozenset({
        "untitled", "chat", "new-chat", "discussion",
        "google-gemini", "gemini", "google-gemini-chat",
        "chatgpt", "chatgpt-chat",
        "deepseek", "deepseek-chat",
        "qwen", "qwen-chat",
        "claude", "claude-chat", "claude-ai",
    })

    _USER_PREFIXES = re.compile(
        r"^(ваш\s+запрос|your\s+request)[\s\n]*",
        re.IGNORECASE,
    )

    def _human_tag(self, data):
        title = data.get("title") or ""
        if title.strip():
            cleaned = self._sanitize_filename(title).lower()
            cleaned = re.sub(r'[\s_]+', "-", cleaned).strip("-")
            cleaned = re.sub(r'[^\w-]', "", cleaned)
            if cleaned and cleaned not in self._GENERIC_TITLES:
                return cleaned[:25]
        for m in data.get("messages", []):
            if m.get("role") == "user":
                txt = m.get("content", "")
                if txt.strip():
                    txt = self._USER_PREFIXES.sub("", txt).strip()
                    if not txt:
                        continue
                    slug = self._sanitize_filename(txt).lower()
                    slug = re.sub(r'[\s_]+', "-", slug).strip("-")
                    slug = re.sub(r'[^\w-]', "", slug)
                    if slug and slug not in self._GENERIC_TITLES:
                        return slug[:25]
        return "chat"

    def _sanitize_filename(self, name):
        name = str(name or "")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        name = name.rstrip(". ")
        name = re.sub(r'\s+', " ", name).strip()
        return name or "untitled"

    def _read_hash(self, path):
        try:
            for line in path.read_text(encoding="utf-8").split("\n"):
                if line.startswith("<!-- hash:"):
                    m = re.match(r'<!-- hash: (\w+)', line)
                    if m:
                        return m.group(1)
            return ""
        except Exception:
            return ""

    def _find_by_stable(self, sub_dir, stable):
        if not sub_dir.is_dir():
            return None
        pat = re.compile(rf".+_{re.escape(stable)}\.md$")
        for f in sub_dir.iterdir():
            if f.is_file() and pat.match(f.name):
                return f
        return None

    @staticmethod
    def _count_stats(messages):
        total = len(messages)
        user_c = sum(1 for m in messages if m.get("role") == "user")
        assistant_c = total - user_c
        att_count = sum(len(m.get("attachments", [])) for m in messages)
        return total, user_c, assistant_c, att_count

    def write(self, data, chat_order=0):
        from conversation.models import ConversationModel as CM
        is_model = isinstance(data, CM)
        tree_data = data.tree if is_model else data.get("tree")
        raw_dict = data.to_dict() if is_model else data

        if not raw_dict.get("messages"):
            return None
        provider = raw_dict.get("source", "deepseek").lower()
        stable_id = self._stable_id(raw_dict)
        stable = self._stable(provider, stable_id)
        tag = self._human_tag(raw_dict)
        order = f"{min(chat_order, 99):02d}"

        sub_dir = self.out_dir / provider
        sub_dir.mkdir(parents=True, exist_ok=True)

        existing = self._find_by_stable(sub_dir, stable)
        if existing:
            old_hash = self._read_hash(existing)
            new_hash = self._hash(raw_dict)[:6]
            if old_hash == new_hash:
                return existing
            path = existing
        else:
            filename = f"{provider}_{order}_{tag}_{stable}.md"
            path = sub_dir / filename

        # Media directory for this chat: raw/{provider}/media/{stable}/
        assets_dir = self.out_dir / provider / "media" / stable
        assets_dir.mkdir(parents=True, exist_ok=True)

        content_hash = self._hash(raw_dict)[:6]
        display_name = self._DISPLAY_PROVIDER.get(provider, provider.capitalize())

        if tree_data:
            # Write tree.json alongside .md
            tree_dict = tree_to_dict(tree_data) if is_model else tree_data
            tree_path = path.with_suffix(".tree.json")
            tree_path.write_text(
                json.dumps(tree_dict, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            md = self._to_markdown_with_branches(
                raw_dict, tree_data, content_hash, tag, chat_order,
                stable=stable, display_name=display_name,
                assets_dir=assets_dir, is_model=is_model,
            )
        else:
            md = self._to_markdown(
                raw_dict, content_hash, tag, chat_order,
                stable=stable, display_name=display_name,
                assets_dir=assets_dir,
            )
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(md, encoding="utf-8")
        tmp.replace(path)
        return path

    def flush_media(self, model):
        """Write _captured assets to media/ dir, set local paths. Returns count."""
        from conversation.models import ConversationModel as CM
        if not isinstance(model, CM):
            return 0
        stable_id = model.stable_id
        media_dir = self.out_dir / "media" / stable_id
        media_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for msg in model.messages:
            for att in msg.attachments:
                capt = att.__dict__.pop("_captured", None)
                if not capt:
                    continue
                ext = capt.content_type.split("/")[-1] if "/" in capt.content_type else "bin"
                name = f"{hashlib.md5(capt.url.encode()).hexdigest()[:16]}.{ext}"
                (media_dir / name).write_bytes(capt.body)
                att.local = {"relative_path": f"media/{stable_id}/{name}"}
                if capt.content_type.startswith("image/"):
                    att.type = "image"
                count += 1
        return count

    def _fmt_ts(self, ts):
        if not ts:
            return ""
        try:
            t = float(ts)
            if t > 1e9:
                return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            pass
        s = str(ts).strip()
        if re.match(r'^\d{4}-\d{2}-\d{2}', s):
            return s[:16].replace("T", " ")
        return s[:16]

    def _branch_to_markdown(self, messages, branch_label=""):
        lines = []
        if branch_label:
            lines.append(f"🌿 {branch_label}")
            lines.append("")
        for msg in messages:
            role = msg.get("role", "unknown")
            ts = self._fmt_ts(msg.get("timestamp"))
            label = "#### 👤 Вы" if role == "user" else "#### 🤖 AI"
            if ts:
                label += f" ({ts})"
            lines.append(label)
            content = msg.get("content", "")
            if content:
                lines.append(content)
            attachments = msg.get("attachments", [])
            if attachments:
                lines.append("")
                lines.append("##### Вложения")
                for att in attachments:
                    self._append_attachment(lines, att)
            lines.append("")
        return lines

    def _append_attachment(self, lines, att):
        local = att.get("local")
        if local:
            rel = local.get("relative_path") if isinstance(local, dict) else None
            if rel:
                lines.append(f"![]({rel})")
                return
        atype = att.get("type", "file")
        name = att.get("name") or atype
        meta = att.get("meta", {}) or {}
        if atype == "generated_image":
            prompt = meta.get("prompt", "")
            lines.append("")
            lines.append("> 🎨 Сгенерировано изображение:")
            if prompt:
                lines.append(f"> {prompt}")
            meta_pid = meta.get("api_id") or meta.get("provider_id")
            if meta_pid:
                lines.append(f"> Asset: `{meta_pid}`")
        elif atype in ("image", "file", "audio", "video"):
            lines.append("")
            is_partial = att.get("is_partial", True)
            lines.append(f"> 📎 Вложение: {name if name != atype else 'файл'}" + (" (metadata-only)" if is_partial else ""))
            mime = att.get("mime")
            if mime:
                lines.append(f"> Тип: {atype} / {mime}")
            else:
                lines.append(f"> Тип: {atype}")
            pointer = att.get("pointer")
            if pointer:
                lines.append(f"> Pointer: `{pointer}`")
        elif atype == "runtime_asset":
            lines.append("")
            lines.append(f"> 📸 Runtime image (confidence: {meta.get('confidence', 'unknown')})")
            mime = att.get("mime")
            if mime:
                lines.append(f"> Тип: {mime}")
        else:
            lines.append("")
            lines.append(f"> 📎 Вложение: {name}")

    def _to_markdown_with_branches(self, data, tree_data, h="", title_slug="",
                                   chat_order=0, stable="", display_name="Chat",
                                   assets_dir=None, is_model=False):
        from conversation.models import ConversationModel as CM
        from conversation.serializer import dict_to_tree

        if is_model:
            tree = tree_data
        elif isinstance(tree_data, dict):
            tree = dict_to_tree(tree_data)
        else:
            tree = tree_data

        title = (data.get("title") or "").strip()
        source_url = (data.get("source_url") or "").strip()
        messages = data.get("messages", [])
        total_msgs, user_c, assistant_c, att_count = self._count_stats(messages)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        parts = []
        parts.append("<!-- schema_version: 3 -->")
        parts.append("")
        if title:
            parts.append(f"# {title}")
            parts.append("")
        parts.append(f"**Источник:** {display_name}")
        parts.append("")
        parts.append(f"**Оригинал:**")
        parts.append(f"{source_url}")
        parts.append("")
        parts.append(f"**Дата экспорта:**")
        parts.append(f"{now}")
        parts.append("")
        parts.append(f"**Stable ID:**")
        parts.append(f"{stable}")
        parts.append("")
        parts.append(f"**Экспортировано сообщений (основная ветка):**")
        parts.append(f"{total_msgs} (Пользователь: {user_c}, AI: {assistant_c})")
        if att_count:
            parts.append("")
            parts.append("**Вложений:**")
            parts.append(f"{att_count}")
        parts.append("")
        parts.append("---")
        parts.append("")

        parts.append(f"<!-- hash: {h} title: {title_slug} chat_order: {chat_order} -->")
        parts.append("")

        # Active branch
        parts.extend(self._branch_to_markdown(messages))

        # Additional branches
        if tree:
            active_branch = tree.get_active_branch()
            active_content = {(m.content[:200] if m.content else "") for m in active_branch}
            branches = tree.get_all_branches()
            alt_idx = 0
            for branch in branches:
                branch_content = {(m.content[:200] if m.content else "") for m in branch}
                if branch_content == active_content or not branch:
                    continue
                alt_idx += 1
                label = f"Альтернативная ветвь {alt_idx}"
                parts.append("")
                parts.append("---")
                parts.append("")
                parts.extend(self._branch_to_markdown(
                    [{"role": m.role, "content": m.content,
                      "timestamp": m.timestamp, "attachments": m.attachments}
                       for m in branch],
                    branch_label=label,
                ))

        return "\n".join(parts)

    def _to_markdown(self, data, h="", title_slug="", chat_order=0,
                     stable="", display_name="Chat", assets_dir=None):
        title = (data.get("title") or "").strip()
        source_url = (data.get("source_url") or "").strip()
        messages = data.get("messages", [])
        total_msgs, user_c, assistant_c, att_count = self._count_stats(messages)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        parts = []

        # Schema
        parts.append("<!-- schema_version: 2 -->")
        parts.append("")

        # Title
        if title:
            parts.append(f"# {title}")
            parts.append("")

        # Summary block
        parts.append(f"**Источник:** {display_name}")
        parts.append("")
        parts.append(f"**Оригинал:**")
        parts.append(f"{source_url}")
        parts.append("")
        parts.append(f"**Дата экспорта:**")
        parts.append(f"{now}")
        parts.append("")
        parts.append(f"**Stable ID:**")
        parts.append(f"{stable}")
        parts.append("")
        parts.append(f"**Экспортировано сообщений:**")
        parts.append(f"{total_msgs} (Пользователь: {user_c}, AI: {assistant_c})")
        if att_count:
            parts.append("")
            parts.append(f"**Вложений:**")
            parts.append(f"{att_count}")
        parts.append("")
        parts.append("---")
        parts.append("")

        # Hash comment
        parts.append(f"<!-- hash: {h} title: {title_slug} chat_order: {chat_order} -->")
        parts.append("")

        # Messages
        for msg in messages:
            role = msg.get("role", "unknown")
            ts = self._fmt_ts(msg.get("timestamp"))
            if role == "user":
                label = "#### 👤 Вы"
            else:
                label = "#### 🤖 AI"
            if ts:
                label += f" ({ts})"
            parts.append(label)

            content = msg.get("content", "")
            if content:
                parts.append(content)

            # Attachments
            attachments = msg.get("attachments", [])
            if attachments:
                parts.append("")
                parts.append("##### Вложения")
                for att in attachments:
                    self._append_attachment(parts, att)

            parts.append("")

        return "\n".join(parts)
