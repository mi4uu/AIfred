"""Triage engine tests — incremental, chunked classify, dedup, person weight."""

import json

from aifred.store.db import Store
from aifred.triage import TriageEngine, TriageItem


class FakeLLM:
    def __init__(self, mapping):
        self.mapping = mapping  # match substring -> importance
        self.batch_sizes = []

    def chat(self, messages, tools=None, temperature=0.0):
        user = messages[-1]["content"]
        lines = [l for l in user.splitlines() if l.strip()]
        self.batch_sizes.append(len(lines))
        out = []
        for i, line in enumerate(lines):
            imp = "low"
            for k, v in self.mapping.items():
                if k in line.lower():
                    imp = v
            out.append({"i": i, "importance": imp, "needs_action": imp == "high", "why": "x"})

        class R:
            content = json.dumps(out)

        return R()


class FakeContacts:
    def name_for_email(self, e):
        return "Kasia" if "kasia" in e else None

    def name_for(self, sender, push=""):
        return push or sender

    def is_known_person(self, q):
        return "kasia" in (q or "").lower()

    def owner_terms_for(self, sender, person_name=""):
        return set()

    def is_owner(self, sender):
        return False


def test_whatsapp_incremental_cursor():
    s = Store(":memory:")
    s.add_message("whatsapp", "fam", "w1", ts=1.0, body="kup mleko", sender="48500", sender_name="Kasia")
    eng = TriageEngine(s, FakeLLM({"mleko": "high"}), contacts=FakeContacts())
    items = eng.collect_whatsapp()
    assert len(items) == 1 and items[0].person == "Kasia"
    assert eng.collect_whatsapp() == []  # cursor advanced, nothing new


def test_prefilter_drops_noise_keeps_known():
    s = Store(":memory:")
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts())
    items = [
        TriageItem("mail", "1", "no-reply@x.com", None, 1, "promo", "buy now"),
        TriageItem("mail", "2", "kasia@x.com", "Kasia", 2, "hej", "zadzwoń"),
        TriageItem("whatsapp", "3", "48500", None, 3, "", ""),  # empty -> drop
    ]
    kept = eng.prefilter(items)
    assert [i.ext_id for i in kept] == ["2"]


def test_classify_batches_and_boosts_known_person():
    s = Store(":memory:")
    llm = FakeLLM({"raport": "low"})  # would be low...
    eng = TriageEngine(s, llm, contacts=FakeContacts())
    items = [TriageItem("mail", str(i), "x", None, i, f"raport {i}", "") for i in range(14)]
    items.append(TriageItem("mail", "k", "kasia@x", "Kasia", 99, "raport", ""))  # known -> boosted
    eng.classify(items)
    assert max(llm.batch_sizes) <= 6  # chunked (BATCH)
    known = [i for i in items if i.person == "Kasia"][0]
    assert known.importance == "medium"  # low boosted because known person


def test_aggregate_dedup_and_high():
    s = Store(":memory:")
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts())
    items = [
        TriageItem("mail", "1", "boss@x", None, 1, "deadline", "", importance="high"),
        TriageItem("whatsapp", "2", "48500", "Kasia", 2, "", "mleko", importance="medium"),
    ]
    r1 = eng.aggregate(items)
    assert r1["new"] == 2 and len(r1["high"]) == 1
    r2 = eng.aggregate(items)  # same refs -> deduped
    assert r2["new"] == 0
    assert len(s.list_attention("open")) == 2
    # high ordered first
    assert s.list_attention("open")[0]["kind"] == "high"
    s.close()


def test_run_empty_when_no_new():
    s = Store(":memory:")
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts())
    assert eng.run() == {"new": 0, "high": [], "review": 0, "notes": 0, "scanned": 0}
    s.close()


def test_group_not_directed_forced_low():
    s = Store(":memory:")

    class LLM:  # says high+directed... but engine must override for undirected group
        def chat(self, messages, tools=None, temperature=0.0):
            class R:
                content = '[{"i":0,"importance":"high","needs_action":true,"directed_at_me":false,"why":"x"}]'
            return R()

    eng = TriageEngine(s, LLM(), contacts=FakeContacts(), owner_aliases="Owner")
    items = [TriageItem("whatsapp", "g1", "111", "Anna", 1, "", "Olka, daj znać", is_group=True)]
    eng.classify(items)
    assert items[0].importance == "low"  # group + not directed -> low
    assert items[0].directed_at_me is False
    r = eng.aggregate(items)
    assert r["high"] == []  # nothing pushed
    s.close()


