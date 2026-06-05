"""WhatsApp query tool tests — agent reads ingested messages, not brain.md."""

from aifred.skills.whatsapp_query import build_whatsapp_tools, whatsapp_chats, whatsapp_recent
from aifred.store.db import Store
from aifred.whatsapp.ingest import WAMessage
from aifred.whatsapp.worker import ingest_history


def _store_with_msgs():
    s = Store(":memory:")
    s.add_message("whatsapp", "fam", "1", ts=1.0, body="kup mleko", sender="Kasia")
    s.add_message("whatsapp", "fam", "2", ts=2.0, body="i chleb", sender="Kasia")
    s.add_message("whatsapp", "work", "3", ts=3.0, body="spotkanie 10", sender="boss")
    return s


def test_recent_returns_store_messages():
    s = _store_with_msgs()
    out = whatsapp_recent(s, limit=10)
    assert out["count"] == 3
    assert any("mleko" in m["text"] for m in out["messages"])
    s.close()


def test_recent_query_filter():
    s = _store_with_msgs()
    out = whatsapp_recent(s, limit=10, query="mleko")
    assert [m["text"] for m in out["messages"]] == ["kup mleko"]
    s.close()


def test_recent_empty_says_so():
    s = Store(":memory:")
    out = whatsapp_recent(s, query="nic")
    assert out["messages"] == [] and "no matching" in out["note"]
    s.close()


def test_chats_summary():
    s = _store_with_msgs()
    chats = {c["chat"]: c["messages"] for c in whatsapp_chats(s)["chats"]}
    assert chats == {"fam": 2, "work": 1}
    s.close()


def test_tools_tagged_whatsapp():
    s = Store(":memory:")
    tools = {t.name: t for t in build_whatsapp_tools(s)}
    assert set(tools) == {"whatsapp_recent", "whatsapp_chats"}
    assert all(t.tags == ("whatsapp",) for t in tools.values())
    s.close()


# ---- history sync backfill ----

class _Key:
    ID = "H1"
    remoteJID = "fam@g.us"
    participant = "Kasia"


class _Inner:
    conversation = "stara wiadomość"


class _WebInfo:
    key = _Key
    message = _Inner
    messageTimestamp = 100


class _Conv:
    messages = [type("HMsg", (), {"message": _WebInfo})()]


class _Data:
    conversations = [_Conv()]


class _HistEv:
    Data = _Data


def test_ingest_history_backfills():
    s = Store(":memory:")
    n = ingest_history(s, _HistEv())
    assert n == 1
    assert whatsapp_recent(s)["messages"][0]["text"] == "stara wiadomość"
    s.close()
