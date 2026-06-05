"""Identity layer tests — brain.md ludzie/ parsing + PushName JID linking."""

from aifred.contacts import Contacts, parse_person
from aifred.store.db import Store

GUSIA = """---
title: ❤️ Katarzyna (Kasia)
tags: [ludzie, rodzina, dziewczyna]
---
# ❤️ Katarzyna "Kasia"
## Podstawowe
- **Rola:** Dziewczyna Ownera
## Kontakt
- Email: katarzyna@example.com
"""


def test_parse_person_aliases_role_email():
    p = parse_person("kasia", GUSIA)
    assert "kasia" in p.aliases and "katarzyna" in p.aliases
    assert "ownera" not in p.aliases  # role word excluded
    assert p.role.startswith("Dziewczyna")
    assert "katarzyna@example.com" in p.emails


class FakeBrain:
    def list_notes(self, folder=None):
        return '{"notes": ["ludzie/kasia.md", "ludzie/Index.md"]}'

    def read(self, path):
        return GUSIA


def test_contacts_links_jid_via_pushname():
    store = Store(":memory:")
    # message from a number, PushName "Katarzyna" -> should link to kasia
    store.add_message("whatsapp", "4855@s", "1", ts=1.0, body="kup mleko", sender="4855@s", sender_name="Katarzyna")
    c = Contacts(FakeBrain(), store)
    c.load()
    assert c.resolve("Kasia").slug == "kasia"
    assert "4855@s" in c.jids_for("Kasia")  # linked by pushname
    assert c.name_for("4855@s") == "❤️ Katarzyna (Kasia)"
    store.close()


def test_owner_lid_resolves_to_owner_not_phantom():
    store = Store(":memory:")
    c = Contacts(FakeBrain(), store, owner_name="Owner (ja)", owner_lid="100000000000001", owner_phone="48512345678")
    c.load()
    assert c.is_owner("100000000000001") is True       # the @lid
    assert c.is_owner("48512345678") is True            # the phone
    assert c.name_for("100000000000001", "Sówka 😁") == "Owner (ja)"  # not a phantom contact
    assert c.is_owner("48598765432") is False        # someone else
    store.close()


GUSIA_PLACEHOLDER = """---
title: ❤️ Katarzyna (Kasia)
---
# ❤️ Katarzyna "Kasia"
## Kontakt
- WhatsApp: do ustalenia z konwersacji
- Email: do ustalenia
"""


class WritableBrain:
    def __init__(self, notes):
        self.notes = dict(notes)  # path -> content

    def list_notes(self, folder=None):
        import json
        return json.dumps({"notes": list(self.notes)})

    def read(self, path):
        return self.notes[path]

    def write(self, path, content):
        self.notes[path] = content
        return "ok"


def test_auto_writeback_fills_confirmed_number():
    store = Store(":memory:")
    store.add_message("whatsapp", "31061", "1", ts=1.0, body="hej", sender="31061", sender_name="Katarzyna")
    brain = WritableBrain({"ludzie/kasia.md": GUSIA_PLACEHOLDER})
    c = Contacts(brain, store)
    c.load()
    changes = c.auto_writeback(today="2026-06-04")
    assert changes == [{"person": '❤️ Katarzyna (Kasia)', "slug": "kasia", "number": "31061"}]
    assert "31061" in brain.notes["ludzie/kasia.md"]
    assert "do ustalenia z konwersacji" not in brain.notes["ludzie/kasia.md"]  # placeholder replaced
    # idempotent: second run writes nothing (number already present)
    assert c.auto_writeback(today="2026-06-04") == []
    store.close()


def test_auto_writeback_skips_group_jid():
    # Piotrek only ever seen in a GROUP -> the group JID must NOT be saved as his number
    store = Store(":memory:")
    store.add_message("whatsapp", "120363000000001", "1", ts=1.0, body="co?", sender="120363000000001",
                      sender_name="Piotrek", is_group=True)
    brain = WritableBrain({"ludzie/aleksander.md": "# Piotrek\n## Kontakt\n- numer: do ustalenia\n"})
    c = Contacts(brain, store)
    c.load()
    assert c.auto_writeback() == []  # group-only -> nothing written
    store.close()


def test_auto_writeback_skips_ambiguous_pushname():
    store = Store(":memory:")
    store.add_message("whatsapp", "999", "1", ts=1.0, body="x", sender="999", sender_name="Ewa")
    # two people whose alias is 'maria' -> ambiguous -> no write
    brain = WritableBrain({
        "ludzie/maria1.md": "# Ewa Kowalska\n## Kontakt\n- WhatsApp: do ustalenia\n",
        "ludzie/maria2.md": "# Ewa Nowak\n## Kontakt\n- WhatsApp: do ustalenia\n",
    })
    c = Contacts(brain, store)
    c.load()
    assert c.auto_writeback() == []  # ambiguous -> nothing written
    store.close()