def test_derive_is_group():
    from aifred.whatsapp.worker import derive_is_group

    assert derive_is_group("48598765432", "48598765432") is False  # direct: chat==sender
    assert derive_is_group("120363000000002", "100000000000002") is True  # group prefix + sender differs
    assert derive_is_group("120363000000001", "120363000000001") is True  # 15-digit... not group by len
    assert derive_is_group("foo@g.us", "x") is True
    assert derive_is_group("48500111222", "48500111222") is False


def test_collect_whatsapp_rederives_group_flag():
    # stored bit wrong (0) but JIDs say group -> engine must re-derive (B3)
    s = Store(":memory:")
    s.add_message("whatsapp", "120363000000002", "w1", ts=1.0, body="Olka, daj znać",
                  sender="100000000000002", sender_name="Anna", is_group=False)
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts())
    items = eng.collect_whatsapp()
    assert items[0].is_group is True
    s.close()


def test_self_chat_note_kept_and_captured():
    s = Store(":memory:")
    # owner writes to himself (chat == sender == owner lid) -> KEEP as a note
    s.add_message("whatsapp", "100000000000001", "n1", ts=1000.0, body="Zosia 16-17.06 u mnie",
                  sender="100000000000001", sender_name="Sówka", is_group=True, from_me=True)
    # owner's own message in someone else's chat -> still skipped
    s.add_message("whatsapp", "31061", "n2", ts=1001.0, body="cześć", sender="100000000000001",
                  sender_name="Sówka", from_me=True)

    class FakeBrain:
        def __init__(self):
            self.appends = []

        def append(self, content, path="journal/inbox.md"):
            self.appends.append((path, content))

    brain = FakeBrain()
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts(), owner_lid="100000000000001", brain=brain)
    items = eng.collect_whatsapp()
    assert [i.ext_id for i in items] == ["n1"]      # only the self-note kept
    assert items[0].is_self_note is True
    n = eng.handle_self_notes(items)
    assert n == 1
    assert any("Zosia" in c for _, c in brain.appends)        # captured to brain.md
    assert any("Journal/inbox.md" in p for p, _ in brain.appends)
    assert eng.handle_self_notes(items) == 0                    # idempotent (dedup by ref)


def test_collect_skips_owner_own_messages():
    s = Store(":memory:")
    s.add_message("whatsapp", "31061", "w1", ts=1.0, body="cześć piękna", sender="100000000000001",
                  sender_name="Sówka", from_me=True)          # owner's own -> skip
    s.add_message("whatsapp", "31061", "w2", ts=2.0, body="kup mleko", sender="31061",
                  sender_name="Katarzyna")                     # real incoming -> keep
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts(), owner_lid="100000000000001")
    items = eng.collect_whatsapp()
    assert [i.ext_id for i in items] == ["w2"]


def test_collect_skips_owner_by_lid_without_flag():
    s = Store(":memory:")  # old row: from_me not set, but sender == owner lid
    s.add_message("whatsapp", "60155", "w1", ts=1.0, body="dziekuje", sender="100000000000001", sender_name="Sówka")
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts(), owner_lid="100000000000001")
    assert eng.collect_whatsapp() == []


def test_guard_otp_forced_low():
    s = Store(":memory:")
    llm = FakeLLM({"kod": "high"})  # model says high...
    eng = TriageEngine(s, llm, contacts=FakeContacts())
    items = [TriageItem("mail", "1", "info@account.netflix.com", None, 1, "Netflix: kod logowania", "")]
    eng.classify(items)
    assert items[0].importance == "low"  # OTP guard wins
    assert items[0].needs_action is False
    s.close()


def test_guard_self_mail_automated():
    s = Store(":memory:")
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts(), owner_email="me@x.com")
    items = [TriageItem("mail", "1", "me@x.com", "Me", 1, "BOTMARLEY daily report", "no errors")]
    eng.classify(items)
    assert items[0].importance == "low"  # self/automated, not boosted to medium
    s.close()


