from dataclasses import dataclass, field
from typing import Optional

NodeID = str


@dataclass
class Message:
    role: str
    content: str
    timestamp: Optional[str] = None
    message_id: Optional[str] = None
    attachments: list[dict] = field(default_factory=list)


@dataclass
class TreeNode:
    id: NodeID
    message: Optional[Message] = None
    parent_id: Optional[NodeID] = None
    children_ids: list[NodeID] = field(default_factory=list)
    is_active_branch: bool = False


@dataclass
class ConversationTree:
    nodes: dict[NodeID, TreeNode]
    root_id: Optional[NodeID] = None
    active_node_id: Optional[NodeID] = None

    def get_active_branch(self) -> list[Message]:
        if not self.active_node_id or not self.root_id:
            return []
        path = []
        cur = self.active_node_id
        visited = set()
        while cur and cur in self.nodes and cur not in visited:
            visited.add(cur)
            node = self.nodes[cur]
            if node.message:
                path.append(node.message)
            cur = node.parent_id
        path.reverse()
        return path

    def get_all_branches(self) -> list[list[Message]]:
        if not self.root_id:
            return []
        branches = []
        self._dfs_branches(self.root_id, [], branches, set())
        return branches

    def _dfs_branches(self, node_id: NodeID, current: list, branches: list, visited: set):
        if node_id in visited:
            return
        visited.add(node_id)
        node = self.nodes.get(node_id)
        if not node:
            return
        branch = list(current)
        if node.message:
            branch.append(node.message)
        if node.children_ids:
            for cid in node.children_ids:
                self._dfs_branches(cid, branch, branches, visited.copy())
        else:
            branches.append(branch)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ConversationModel:
    source: str
    stable_id: str
    title: str
    source_url: str
    messages: list[Message]
    tree: Optional[ConversationTree] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": 2,
            "source": self.source,
            "chat_id": self.stable_id,
            "title": self.title,
            "source_url": self.source_url,
            "messages": [self._msg_to_dict(m) for m in self.messages],
            "tree": self._tree_to_legacy_dict() if self.tree else None,
        }

    def _msg_to_dict(self, m: Message) -> dict:
        d = {"role": m.role, "content": m.content}
        if m.timestamp:
            d["timestamp"] = m.timestamp
        if m.message_id:
            d["message_id"] = m.message_id
        if m.attachments:
            d["attachments"] = m.attachments
        return d

    def _tree_to_legacy_dict(self) -> dict:
        if not self.tree:
            return {}
        nodes = {}
        for nid, node in self.tree.nodes.items():
            nodes[nid] = {
                "id": node.id,
                "message": self._msg_to_dict(node.message) if node.message else None,
                "parent_id": node.parent_id,
                "children_ids": node.children_ids,
                "is_active_branch": node.is_active_branch,
            }
        return {
            "nodes": nodes,
            "root_id": self.tree.root_id,
            "active_node_id": self.tree.active_node_id,
        }
