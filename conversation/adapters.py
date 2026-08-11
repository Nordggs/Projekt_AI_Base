from conversation.models import ConversationModel
from conversation.irbuilder import IRBuilder, Provider


def from_next_data(page) -> ConversationModel | None:
    try:
        raw = page.evaluate("""() => {
            try {
                const nd = window.__NEXT_DATA__;
                if (!nd || !nd.props || !nd.props.pageProps) return null;
                const conv = nd.props.pageProps.conversation;
                if (!conv || !conv.mapping) return null;
                return JSON.parse(JSON.stringify(conv));
            } catch(e) { return null; }
        }""")
        if not raw:
            return None
        url = page.evaluate("location.href")
        model = IRBuilder.build(Provider.CHATGPT_API, raw, url=url)
        model.metadata["source"] = "next_data"
        return model
    except Exception:
        return None
