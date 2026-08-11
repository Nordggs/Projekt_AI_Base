from conversation.models import AttachmentNode, ConversationModel, ValidationResult


def validate_structure(model: ConversationModel) -> ValidationResult:
    errors = []
    warnings = []
    if not model.title:
        warnings.append("title is empty")
    if not model.source_url:
        warnings.append("source_url is empty")
    if len(model.messages) < 2:
        errors.append(f"too few messages: {len(model.messages)} (need >= 2)")
        return ValidationResult(ok=False, errors=errors, warnings=warnings)
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def validate_roles(model: ConversationModel) -> ValidationResult:
    errors = []
    has_user = any(m.role == "user" for m in model.messages)
    has_assistant = any(m.role == "assistant" for m in model.messages)
    if not has_user:
        errors.append("no user messages")
    if not has_assistant:
        errors.append("no assistant messages")
    valid_roles = {"user", "assistant", "system", "tool"}
    bad = [m.role for m in model.messages if m.role not in valid_roles]
    if bad:
        errors.append(f"invalid roles found: {set(bad)}")
    return ValidationResult(ok=not errors, errors=errors)


def validate_tree(model: ConversationModel) -> ValidationResult:
    errors = []
    warnings = []
    tree = model.tree
    if not tree:
        return ValidationResult(ok=True)
    if not tree.root_id:
        errors.append("tree has no root_id")
        return ValidationResult(ok=False, errors=errors)
    if tree.root_id not in tree.nodes:
        errors.append(f"root_id {tree.root_id} not in nodes")
        return ValidationResult(ok=False, errors=errors)
    visited = set()
    stack = [tree.root_id]
    while stack:
        nid = stack.pop()
        if nid in visited:
            errors.append(f"cycle detected at node {nid}")
            return ValidationResult(ok=False, errors=errors)
        visited.add(nid)
        node = tree.nodes.get(nid)
        if not node:
            errors.append(f"node {nid} references missing parent/child")
            continue
        for cid in node.children_ids:
            stack.append(cid)
    if tree.active_node_id and tree.active_node_id not in visited:
        warnings.append(f"active_node_id {tree.active_node_id} not reachable from root")
    orphan_count = len(tree.nodes) - len(visited)
    if orphan_count:
        warnings.append(f"{orphan_count} orphan nodes (not reachable from root)")
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def validate_attachments(model: ConversationModel) -> ValidationResult:
    warnings = []
    for idx, msg in enumerate(model.messages):
        for aidx, att in enumerate(msg.attachments):
            if not isinstance(att, AttachmentNode):
                warnings.append(f"msg[{idx}].attachments[{aidx}] is not AttachmentNode")
                continue
            if att.type and att.type not in ("image", "file", "audio", "video", "runtime_asset", "generated_image"):
                warnings.append(f"msg[{idx}].attachments[{aidx}]: unknown type '{att.type}'")
    return ValidationResult(ok=True, warnings=warnings)


def validate_messages(model: ConversationModel) -> ValidationResult:
    errors = []
    warnings = []
    for idx, msg in enumerate(model.messages):
        if not msg.content.strip() and not msg.attachments:
            warnings.append(f"msg[{idx}]: empty content and no attachments")
        if len(msg.content) > 100_000:
            warnings.append(f"msg[{idx}]: content unusually long ({len(msg.content)} chars)")
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def validate_all(model: ConversationModel) -> ValidationResult:
    results = [
        validate_structure(model),
        validate_roles(model),
        validate_tree(model),
        validate_attachments(model),
        validate_messages(model),
    ]
    all_errors = []
    all_warnings = []
    for r in results:
        all_errors.extend(r.errors)
        all_warnings.extend(r.warnings)
    return ValidationResult(
        ok=not all_errors,
        errors=all_errors,
        warnings=all_warnings,
    )