def test_thread_context_attached():
    s = Store(":memory:")
    s.add_message("whatsapp", "c1", "a", ts=1.0, body="kupisz mleko?", sender="x", sender_name="Kasia")
    s.add_message("whatsapp", "c1", "b", ts=2.0, body="i chleb", sender="x", sender_name="Kasia")
    eng = TriageEngine(s, FakeLLM({}), contacts=FakeContacts())
    items = eng.collect_whatsapp()
    assert "kupisz mleko" in items[-1].context  # last item sees prior thread line
    s.close()


def test_verify_push_downgrades_on_disagreement():
    s = Store(":memory:")

    class LLM:  # batch classify says high; single skeptical pass says not high
        def chat(self, messages, tools=None, temperature=0.0):
            sys = messages[0]["content"]
            class R:
                content = ('{"high":false,"why":"chit-chat"}' if "double-check" in sys
                           else '[{"i":0,"importance":"high","needs_action":true,"directed_at_me":true,"why":"x"}]')
            return R()

    eng = TriageEngine(s, LLM(), contacts=FakeContacts(), owner_aliases="Owner")
    items = [TriageItem("whatsapp", "d1", "48500", "Kasia", 1, "", "haha", directed_at_me=True, importance="high")]
    eng.verify_push(items)
    assert items[0].importance == "medium"  # downgraded -> not pushed
    assert eng.aggregate(items)["high"] == []
    s.close()


def test_disagreement_routes_to_review_queue():
    s = Store(":memory:")

    class LLM:
        def chat(self, messages, tools=None, temperature=0.0):
            sys = messages[0]["content"]
            class R:
                content = ('{"high":false,"why":"chit-chat"}' if "double-check" in sys
                           else '[{"i":0,"importance":"high","needs_action":true,"directed_at_me":true,"why":"x"}]')
            return R()

    eng = TriageEngine(s, LLM(), contacts=FakeContacts(), owner_aliases="Owner")
    items = [TriageItem("whatsapp", "r1", "48500", "Kasia", 1, "", "haha",
                        directed_at_me=True, importance="high")]
    eng.verify_push(items)
    assert items[0].review is True and items[0].suggest == "high"
    res = eng.aggregate(items)
    assert res["review"] == 1 and res["high"] == []  # queued, not pushed
    assert s.list_attention("review")  # in the review queue
    assert s.list_attention("open") == []  # not in the live feed
    s.close()


def test_verify_push_skips_forced_rule():
    s = Store(":memory:")
    s.add_rule("sender", "kasia", "vip")

    class LLM:
        def chat(self, messages, tools=None, temperature=0.0):
            class R:
                content = '{"high":false,"why":"x"}'  # skeptic would downgrade...
            return R()

    eng = TriageEngine(s, LLM(), contacts=FakeContacts(), owner_aliases="Owner")
    items = [TriageItem("whatsapp", "d2", "48500", "Kasia", 1, "", "hej", directed_at_me=True, importance="medium")]
    eng.apply_rules(items)  # vip -> high + forced
    assert items[0].forced is True
    eng.verify_push(items)
    assert items[0].importance == "high"  # forced rule not second-guessed
    s.close()


def test_owner_nickname_marks_directed():
    s = Store(":memory:")

    class LLM:  # model says low + not directed (missed the pet name)
        def chat(self, messages, tools=None, temperature=0.0):
            class R:
                content = '[{"i":0,"importance":"low","needs_action":false,"directed_at_me":false,"why":"x"}]'
            return R()

    class CT:  # Kasia calls the owner "kotek"
        def name_for(self, sender, push=""):
            return "Kasia"
        def is_known_person(self, q):
            return True
        def owner_terms_for(self, sender, person_name=""):
            return {"kotek", "kotkowi"} if person_name == "Kasia" or sender == "48500" else set()

    eng = TriageEngine(s, LLM(), contacts=CT(), owner_aliases="Owner")
    it = TriageItem("whatsapp", "g1", "48500", "Kasia", 1, "", "a odebrałby kotek paczkę", is_group=False)
    eng.classify([it])
    assert it.directed_at_me is True   # 'kotek' from Kasia == addressed to owner
    assert it.importance == "medium"   # bumped from low

    # same word from someone who doesn't use it -> not owner-directed
    other = TriageItem("whatsapp", "g2", "99999", None, 1, "", "kupiłem kotkowi zabawkę", is_group=False)
    eng.classify([other])
    assert other.directed_at_me is False


