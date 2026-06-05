"""WhatsApp worker normalize tests — fake neonize proto, no live connection."""

from aifred.whatsapp.worker import normalize_neonize


class _Src:
    class Chat:
        User = "fam@g.us"

    class Sender:
        User = "48555111@s.whatsapp.net"


class _Info:
    MessageSource = _Src
    ID = "WA123"

    class Timestamp:
        seconds = 1717412400


class _Msg:
    conversation = "dinner at 6?"


class FakeMessageEv:
    Info = _Info
    Message = _Msg


def test_normalize_neonize_extracts_fields():
    wam = normalize_neonize(FakeMessageEv())
    assert wam is not None
    assert wam.ext_id == "WA123"
    assert wam.chat_id == "fam@g.us"
    assert wam.body == "dinner at 6?"
    assert wam.ts == 1717412400.0


def test_normalize_missing_id_dropped():
    class NoId(FakeMessageEv):
        class Info:
            MessageSource = _Src
            ID = ""

            class Timestamp:
                seconds = 1

    assert normalize_neonize(NoId()) is None


def test_normalize_no_info():
    class Empty:
        Info = None
        Message = None

    assert normalize_neonize(Empty()) is None
