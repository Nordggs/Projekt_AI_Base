import json
import time

QWEN_SELECTORS = [
    ".message-item", '[class*="message-item"]',
    ".chat-message", '[class*="chat-message"]',
    '[class*="conversation-item"]',
    "[data-role]",
    ".qwen-message", '[class*="qwen-message"]',
    ".user-message", ".assistant-message",
    '[class*="user-message"]', '[class*="assistant-message"]',
    ".msg-item", '[class*="msg-item"]',
]


class CdpSnapshotExtractor:
    MAX_STEPS = 50
    MAX_STABLE = 5

    def __init__(self, page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)

    @staticmethod
    def _resolve(strings, idx):
        if not isinstance(idx, int):
            return ""
        if 0 <= idx < len(strings):
            v = strings[idx]
            if isinstance(v, str) and v not in ("undefined", "null"):
                return v
        return ""

    def capture(self):
        return self.cdp.send("DOMSnapshot.captureSnapshot", {
            "computedStyles": [],
            "includeDOMRects": False,
            "includePaintOrder": False,
        })

    def scroll_down(self):
        try:
            self.page.evaluate("window.scrollBy(0, 1000)")
        except Exception:
            pass

    def extract_messages(self, snapshot, offset=0):
        if not snapshot or "documents" not in snapshot:
            return []
        strings = snapshot.get("strings", [])
        if not isinstance(strings, list) or not strings:
            return []
        doc = snapshot["documents"][0]
        n = doc["nodes"]
        parent_idx = n.get("parentIndex", [])
        node_name = n.get("nodeName", [])
        node_value = n.get("nodeValue", [])
        node_type = n.get("nodeType", [])
        attrs = n.get("attributes", [])
        children = [[] for _ in range(len(parent_idx))]
        for i, p in enumerate(parent_idx):
            if 0 <= p < len(children):
                children[p].append(i)
        self._visited = set()
        messages = []
        self._walk_tree(0, children, node_name, node_value, node_type, attrs, strings, messages, 0)
        result = []
        for role, content, pos in messages:
            result.append({
                "role": role,
                "content": content,
                "dom_position": pos + offset,
            })
        return result

    def _walk_tree(self, idx, children, node_name, node_value, node_type, attrs, strings, out, depth):
        if idx in self._visited:
            return
        self._visited.add(idx)

        ni = node_name[idx] if idx < len(node_name) else None
        tag = self._resolve(strings, ni).lower()
        vi = node_value[idx] if idx < len(node_value) else None
        val = self._resolve(strings, vi)

        # nodeType guard: only 1 (ELEMENT_NODE) passes through
        nt = 0
        if isinstance(node_type, list) and idx < len(node_type):
            nt_v = node_type[idx]
            if isinstance(nt_v, int):
                nt = nt_v
        if nt != 1:
            child_nodes = children[idx] if isinstance(children, list) and idx < len(children) else []
            for c in child_nodes:
                self._walk_tree(c, children, node_name, node_value, node_type, attrs, strings, out, depth + 1)
            return

        attr_list = attrs[idx] if idx < len(attrs) else []
        cls = ""
        data_role = ""
        for j in range(0, len(attr_list), 3):
            if j + 2 <= len(attr_list):
                a_name = self._resolve(strings, attr_list[j])
                a_val = self._resolve(strings, attr_list[j + 1])
                if a_name == "class":
                    cls = a_val
                elif a_name == "data-role":
                    data_role = a_val
        role = "assistant"
        if data_role == "user":
            role = "user"
        elif "user" in cls.lower():
            role = "user"
        is_container = False
        if tag in ("div", "section", "li", "article", "span"):
            for sel in QWEN_SELECTORS:
                parts = sel.strip(". []").split("*=")
                if len(parts) == 2 and parts[1].strip('"') in cls:
                    is_container = True
                    break
        if data_role:
            is_container = True
        text_parts = []
        if is_container:
            self._collect_text(idx, children, node_name, node_value, attrs, strings, text_parts)
            if text_parts:
                text = "\n".join(text_parts).strip()
                if len(text) >= 5:
                    out.append((role, text, idx))
        child_nodes = children[idx] if isinstance(children, list) and idx < len(children) else []
        for c in child_nodes:
            self._walk_tree(c, children, node_name, node_value, node_type, attrs, strings, out, depth + 1)

    def _collect_text(self, idx, children, node_name, node_value, attrs, strings, out):
        vi = node_value[idx] if idx < len(node_value) else None
        v = self._resolve(strings, vi)
        if v:
            v = v.strip()
            if v:
                out.append(v)
        child_nodes = children[idx] if isinstance(children, list) and idx < len(children) else []
        for c in child_nodes:
            self._collect_text(c, children, node_name, node_value, attrs, strings, out)

    def capture_conversation(self):
        all_msgs = []
        stable = 0
        offset = 0

        snap = self.capture()
        msgs = self.extract_messages(snap, offset=offset)
        offset += len(msgs)
        all_msgs.extend(msgs)

        for _ in range(self.MAX_STEPS):
            self.scroll_down()
            time.sleep(1.5)

            snap = self.capture()
            msgs = self.extract_messages(snap, offset=offset)
            offset += len(msgs)

            before = len(all_msgs)
            all_msgs.extend(msgs)

            if len(all_msgs) == before:
                stable += 1
                if stable >= self.MAX_STABLE:
                    break
            else:
                stable = 0

        return self._dedupe(all_msgs)

    def _dedupe(self, messages):
        seen = set()
        out = []
        for m in messages:
            key = (m["role"], m["content"], m.get("dom_position", 0))
            if key in seen:
                continue
            seen.add(key)
            out.append(m)
        for m in out:
            del m["dom_position"]
        return out

    def close(self):
        try:
            self.cdp.detach()
        except Exception:
            pass
