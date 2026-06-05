"""Daily-note composer — gathers the day's signals, fills the fixed template."""

from aifred.skills.daily_note import _norm_ts, _weekday_pl, compose_daily_note, gather_day
from aifred.store.db import Store


class FakeContacts:
    def is_owner(self, sender):
        return sender == "100000000000001"

    def name_for(self, sender, push=""):
        return {"31061": "❤️ Katarzyna (Kasia)"}.get(sender, push or sender)


class FakeBrain:
    def __init__(self):
        self.written = {}

    def write(self, path, content):
        self.written[path] = content
        return "ok"


class FakeLLM:
    def __init__(self):
        self.seen = ""

    def chat(self, messages, tools=None, temperature=0.0):
        self.seen = messages[-1]["content"]

        class R:
            content = "# 📓 2026-06-03 (wtorek)\n\n## 💬 Komunikacja\n- Kasia: prosiła o mleko"

        return R()


DAY = "2026-06-03"


def _seed(s):
    from datetime import datetime, timezone

    day_ms = datetime.strptime(DAY, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000  # WA stores ms
    s.add_message("whatsapp", "31061", "w1", ts=day_ms + 3600_000, body="kup mleko", sender="31061", sender_name="Katarzyna")
    s.add_message("whatsapp", "31061", "w2", ts=day_ms + 3700_000, body="cześć", sender="100000000000001",
                  sender_name="Sówka", from_me=True)  # owner's own -> excluded
    s.add_message("whatsapp", "31061", "w3", ts=1700_000_000_000, body="stare", sender="31061", sender_name="Katarzyna")  # other day


def test_norm_ts_and_weekday():
    assert _norm_ts(1780531200_000) == 1780531200.0  # ms -> s
    assert _norm_ts(1780531200.0) == 1780531200.0     # already s
    assert _weekday_pl(DAY) == "środa"


def test_gather_day_groups_and_excludes_owner():
    s = Store(":memory:")
    _seed(s)
    sig = gather_day(s, FakeContacts(), DAY)
    # only w1 (right day, not owner) -> grouped under resolved name
    assert "❤️ Katarzyna (Kasia)" in sig["communication"]
    assert sig["communication"]["❤️ Katarzyna (Kasia)"] == ["kup mleko"]
    s.close()


def test_compose_writes_structured_note():
    s = Store(":memory:")
    _seed(s)
    brain, llm = FakeBrain(), FakeLLM()
    res = compose_daily_note(brain, llm, s, FakeContacts(), DAY)
    assert res["written"] is True
    assert f"Journal/{DAY}.md" in brain.written
    assert "Komunikacja" in brain.written[f"Journal/{DAY}.md"]
    assert "kup mleko" in llm.seen  # signal reached the model
    s.close()


def test_compose_no_signals_skips():
    s = Store(":memory:")
    res = compose_daily_note(FakeBrain(), FakeLLM(), s, FakeContacts(), DAY)
    assert res["written"] is False
    s.close()
