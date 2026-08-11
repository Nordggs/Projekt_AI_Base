from dataclasses import dataclass, field
from typing import Optional

NodeID = str


@dataclass
class AttachmentNode:
    type: str = "file"                       # image | file | audio | video | runtime_asset | generated_image
    mime: Optional[str] = None
    name: Optional[str] = None
    meta: dict = field(default_factory=dict)
    local: Optional[dict] = None             # {"relative_path": "media/..."} — set by writer

    pointer: Optional[str] = None            # file_xxx / sediment:// URL
    api_id: Optional[str] = None
    source: str = "api"                      # api | cdp | cdp_temporal | runtime | partial
    is_partial: bool = True
    confidence: float = 1.0                  # 1.0 = api truth, 0.8 = CDP, 0.6 = runtime, 0.0 = partial
    timestamp: Optional[float] = None

    bytes: Optional[bytes] = None            # payload (not serialized to JSON)
    url: Optional[str] = None                # original URL from CDP/runtime

    # dict-compatible access for gradual migration (remove in v7)
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            return self.__dict__[key]

    def __setitem__(self, key, value):
        try:
            setattr(self, key, value)
        except AttributeError:
            self.__dict__[key] = value

    def __delitem__(self, key):
        try:
            delattr(self, key)
        except AttributeError:
            try:
                del self.__dict__[key]
            except KeyError:
                raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except (AttributeError, KeyError):
            return default

    def pop(self, key, *args):
        try:
            val = self[key]
            del self[key]
            return val
        except (AttributeError, KeyError):
            if args:
                return args[0]
            raise KeyError(key)


@dataclass
class Provenance:
    api_ok: bool = False
    cdp_ok: bool = False
    runtime_ok: bool = False


@dataclass
class Message:
    role: str
    content: str
    timestamp: Optional[float] = None
    message_id: Optional[str] = None
    attachments: list[AttachmentNode] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    provenance: Optional[Provenance] = None

    def is_renderable(self) -> bool:
        return bool(self.content.strip() or self.attachments)


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
        # Returns messages intended for rendering (filtered by Message.is_renderable).
        # Empty/system-only/tool-call-only nodes are omitted.
        # For the complete provider tree use ConversationTree.nodes.
        if not self.active_node_id or not self.root_id:
            return []
        path = []
        cur = self.active_node_id
        visited = set()
        while cur and cur in self.nodes and cur not in visited:
            visited.add(cur)
            node = self.nodes[cur]
            msg = node.message
            if msg and msg.is_renderable():
                path.append(msg)
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
        msg = node.message
        if msg and msg.is_renderable():
            branch.append(msg)
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
            d["attachments"] = [self._att_to_dict(a) for a in m.attachments]
        if m.provenance:
            d["provenance"] = {"api_ok": m.provenance.api_ok,
                               "cdp_ok": m.provenance.cdp_ok,
                               "runtime_ok": m.provenance.runtime_ok}
        return d

    @staticmethod
    def _att_to_dict(a: AttachmentNode) -> dict:
        d = {"type": a.type, "mime": a.mime, "name": a.name, "meta": a.meta,
             "source": a.source, "is_partial": a.is_partial,
             "confidence": a.confidence, "timestamp": a.timestamp}
        if a.pointer:
            d["pointer"] = a.pointer
        if a.api_id:
            d["api_id"] = a.api_id
        if a.local:
            d["local"] = a.local
        if a.url:
            d["url"] = a.url
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
