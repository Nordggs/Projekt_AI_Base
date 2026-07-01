from conversation.models import (
    ConversationModel, ConversationTree, TreeNode, Message, NodeID,
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


def _message_to_dict(m: Message) -> dict:
    d = {"role": m.role, "content": m.content}
    if m.timestamp:
        d["timestamp"] = m.timestamp
    if m.message_id:
        d["message_id"] = m.message_id
    if m.attachments:
        d["attachments"] = m.attachments
    return d


def _dict_to_message(d: dict) -> Message:
    return Message(
        role=d.get("role", "assistant"),
        content=d.get("content", ""),
        timestamp=d.get("timestamp"),
        message_id=d.get("message_id"),
        attachments=d.get("attachments", []),
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
