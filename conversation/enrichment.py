"""Enricher — pure 3-layer merge for attachment enrichment."""

from __future__ import annotations

import base64
import copy
import hashlib
from dataclasses import dataclass, field
from typing import Callable

from conversation.models import AttachmentNode, ConversationModel, Message
from exporters.attachment_capture import CapturedAsset


@dataclass
class TraceEntry:
    src: str
    t: float
    kind: str = "added"


@dataclass
class BlobEntry:
    src: str
    b64: str
    ctype: str
    t: float


@dataclass
class RuntimeData:
    trace: list[TraceEntry] = field(default_factory=list)
    blobs: list[BlobEntry] = field(default_factory=list)


@dataclass
class AnchorData:
    perf_zero: float = 0.0
    epoch_zero: float = 0.0


@dataclass
class EnrichmentStats:
    cdp_strict: int = 0
    cdp_temporal: int = 0
    runtime: int = 0
    partial: int = 0

    @property
    def total(self) -> int:
        return self.cdp_strict + self.cdp_temporal + self.runtime + self.partial


class Enricher:
    """Pure: deepcopy model → 3-layer merge → (new_model, stats)."""

    @staticmethod
    def enrich(
        model: ConversationModel,
        cdp_assets: list[CapturedAsset],
        runtime: RuntimeData,
        anchor: AnchorData,
        log_func: Callable | None = None,
    ) -> tuple[ConversationModel, EnrichmentStats]:
        model = copy.deepcopy(model)
        stats = EnrichmentStats()

        first_ts = model.messages[0].timestamp if model.messages else None
        if first_ts is None:
            perf_epoch_offset = 0.0
        else:
            perf_epoch_offset = first_ts - anchor.perf_zero

        def to_epoch(perf_ms: float) -> float:
            return perf_epoch_offset + perf_ms / 1000.0

        img_trace = list(runtime.trace)
        blob_queue = sorted(runtime.blobs, key=lambda b: b.t)
        cdp_snapshot = list(cdp_assets)

        for i, msg in enumerate(model.messages):
            t0 = msg.timestamp

            cdp_in_window = []
            imgs_in_window = []
            window_blobs = []

            if t0 is not None:
                if i + 1 < len(model.messages):
                    t1 = model.messages[i + 1].timestamp
                else:
                    t1 = None

                if t1 is not None:
                    if t0 == t1 and t0 > 0:
                        t1 = t0 + 0.001

                    j = 0
                    while j < len(cdp_snapshot):
                        if t0 <= cdp_snapshot[j].timestamp < t1:
                            cdp_in_window.append(cdp_snapshot.pop(j))
                        else:
                            j += 1

                    imgs_in_window = [e for e in img_trace if t0 <= to_epoch(e.t) < t1]
                    window_blobs = [b for b in blob_queue if t0 <= to_epoch(b.t) < t1]

                else:
                    j = 0
                    while j < len(cdp_snapshot):
                        if t0 <= cdp_snapshot[j].timestamp:
                            cdp_in_window.append(cdp_snapshot.pop(j))
                        else:
                            j += 1

                    imgs_in_window = [e for e in img_trace if t0 <= to_epoch(e.t)]
                    window_blobs = [b for b in blob_queue if t0 <= to_epoch(b.t)]

            for att in msg.attachments:
                att["is_partial"] = True
                att["source"] = "api"

                url = (att.get("meta") or {}).get("url", "")
                cdp_match = next((a for a in cdp_in_window if a.url == url), None)
                if not cdp_match and url:
                    base = url.split("?")[0]
                    cdp_match = next((a for a in cdp_in_window if a.url.split("?")[0] == base), None)
                if cdp_match:
                    cdp_in_window.remove(cdp_match)
                    att["_captured"] = cdp_match
                    att["is_partial"] = False
                    att["source"] = "cdp"
                    stats.cdp_strict += 1
                    continue

                if cdp_in_window:
                    capt = cdp_in_window.pop(0)
                    att["_captured"] = capt
                    att["is_partial"] = False
                    att["source"] = "cdp_temporal"
                    stats.cdp_temporal += 1
                    continue

                if window_blobs:
                    b = window_blobs.pop(0)
                    blob_queue.remove(b)
                    att["_captured"] = CapturedAsset(
                        url=b.src,
                        content_type=b.ctype,
                        body=base64.b64decode(b.b64),
                        timestamp=to_epoch(b.t),
                        filename=f"runtime_{hashlib.md5(b.src.encode()).hexdigest()[:8]}",
                    )
                    att["is_partial"] = False
                    att["source"] = "runtime"
                    stats.runtime += 1
                    continue

                stats.partial += 1

            unbound_imgs = {b.src for b in window_blobs}
            for e in imgs_in_window:
                if e.src not in unbound_imgs:
                    continue
                b = next((x for x in blob_queue if x.src == e.src), None)
                if not b:
                    continue
                blob_queue.remove(b)
                capt = CapturedAsset(
                    url=b.src,
                    content_type=b.ctype,
                    body=base64.b64decode(b.b64),
                    timestamp=to_epoch(b.t),
                    filename=f"runtime_{hashlib.md5(b.src.encode()).hexdigest()[:8]}",
                )
                msg.attachments.append(AttachmentNode(
                    type="runtime_asset",
                    mime=b.ctype,
                    name=f"runtime_{len(msg.attachments)}",
                    meta={"origin": "dom_runtime", "confidence": "high", "capture_t": b.t},
                    is_partial=False,
                    source="runtime",
                    confidence=0.6,
                    timestamp=to_epoch(b.t),
                ))
                msg.attachments[-1]["_captured"] = capt
                stats.runtime += 1

        if log_func:
            log_func(
                f"[Enricher] cdp_strict={stats.cdp_strict} "
                f"cdp_temporal={stats.cdp_temporal} "
                f"runtime={stats.runtime} "
                f"partial={stats.partial}"
            )

        return model, stats
