from conversation.models import (
    ConversationModel, ConversationTree, TreeNode, Message, NodeID,
)


def _parse_message_from_api(msg: dict) -> Message:
    content_parts = []
    attachments = []
    for part in msg.get("content", {}).get("parts", []):
        if isinstance(part, str):
            content_parts.append(part)
        elif isinstance(part, dict):
            if "prompt" in part:
                attachments.append({
                    "type": "generated_image",
                    "mime": None,
                    "name": None,
                    "caption": None,
                    "meta": {"prompt": part["prompt"], "size": part.get("size")},
                })
            elif "asset_pointer" in part:
                attachments.append({
                    "type": "image",
                    "mime": part.get("content_type"),
                    "name": None,
                    "caption": None,
                    "source": {"provider_id": part["asset_pointer"], "remote_url": None},
                    "meta": {
                        "size": part.get("size_bytes"),
                        "width": part.get("width"),
                        "height": part.get("height"),
                    },
                })
            else:
                attachments.append({
                    "type": "file",
                    "mime": None,
                    "name": None,
                    "caption": None,
                    "meta": {},
                })

    content_text = " ".join(t for t in content_parts if t).strip()
    ts = msg.get("create_time")
    if ts is not None:
        try:
            ts = str(int(float(ts)))
        except (ValueError, TypeError):
            ts = None

    return Message(
        role=msg.get("author", {}).get("role", "assistant"),
        content=content_text,
        timestamp=ts,
        message_id=msg.get("id"),
        attachments=attachments,
    )


def _build_tree(mapping: dict, current_node: str) -> tuple[ConversationTree, dict]:
    nodes: dict[NodeID, TreeNode] = {}
    root_id = None

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
        message = _parse_message_from_api(msg_data) if msg_data else None
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


def from_chatgpt_api(raw: dict, source_url: str, log_func=None) -> ConversationModel:
    mapping = raw.get("mapping", {})
    current_node = raw.get("current_node")
    conv_id = raw.get("conversation_id", "")
    title = raw.get("title", "")

    tree, stats = _build_tree(mapping, current_node) if mapping else (None, {})

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
        source_url=source_url,
        messages=messages,
        tree=tree,
        metadata=metadata,
    )


def from_next_data(page) -> ConversationModel | None:
    try:
        raw = page.evaluate("""() => {
            try {
                const nd = window.__NEXT_DATA__;
                if (!nd || !nd.props || !nd.props.pageProps) return null;
                const conv = nd.props.pageProps.conversation;
                if (!conv || !conv.mapping) return null;
                return JSON.parse(JSON.stringify(conv));
            } catch(e) { return null; }
        }""")
        if not raw:
            return None
        url = page.evaluate("location.href")
        model = from_chatgpt_api(raw, url, log_func=None)
        model.metadata["source"] = "next_data"
        return model
    except Exception:
        return None


def from_dom(dom_data: dict) -> ConversationModel:
    messages = []
    for m in dom_data.get("messages", []):
        messages.append(Message(
            role=m.get("role", "assistant"),
            content=m.get("content", ""),
            timestamp=m.get("timestamp"),
            message_id=m.get("message_id"),
            attachments=m.get("attachments", []),
        ))

    return ConversationModel(
        source="chatgpt",
        stable_id=dom_data.get("chat_id", ""),
        title=dom_data.get("title", "ChatGPT Chat"),
        source_url=dom_data.get("source_url", ""),
        messages=messages,
        tree=None,
        metadata={
            "provider": "chatgpt",
            "source": "dom",
        },
    )
