from typing import Optional

from conversation.models import ConversationModel


class BaseAdapter:
    name: str = "base"

    def list_chats(self) -> list[dict]:
        raise NotImplementedError

    def open_chat(self, chat: dict) -> bool:
        raise NotImplementedError

    def extract_chat(self, chat: dict) -> Optional[ConversationModel]:
        raise NotImplementedError

    def healthcheck(self) -> bool:
        raise NotImplementedError

    def _snapshot_sidebar_state(self, link_selector: str) -> dict:
        result = self.page.evaluate(f"""() => {{
            const links = [...document.querySelectorAll('{link_selector}')];
            const listitems = document.querySelectorAll('[role="listitem"]');
            return {{
                url: location.href,
                title: document.title,
                app_links: links.length,
                all_links: document.querySelectorAll('a').length,
                listitem_count: listitems.length,
                history_visible: links.length > 0,
                ready_state: document.readyState,
                timeOrigin: performance.timeOrigin,
                perf_now: performance.now(),
            }};
        }}""")
        return result

    def _log_sidebar_snapshot(self, phase: str, state: dict, **extra) -> None:
        tag = extra.pop("provider", "ADAPTER")
        extras = " ".join(f"{k}={v}" for k, v in extra.items())
        self.log(
            f"[{tag}] {phase}:"
            f" app_links={state['app_links']}"
            f" all_links={state['all_links']}"
            f" listitem={state['listitem_count']}"
            f" history_visible={str(state['history_visible']).lower()}"
            f" url={state['url']}"
            f" title={state['title']}"
            f" ready={state['ready_state']}"
            f" origin={state['timeOrigin']:.0f}"
            f" perf={state['perf_now']:.0f}"
            + (f" {extras}" if extras else "")
        )
