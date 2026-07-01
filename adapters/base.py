from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChatRecord:
    id: str
    title: str
    messages: list[dict]
    source: str
    url: str
    schema_version: str = "1.0"
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "chat_id": self.id,
            "title": self.title,
            "source_url": self.url,
            "messages": self.messages,
        }


class BaseAdapter:
    name: str = "base"

    def list_chats(self) -> list[dict]:
        raise NotImplementedError

    def open_chat(self, chat: dict) -> bool:
        raise NotImplementedError

    def extract_chat(self, chat: dict) -> Optional[ChatRecord]:
        raise NotImplementedError

    def healthcheck(self) -> bool:
        raise NotImplementedError
