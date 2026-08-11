from conversation.models import (
    AttachmentNode, ConversationModel, ConversationTree, TreeNode, Message, NodeID,
)


def model_to_dict(model: ConversationModel) -> dict:
    return {
        "schema_version": 2,
        "source": model.source,
        "chat_id": model.stable_id,
        "title": model.title,
        "source_url": model.source_url,
        "messages": [_message_to_dict(m) for m in model.messages],
        "tree": tree_to_dict(model.tree) if model.tree else None,
        "metadata": dict(model.metadata),
    }


def _att_to_dict(a: AttachmentNode) -> dict:
    d = {"type": a.type, "mime": a.mime, "name": a.name, "meta": dict(a.meta),
         "source": a.source, "is_partial": a.is_partial,
         "confidence": a.confidence, "timestamp": a.timestamp}
    if a.pointer:
        d["pointer"] = a.pointer
    if a.api_id:
        d["api_id"] = a.api_id
    if a.local:
        d["local"] = dict(a.local)
    if a.url:
        d["url"] = a.url
    return d


def _dict_to_att(d: dict) -> AttachmentNode:
    return AttachmentNode(
        type=d.get("type", "file"),
        mime=d.get("mime"),
        name=d.get("name"),
        meta=d.get("meta", {}),
        local=d.get("local"),
        source=d.get("source", "api"),
        is_partial=d.get("is_partial", True),
        confidence=d.get("confidence", 1.0),
        timestamp=d.get("timestamp"),
        pointer=d.get("pointer"),
        api_id=d.get("api_id"),
        url=d.get("url"),
    )


def _message_to_dict(m: Message) -> dict:
    d = {"role": m.role, "content": m.content}
    if m.timestamp:
        d["timestamp"] = m.timestamp
    if m.message_id:
        d["message_id"] = m.message_id
    if m.attachments:
        d["attachments"] = [_att_to_dict(a) for a in m.attachments]
    return d


def _dict_to_message(d: dict) -> Message:
    raw_atts = d.get("attachments", [])
    atts = [_dict_to_att(a) if isinstance(a, dict) else a for a in raw_atts]
    return Message(
        role=d.get("role", "assistant"),
        content=d.get("content", ""),
        timestamp=d.get("timestamp"),
        message_id=d.get("message_id"),
        attachments=atts,
    )


def tree_to_dict(tree: ConversationTree) -> dict:
    nodes = {}
    for nid, node in tree.nodes.items():
        nodes[nid] = {
            "id": node.id,
            "message": _message_to_dict(node.message) if node.message else None,
            "parent_id": node.parent_id,
            "children_ids": node.children_ids,
            "is_active_branch": node.is_active_branch,
        }
    return {
        "nodes": nodes,
        "root_id": tree.root_id,
        "active_node_id": tree.active_node_id,
    }


def dict_to_tree(d: dict) -> ConversationTree:
    nodes = {}
    for nid, nd in d.get("nodes", {}).items():
        msg_dict = nd.get("message")
        nodes[nid] = TreeNode(
            id=nd["id"],
            message=_dict_to_message(msg_dict) if msg_dict and isinstance(msg_dict, dict) else None,
            parent_id=nd.get("parent_id"),
            children_ids=nd.get("children_ids", []),
            is_active_branch=nd.get("is_active_branch", False),
        )
    return ConversationTree(
        nodes=nodes,
        root_id=d.get("root_id"),
        active_node_id=d.get("active_node_id"),
    )


def dict_to_model(d: dict) -> ConversationModel:
    tree_data = d.get("tree")
    return ConversationModel(
        source=d.get("source", ""),
        stable_id=d.get("chat_id", ""),
        title=d.get("title", ""),
        source_url=d.get("source_url", ""),
        messages=[_dict_to_message(m) for m in d.get("messages", [])],
        tree=dict_to_tree(tree_data) if tree_data else None,
        metadata=d.get("metadata", {}),
    )
