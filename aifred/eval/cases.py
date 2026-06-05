"""Golden evaluation cases — built from AIfred's REAL data, with the correct
answers established by a large model with full context. Three task families that
target the failure the owner cares about: hallucination, worse as context grows.

  grounding  — answer ONLY from context, else say NIE_WIEM. The needle is real;
               distractor padding grows the prompt to expose context-loss.
  extract    — pull calendar events from a note; must NOT invent dates.
  triage     — classify importance/directed; must respect group + owner-terms.
"""

from __future__ import annotations

# A block of unrelated-but-realistic Polish chatter to grow the context window.
PAD = (
    "Anna: Olka, daj znać czy masz kasetkę na forsę pilnikową.\n"
    "Ewa: Ciekawe czy się ujawni.\n"
    "Jan Kowalski: Tomaszem J.\n"
    "Piotr: Co? Nie chciałem się logować. Ja też nie wiem.\n"
    "Sówka: Dostałem kod Netflix na maila. Czyli to nie ty. To nie wiem kto.\n"
    "kilocode@substack.com: Architect Agent Uses Grill-Me to Ask Better Questions.\n"
    "ikea@news.email.ikea.pl: Przeprowadzasz się? Możemy pomóc!\n"
    "temu@eu.temuemail.com: 3 artykuły za 39,9 zł.\n"
    "kayak@msg.kayak.com: Need a rental car? Grab this Double Deal.\n"
    "rossmann.pl: Moc promocji i inspiracji.\n"
) * 12  # ~3k tokens of distractors


GROUNDING_SYS = (
    "Odpowiadasz WYŁĄCZNIE na podstawie KONTEKSTU. Jeśli odpowiedzi nie ma w kontekście, "
    "odpowiedz dokładnie: NIE_WIEM. Nie zgaduj, nie dodawaj wiedzy spoza kontekstu. "
    "Odpowiedz jednym krótkim zdaniem albo NIE_WIEM."
)

# (question, context_core, gold) — gold None means must refuse (NIE_WIEM).
_GROUNDING = [
    ("O której godzinie Zosia ma wizytę u dentysty?",
     "Zosia we wtorek 09.06 ma wizytę u dentysty. STOMATOLOG KAMIENNA 21 GODZINA 16.40.",
     "16.40"),
    ("Kiedy Zosia nocuje u Ownera?",
     "Zosia będzie u Ciebie nocować 16 i 17 czerwca.",
     "16"),
    ("Jaki jest numer telefonu Marty?",                       # not in context -> refuse
     "22.06 i 23.06 mama by Marta odebrała bo jeszcze mnie nie będzie.",
     None),
    ("Ile kosztuje wizyta u dentysty?",                        # not in context -> refuse
     "Zosia we wtorek 09.06 ma wizytę u dentysty. STOMATOLOG KAMIENNA 21 GODZINA 16.40.",
     None),
    ("Kto prosił o odbiór paczki?",
     "Kasia napisała: a odebrałby kotek paczkę i kupił truskawki. Przeleje kotkowi.",
     "Kasia"),
    ("Jaki jest adres email Ownera?",                         # not in context -> refuse
     "Sówka: jaki ty masz mail? czy się do nf logować.",
     None),
]

EXTRACT_SYS = (
    "Wyłuskaj wydarzenia do kalendarza. Dziś 2026-06-04. Dla każdego: summary, date (YYYY-MM-DD), "
    "all_day (bool), start_time (HH:MM lub null). Użyj TYLKO dat obecnych w tekście — nie wymyślaj. "
    "Odpowiedz TYLKO tablicą JSON."
)
# (note_text, must_dates, forbidden_count_over) — dates that MUST appear; extra invented dates = hallucination
_EXTRACT = [
    ("Zosia we wtorek 09.06 wizyta u dentysty godzina 16.40. Nocuje 16 i 17 czerwca.",
     {"2026-06-09", "2026-06-16", "2026-06-17"}),
    ("Spotkanie z Janem w piątek. Trzeba kupić mleko.",       # no concrete date -> expect empty/near-empty
     set()),
    ("Dentysta 22.06 o 9:00. Odbiór ze szkoły 23.06.",
     {"2026-06-22", "2026-06-23"}),
]

TRIAGE_SYS = (
    "Triage wiadomości dla właściciela Ownera (Kasia mówi do niego 'kotek'). Zwróć JSON: "
    '{"importance":"high|medium|low","directed_at_me":bool}. '
    "W grupie wiadomość do kogoś innego = low + directed_at_me false. "
    "Kod/OTP/newsletter = low. Prośba do właściciela = high."
)
# (where, sender, body, gold_importance_in, gold_directed)
_TRIAGE = [
    ("direct", "Kasia", "a odebrałby kotek paczkę", {"high", "medium"}, True),
    ("GROUP", "Anna", "Olka, daj znać czy masz kasetkę", {"low"}, False),
    ("direct", "Netflix", "Twój kod logowania to 481923", {"low"}, None),
    ("GROUP", "Ewa", "Ciekawe czy się ujawni", {"low"}, False),
    ("direct", "Kasia", "kupiłbyś kotek chleb?", {"high", "medium"}, True),
]


def grounding_cases(tier: str = "small") -> list[dict]:
    """tier: small (needle only) | grow (~3k pad) | huge (~10k pad). For grow/huge
    the needle sits in the MIDDLE so truncation at either end would drop it."""
    mult = {"small": 0, "grow": 12, "huge": 40}[tier]
    pad = PAD if tier == "grow" else (PAD * 4 if tier == "huge" else "")
    out = []
    for q, core, gold in _GROUNDING:
        ctx = (pad + "\n" + core + "\n" + pad) if mult else core
        out.append({"task": "grounding", "grow": tier != "small", "tier": tier, "system": GROUNDING_SYS,
                    "user": f"KONTEKST:\n{ctx}\n\nPYTANIE: {q}", "gold": gold, "q": q})
    return out


def extract_cases() -> list[dict]:
    out = []
    for text, must in _EXTRACT:
        out.append({"task": "extract", "system": EXTRACT_SYS, "user": text, "must": must})
    return out


def triage_cases() -> list[dict]:
    out = []
    for where, sender, body, imp, directed in _TRIAGE:
        out.append({"task": "triage", "system": TRIAGE_SYS,
                    "user": f"[{where}] od {sender}: {body}", "imp": imp, "directed": directed})
    return out


def all_cases() -> list[dict]:
    return (grounding_cases("small") + grounding_cases("grow") + grounding_cases("huge")
            + extract_cases() + triage_cases())
