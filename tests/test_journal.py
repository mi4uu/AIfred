"""T11 journal tests (V6 append-only, V12 scoped retrieval, distill pattern)."""

from aifred.skills.journal import (
    build_journal_tools,
    distill_to_journal,
    journal_add,
    journal_path,
    journal_recall,
)


class FakeBrain:
    def __init__(self, ctx="internal note"):
        self.appended = []  # (content, path)
        self.retrieved = None
        self.ctx_scope = None
        self._ctx = ctx

    def append(self, content, path="journal/inbox.md"):
        self.appended.append((content, path))

    def retrieve(self, query, top_k=5, scope=None):
        self.retrieved = (query, scope)
        return ["chunk about " + query]

    def context(self, query, budget_tokens=1500, scope=None):
        self.ctx_scope = scope
        return self._ctx


class FakeLLM:
    def __init__(self, content):
        self.content = content

    def chat(self, messages, tools=None, temperature=0.0):
        class R:
            pass

        r = R()
        r.content = self.content
        return r


def test_journal_add_targets_day_file():
    b = FakeBrain()
    journal_add(b, "first", day="2026-06-03", time_str="08:00")
    journal_add(b, "second", day="2026-06-03", time_str="09:00")
    assert len(b.appended) == 2  # V6 append-only
    assert b.appended[0][1] == journal_path("2026-06-03") == "Journal/2026-06-03.md"
    assert "first" in b.appended[0][0] and "second" in b.appended[1][0]


def test_recall_scoped_to_journal():
    b = FakeBrain()
    journal_recall(b, "dentist")
    assert b.retrieved == ("dentist", ["Journal"])  # V12 scoped retrieval


def test_distill_reads_internal_writes_journal():
    b = FakeBrain(ctx="raw internal reasoning")
    out = distill_to_journal(b, FakeLLM("- decided X\n- remember Y"), "project", day="2026-06-03")
    assert b.ctx_scope == ["hermes"]  # read internal catalog scoped (V12)
    assert out["appended"] == 1
    assert b.appended[0][1] == "Journal/2026-06-03.md"  # conclusions into journal (V2)
    assert "decided X" in b.appended[0][0]


def test_distill_empty_context_noop():
    b = FakeBrain(ctx="")
    out = distill_to_journal(b, FakeLLM("x"), "nothing")
    assert out["appended"] == 0 and b.appended == []


def test_build_tools_includes_distill_with_llm():
    assert {t.name for t in build_journal_tools(FakeBrain())} == {"journal_add", "journal_recall"}
    with_llm = {t.name for t in build_journal_tools(FakeBrain(), FakeLLM("x"))}
    assert with_llm == {"journal_add", "journal_recall", "journal_distill"}