def test_auto_writeback_never_overwrites_real_number():
    store = Store(":memory:")
    store.add_message("whatsapp", "31061", "1", ts=1.0, body="hej", sender="31061", sender_name="Katarzyna")
    brain = WritableBrain({"ludzie/kasia.md": GUSIA_PLACEHOLDER.replace("do ustalenia z konwersacji", "111222333")})
    c = Contacts(brain, store)
    c.load()
    c.auto_writeback()
    assert "111222333" in brain.notes["ludzie/kasia.md"]  # original kept
    store.close()


def test_parse_owner_terms():
    note = '# Kasia\n## Podstawowe\n- **Mówi do mnie:** kotek, kotku / kotkowi\n- **Rola:** Dziewczyna\n'
    p = parse_person("kasia", note)
    assert p.owner_terms == {"kotek", "kotku", "kotkowi"}


def test_teach_owner_term_persists_and_scopes():
    store = Store(":memory:")
    store.add_message("whatsapp", "31061", "1", ts=1.0, body="hej", sender="31061", sender_name="Katarzyna")
    brain = WritableBrain({"ludzie/kasia.md": GUSIA_PLACEHOLDER.replace("## Kontakt", "## Podstawowe\n- **Rola:** x\n## Kontakt")})
    c = Contacts(brain, store)
    c.load()
    res = c.teach_owner_term("Kasia", "kotek")
    assert res["ok"] and "kotek" in res["terms"]
    assert "Mówi do mnie" in brain.notes["ludzie/kasia.md"]
    # scoped: only resolvable from Kasia, not globally for an unknown sender
    assert "kotek" in c.owner_terms_for("31061", "Kasia")
    assert c.owner_terms_for("99999", "") == set()  # unknown sender -> no terms
    store.close()


def test_link_google_unique_alias_only():
    store = Store(":memory:")
    c = Contacts(FakeBrain(), store)
    c.load()  # person Kasia, aliases {kasia, katarzyna}
    c.google_contacts = [
        {"name": "Kasia ❤️", "emails": {"aguu@gmail.com"}, "phones": {"+48 600 000 000"}, "tails": {"600000000"}},
        {"name": "Katarzyna Adhd", "emails": set(), "phones": {"+48 111"}, "tails": {"111"}},
        {"name": "Katarzyna Praca", "emails": set(), "phones": {"+48 222"}, "tails": {"222"}},
    ]
    n = c.link_google()
    p = c.resolve("Kasia")
    assert n == 1
    assert "+48 600 000 000" in p.phones        # unique "kasia" -> linked
    assert "+48 111" not in p.phones            # ambiguous "katarzyna" -> NOT linked
    assert "aguu@gmail.com" in p.emails
    store.close()


def test_describe_unknown():
    store = Store(":memory:")
    c = Contacts(FakeBrain(), store)
    c.load()
    assert c.describe("Nikt")["found"] is False
    assert c.describe("kasia")["found"] is True
    store.close()


def test_whatsapp_recent_filters_by_person():
    from aifred.skills.whatsapp_query import whatsapp_recent

    store = Store(":memory:")
    store.add_message("whatsapp", "4855@s", "1", ts=1.0, body="kup mleko", sender="4855@s", sender_name="Katarzyna")
    store.add_message("whatsapp", "work@s", "2", ts=2.0, body="raport", sender="work@s", sender_name="Boss")
    c = Contacts(FakeBrain(), store)
    c.load()
    out = whatsapp_recent(store, sender="Kasia", contacts=c)
    assert [m["text"] for m in out["messages"]] == ["kup mleko"]
    assert out["messages"][0]["from"] == "❤️ Katarzyna (Kasia)"
    store.close()


def test_lid_to_phone_enables_google_match():
    store = Store(":memory:")
    store.set_lid_phone("48598765432", "48600000000")   # resolved by neonize
    c = Contacts(FakeBrain(), store)
    c.load()  # loads lid_map
    c.google_contacts = [
        {"name": "Kasia ❤️", "emails": {"aguu@gmail.com"}, "phones": {"+48 600 000 000"}, "tails": {"600000000"}},
    ]
    # lid sender now resolves to the Google contact via its real phone
    assert c.name_for("48598765432") == "Kasia ❤️"
    # without the lid map a raw lid wouldn't match
    c.lid_map = {}
    assert c.name_for("48598765432") == "48598765432"
    store.close()
