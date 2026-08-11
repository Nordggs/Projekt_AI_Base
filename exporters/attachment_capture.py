import hashlib
import time
from dataclasses import dataclass, field


@dataclass
class CapturedAsset:
    url: str
    content_type: str
    body: bytes
    timestamp: float
    filename: str = ""


class AttachmentCDPCapture:
    """Captures image/media assets from CDP network responses.
    
    Hooks page.on("response") and filters for image/*, oaiusercontent, cdn.oaistatic.
    Assets are stored in-memory until bound to messages via temporal window.
    """

    def __init__(self, page, log_func=None):
        self.page = page
        self._log = log_func or (lambda *_: None)
        self.assets: list[CapturedAsset] = []
        self._enabled = False

    def start(self):
        self._enabled = True
        self.page.on("response", self._on_response)

    def stop(self):
        self._enabled = False

    @property
    def captured_assets(self):
        return list(self.assets)

    def _is_asset(self, url, content_type):
        ct = (content_type or "").lower()
        url_lower = url.lower()
        return (
            ct.startswith("image/")
            or "oaiusercontent.com" in url_lower
            or "cdn.oaistatic.com" in url_lower
        )

    def _on_response(self, response):
        if not self._enabled:
            return
        try:
            url = response.url
            ctype = response.headers.get("content-type", "")
            if not self._is_asset(url, ctype):
                return
            body = response.body()
            ts = time.time()
            name = url.rstrip("/").split("/")[-1][:80] or hashlib.md5(url.encode()).hexdigest()[:12]
            self.assets.append(CapturedAsset(
                url=url,
                content_type=ctype,
                body=body,
                timestamp=ts,
                filename=name,
            ))
            if url.startswith("blob:"):
                self._log(f"[CDP] blob asset (log only): {url[:80]}")
            self._log(f"[CDP] captured {ctype} {len(body)} bytes from {url[:80]}")
        except Exception:
            pass