def test_group_directed_stays_high():
    s = Store(":memory:")

    class LLM:
        def chat(self, messages, tools=None, temperature=0.0):
            class R:
                content = '[{"i":0,"importance":"high","needs_action":true,"directed_at_me":true,"why":"asks you"}]'
            return R()

    eng = TriageEngine(s, LLM(), contacts=FakeContacts(), owner_aliases="Owner")
    items = [TriageItem("whatsapp", "g2", "111", "Anna", 1, "", "Owner odbierzesz?", is_group=True)]
    eng.classify(items)
    assert items[0].importance == "high"
    assert eng.aggregate(items)["high"]  # pushed (directed at owner)
    s.close()


def test_attention_resolves_number_to_name():
    from aifred.skills.attention import _resolve_identities, attention_list
    from aifred.store.db import Store

    class CT:
        def name_for(self, sender, push=""):
            return "❤️ Katarzyna (Kasia)" if sender == "48598765432" else sender

    assert _resolve_identities("Ktoś z numeru 48598765432 prosi", CT()) == "Ktoś z numeru ❤️ Katarzyna (Kasia) prosi"
    s = Store(":memory:")
    s.add_attention("triage:whatsapp", "high", "[whatsapp] 48598765432: odbierz paczkę", "wa:x", 1.0)
    out = attention_list(s, contacts=CT())
    assert "Kasia" in out["items"][0]["text"] and "48598765432" not in out["items"][0]["text"]
    s.close()


def test_guard_phishing_payment_forced_low():
    s = Store(":memory:")
    eng = TriageEngine(s, FakeLLM({"payment": "high"}), contacts=FakeContacts())
    items = [TriageItem("mail", "p1", "order@zen.com", None, 1,
                        "New payment EUR100.50 requiring verification", "verify your account")]
    eng.classify(items)
    assert items[0].importance == "low"          # phishing from stranger -> low
    assert items[0].directed_at_me is False
    s.close()


def test_rejudge_open_demotes_phishing_and_reresolves_name():
    s = Store(":memory:")
    s.add_message("whatsapp", "g", "m1", ts=1.0, body="strategia?", sender="117", sender_name="Tomek")

    class CT:
        def name_for(self, sender, push=""):
            return "Tomasz Nowak" if sender == "117" else (push or sender)
        def is_known_person(self, q): return False
        def owner_terms_for(self, s, p=""): return set()
        def is_owner(self, s): return False

    eng = TriageEngine(s, FakeLLM({}), contacts=CT())
    s.add_attention("triage:whatsapp", "high", "[whatsapp (grupa)] Tomek: strategia?", "whatsapp:m1", 1.0)
    s.add_attention("triage:mail", "high", "order@zen.com: New payment requiring verification", "mail:x", 1.0)
    n = eng.rejudge_open()
    items = {i["content"]: i["kind"] for i in s.list_attention("open")}
    # Tomek -> Tomasz Nowak (identity re-resolved)
    assert any("Tomasz Nowak" in c for c in items)
    assert not any("Tomek" in c for c in items)
    # phishing mail demoted to low
    assert items["order@zen.com: New payment requiring verification"] == "low"
    assert n >= 2
    s.close()


def test_group_context_marks_owner_and_uses_more_lines():
    s = Store(":memory:")
    chat = "120363000000000001"  # group
    s.add_message("whatsapp", chat, "a", ts=1.0, body="kto odbiera Zosię?", sender="OWNER", sender_name="Sówka", is_group=True)
    s.add_message("whatsapp", chat, "b", ts=2.0, body="ja nie mogę", sender="111", sender_name="Ewa", is_group=True)
    s.add_message("whatsapp", chat, "c", ts=3.0, body="to ty weź", sender="111", sender_name="Ewa", is_group=True)

    class CT:
        def is_owner(self, sender): return sender == "OWNER"
        def name_for(self, sender, push=""): return push or sender
        def owner_terms_for(self, s, p=""): return set()
        def is_known_person(self, q): return False

    eng = TriageEngine(s, FakeLLM({}), contacts=CT())
    ctx = eng._thread_context("whatsapp", chat, before_ts=4.0, is_group=True)
    assert "JA (właściciel): kto odbiera Zosię?" in ctx   # owner line marked -> addressee inferable
    assert "Ewa: to ty weź" in ctx
    s.close()
