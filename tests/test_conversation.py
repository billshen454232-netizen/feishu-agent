from src.conversation import ConversationStore


def test_appends_and_returns_history_for_chat():
    store = ConversationStore(max_messages=4)

    store.append("chat-1", "user", "hello")
    store.append("chat-1", "assistant", "hi")

    assert store.get("chat-1") == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_trims_oldest_messages_when_limit_exceeded():
    store = ConversationStore(max_messages=3)

    store.append("chat-1", "user", "one")
    store.append("chat-1", "assistant", "two")
    store.append("chat-1", "user", "three")
    store.append("chat-1", "assistant", "four")

    assert store.get("chat-1") == [
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
    ]


def test_clears_chat_history():
    store = ConversationStore(max_messages=4)
    store.append("chat-1", "user", "hello")

    store.clear("chat-1")

    assert store.get("chat-1") == []
