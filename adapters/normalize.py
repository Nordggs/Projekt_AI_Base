PRESERVE_KEYS = {"timestamp", "attachments", "message_id"}


def normalize_messages(raw_messages: list) -> list[dict]:
    result = []
    for msg in raw_messages:
        role = msg.get("role", "assistant")
        content = str(msg.get("content", "") or "").strip()
        if not content and not (msg.get("attachments") or []):
            continue
        entry = {"role": role, "content": content}
        for key in PRESERVE_KEYS:
            if key in msg:
                entry[key] = msg[key]
        result.append(entry)
    return result
