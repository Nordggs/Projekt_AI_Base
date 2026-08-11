"""IR builder — single entry point for raw dict → ConversationModel."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from conversation.models import (
    AttachmentNode,
    ConversationModel,
    ConversationTree,
    Message,
    NodeID,
    TreeNode,
)


class Provider(str, Enum):
    CHATGPT_API = "chatgpt_api"
    CHATGPT_DOM = "chatgpt_dom"
    GEMINI = "gemini"
    CLAUDE = "claude"
    QWEN = "qwen"
    DEEPSEEK = "deepseek"


def _normalize_ts(ts: Any) -> float | None:
    if ts is None:
        return None
    try:
        return float(ts)
    except (ValueError, TypeError):
        return None


class IRBuilder:
    """Build ConversationModel from provider-specific raw dict."""

    @staticmethod
    def build(
        provider: Provider,
        raw: dict,
        *,
        url: str = "",
        log_func: Callable | None = None,
    ) -> ConversationModel:
        match provider:
            case Provider.CHATGPT_API:
                return IRBuilder._from_chatgpt_api(raw, url, log_func)
            case Provider.CHATGPT_DOM:
                return IRBuilder._from_chatgpt_dom(raw)
            case Provider.GEMINI:
                return IRBuilder._from_generic("gemini", raw, "dom")
            case Provider.CLAUDE:
                return IRBuilder._from_generic("claude", raw, "dom")
            case Provider.QWEN:
                return IRBuilder._from_generic("qwen", raw, "dom")
            case Provider.DEEPSEEK:
                return IRBuilder._from_generic("deepseek", raw, "scroll")
            case _:
                raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _parse_message_from_api(msg: dict) -> Message:
        content_parts: list[str] = []
        attachments: list[AttachmentNode] = []
        for part in msg.get("content", {}).get("parts", []):
            if isinstance(part, str):
                content_parts.append(part)
            elif isinstance(part, dict):
                if "prompt" in part:
                    atype = "generated_image"
                elif part.get("content_type", "").startswith("image/"):
                    atype = "image"
                else:
                    atype = "file"

                att = AttachmentNode(
                    type=atype,
                    mime=part.get("content_type"),
                    name=None,
                    meta={},
                )

                if "prompt" in part:
                    att["meta"]["prompt"] = part["prompt"]
                    if part.get("size"):
                        att["meta"]["size"] = part["size"]

                if "asset_pointer" in part:
                    if part.get("size_bytes"):
                        att["meta"]["size"] = part["size_bytes"]
                    if part.get("width"):
                        att["meta"]["width"] = part["width"]
                    if part.get("height"):
                        att["meta"]["height"] = part["height"]

                attachments.append(att)

        content_text = " ".join(t for t in content_parts if t).strip()

        return Message(
            role=msg.get("author", {}).get("role", "assistant"),
            content=content_text,
            timestamp=_normalize_ts(msg.get("create_time")),
            message_id=msg.get("id"),
            attachments=attachments,
        )

    @staticmethod
    def _build_tree(mapping: dict, current_node: str) -> tuple[ConversationTree, dict]:
        nodes: dict[NodeID, TreeNode] = {}
        root_id: str | None = None

        stats = {
            "total_nodes": len(mapping),
            "root_nodes": 0,
            "text_only": 0,
            "attachment_only": 0,
            "text_with_attachments": 0,
            "empty": 0,
        }

        for nid, node_data in mapping.items():
            msg_data = node_data.get("message")
            message = IRBuilder._parse_message_from_api(msg_data) if msg_data else None
            parent = node_data.get("parent")
            children = node_data.get("children", [])

            if parent is None and not root_id:
                root_id = nid

            if message is None:
                stats["root_nodes"] += 1
            else:
                content = (message.content or "").strip()
                has_text = bool(content)
                has_att = bool(message.attachments)
                if has_text and has_att:
                    stats["text_with_attachments"] += 1
                elif has_text:
                    stats["text_only"] += 1
                elif has_att:
                    stats["attachment_only"] += 1
                else:
                    stats["empty"] += 1

            nodes[nid] = TreeNode(
                id=nid,
                message=message,
                parent_id=parent,
                children_ids=list(children) if isinstance(children, list) else [],
                is_active_branch=(nid == current_node),
            )

        if root_id is None and mapping:
            root_id = next(iter(mapping.keys()))

        if current_node and current_node in nodes:
            cur = current_node
            while cur:
                if cur in nodes:
                    nodes[cur].is_active_branch = True
                node_data = mapping.get(cur, {})
                cur = node_data.get("parent")

        return ConversationTree(
            nodes=nodes,
            root_id=root_id,
            active_node_id=current_node,
        ), stats

    @staticmethod
    def _from_chatgpt_api(
        raw: dict, url: str, log_func: Callable | None = None
    ) -> ConversationModel:
        mapping = raw.get("mapping", {})
        current_node = raw.get("current_node")
        conv_id = raw.get("conversation_id", "")
        title = raw.get("title", "")

        tree, stats = IRBuilder._build_tree(mapping, current_node) if mapping else (None, {})

        messages = tree.get_active_branch() if tree else []

        if log_func and stats:
            log_func(
                f"[CHATGPT] Parse stats: "
                f"total_nodes={stats['total_nodes']} "
                f"root_nodes={stats['root_nodes']} "
                f"text_only={stats['text_only']} "
                f"attachment_only={stats['attachment_only']} "
                f"text_with_attachments={stats['text_with_attachments']} "
                f"empty={stats['empty']} "
                f"exported={len(messages)}"
            )

        metadata = {
            "provider": "chatgpt",
            "provider_chat_id": conv_id,
            "create_time": raw.get("create_time"),
            "update_time": raw.get("update_time"),
            "model": raw.get("default_model_slug"),
            "source": "api",
        }

        stable_id = conv_id[:8] if conv_id else title[:12]

        return ConversationModel(
            source="chatgpt",
            stable_id=stable_id,
            title=title or "ChatGPT Chat",
            source_url=url,
            messages=messages,
            tree=tree,
            metadata=metadata,
        )

    @staticmethod
    def _from_chatgpt_dom(raw: dict) -> ConversationModel:
        messages = []
        for m in raw.get("messages", []):
            messages.append(Message(
                role=m.get("role", "assistant"),
                content=m.get("content", ""),
                timestamp=m.get("timestamp"),
                message_id=m.get("message_id"),
                attachments=m.get("attachments", []),
            ))

        return ConversationModel(
            source="chatgpt",
            stable_id=raw.get("chat_id", ""),
            title=raw.get("title", "ChatGPT Chat"),
            source_url=raw.get("source_url", ""),
            messages=messages,
            tree=None,
            metadata={"provider": "chatgpt", "source": "dom"},
        )

    @staticmethod
    def _from_generic(provider_name: str, raw: dict, source_label: str) -> ConversationModel:
        messages = []
        for m in raw.get("messages", []):
            messages.append(Message(
                role=m.get("role", "assistant"),
                content=m.get("content", ""),
                timestamp=m.get("timestamp"),
            ))

        return ConversationModel(
            source=provider_name,
            stable_id=raw.get("chat_id", ""),
            title=raw.get("title", ""),
            source_url=raw.get("source_url", ""),
            messages=messages,
            tree=None,
            metadata={"provider": provider_name, "source": source_label},
        )
