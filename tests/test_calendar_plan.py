"""T13 calendar planning tests (V3 source, V12 retrieval, V7 read-only proposer)."""

from aifred.skills.calendar_plan import build_calendar_plan_tools, propose_events


class FakeBrain:
    def __init__(self, chunks):
        self.chunks = chunks
        self.q = None

    def retrieve(self, query, top_k=5):
        self.q = query
        return self.chunks


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.sent = None

    def chat(self, messages, tools=None, temperature=0.0):
        self.sent = messages

        class R:
            pass

        r = R()
        r.content = self.content
        return r


def test_propose_uses_retrieval_and_stamps_source():
    b = FakeBrain(["dentist next tuesday 10am"])
    llm = FakeLLM('{"proposals":[{"summary":"dentist","start":"2026-06-09T10:00:00Z","end":"2026-06-09T11:00:00Z"}]}')
    out = propose_events(b, llm, "appointments this week")
    assert b.q == "appointments this week"  # V12 retrieval
    p = out["proposals"][0]
    assert p["summary"] == "dentist"
    assert "brain.md" in p["source"]  # V3 source cite


def test_no_chunks_no_llm():
    b = FakeBrain([])
    llm = FakeLLM("{}")
    out = propose_events(b, llm, "x")
    assert out == {"proposals": []}
    assert llm.sent is None


def test_proposer_is_read_only():
    tools = build_calendar_plan_tools(FakeBrain([]), FakeLLM("{}"))
    assert tools[0].name == "calendar_propose"
    assert tools[0].side_effecting is False  # V7: writing handled by calendar_create
