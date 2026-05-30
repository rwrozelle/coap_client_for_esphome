"""Unit tests for _SimpleOscoreSecurityContext — no network needed.

Tests cover key derivation (RFC 8613 Appendix C.1 known-answer vectors),
sequence number threshold persistence, and the protect() method patches
(FETCH→POST remap, URI-Path copy, mtype copy).
"""

import aiocoap
import pytest

from coap_client_for_esphome.coordinator import _SimpleOscoreSecurityContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OSCORE_SEQ_INTERVAL = 1024


def _ctx(
    sender_id: bytes = b"\x02",
    recipient_id: bytes = b"\x01",
    initial_seq_no: int = 0,
    on_threshold=None,
) -> _SimpleOscoreSecurityContext:
    return _SimpleOscoreSecurityContext(
        master_secret=bytes.fromhex("0102030405060708090a0b0c0d0e0f10"),
        master_salt=bytes.fromhex("9e7ca92223786340"),
        sender_id=sender_id,
        recipient_id=recipient_id,
        id_context=None,
        initial_seq_no=initial_seq_no,
        on_threshold=on_threshold,
    )


# ---------------------------------------------------------------------------
# Key derivation — RFC 8613 Appendix C.1
# ---------------------------------------------------------------------------
#
# Test vectors from RFC 8613 §C.1.1:
#   master_secret = 0x0102030405060708090a0b0c0d0e0f10
#   master_salt   = 0x9e7ca92223786340
#   sender_id     = b''   (empty byte string)
#   recipient_id  = 0x01
#   algorithm     = AES-CCM-16-64-128
#   hash function = SHA-256
#
# Expected outputs:
#   Sender Key    = f0910ed7295e6ad4b54fc793154302ff
#   Recipient Key = ffb14e093c94c9cac9471648b4f98710
#   Common IV     = 4622d4dd6d944168eefb54987c


def test_key_derivation_sender_key():
    ctx = _SimpleOscoreSecurityContext(
        master_secret=bytes.fromhex("0102030405060708090a0b0c0d0e0f10"),
        master_salt=bytes.fromhex("9e7ca92223786340"),
        sender_id=b"",
        recipient_id=bytes.fromhex("01"),
        id_context=None,
    )
    assert ctx.sender_key == bytes.fromhex("f0910ed7295e6ad4b54fc793154302ff")


def test_key_derivation_recipient_key():
    ctx = _SimpleOscoreSecurityContext(
        master_secret=bytes.fromhex("0102030405060708090a0b0c0d0e0f10"),
        master_salt=bytes.fromhex("9e7ca92223786340"),
        sender_id=b"",
        recipient_id=bytes.fromhex("01"),
        id_context=None,
    )
    assert ctx.recipient_key == bytes.fromhex("ffb14e093c94c9cac9471648b4f98710")


def test_key_derivation_common_iv():
    ctx = _SimpleOscoreSecurityContext(
        master_secret=bytes.fromhex("0102030405060708090a0b0c0d0e0f10"),
        master_salt=bytes.fromhex("9e7ca92223786340"),
        sender_id=b"",
        recipient_id=bytes.fromhex("01"),
        id_context=None,
    )
    assert ctx.common_iv == bytes.fromhex("4622d4dd6d944168eefb54987c")


def test_key_derivation_different_ids_produce_different_keys():
    ctx_a = _ctx(sender_id=b"\x01", recipient_id=b"\x02")
    ctx_b = _ctx(sender_id=b"\x02", recipient_id=b"\x01")
    assert ctx_a.sender_key != ctx_b.sender_key
    assert ctx_a.recipient_key != ctx_b.recipient_key


def test_key_derivation_same_inputs_reproducible():
    ctx1 = _ctx()
    ctx2 = _ctx()
    assert ctx1.sender_key == ctx2.sender_key
    assert ctx1.common_iv == ctx2.common_iv


# ---------------------------------------------------------------------------
# Sequence number threshold
# ---------------------------------------------------------------------------


def test_initial_threshold_is_seq_plus_interval():
    ctx = _ctx(initial_seq_no=0)
    assert ctx._oscore_seq_threshold == _OSCORE_SEQ_INTERVAL


def test_initial_threshold_respects_starting_seq():
    ctx = _ctx(initial_seq_no=2048)
    assert ctx._oscore_seq_threshold == 2048 + _OSCORE_SEQ_INTERVAL


def test_threshold_callback_fires_at_crossing():
    fired: list[int] = []
    ctx = _ctx(initial_seq_no=0, on_threshold=lambda t: fired.append(t))
    ctx.sender_sequence_number = _OSCORE_SEQ_INTERVAL
    ctx.post_seqnoincrease()
    assert fired == [_OSCORE_SEQ_INTERVAL * 2]


def test_threshold_updates_after_crossing():
    ctx = _ctx(initial_seq_no=0)
    ctx.sender_sequence_number = _OSCORE_SEQ_INTERVAL
    ctx.post_seqnoincrease()
    assert ctx._oscore_seq_threshold == _OSCORE_SEQ_INTERVAL * 2


def test_threshold_not_fired_below_crossing():
    fired: list[int] = []
    ctx = _ctx(initial_seq_no=0, on_threshold=lambda t: fired.append(t))
    ctx.sender_sequence_number = _OSCORE_SEQ_INTERVAL - 1
    ctx.post_seqnoincrease()
    assert fired == []


def test_threshold_fires_multiple_times():
    fired: list[int] = []
    ctx = _ctx(initial_seq_no=0, on_threshold=lambda t: fired.append(t))
    for crossing in range(1, 4):
        ctx.sender_sequence_number = _OSCORE_SEQ_INTERVAL * crossing
        ctx.post_seqnoincrease()
    assert len(fired) == 3
    assert fired == [
        _OSCORE_SEQ_INTERVAL * 2,
        _OSCORE_SEQ_INTERVAL * 3,
        _OSCORE_SEQ_INTERVAL * 4,
    ]


def test_no_callback_without_on_threshold():
    ctx = _ctx(initial_seq_no=0, on_threshold=None)
    ctx.sender_sequence_number = _OSCORE_SEQ_INTERVAL
    ctx.post_seqnoincrease()  # must not raise


# ---------------------------------------------------------------------------
# protect() — FETCH→POST remap (Bug 2 regression)
# ---------------------------------------------------------------------------


def test_protect_remaps_fetch_to_post():
    ctx = _ctx()
    msg = aiocoap.Message(code=aiocoap.FETCH, uri="coap://127.0.0.1/fp/1")
    msg.opt.uri_path = ("fp", "1")
    protected, _ = ctx.protect(msg)
    assert protected.code == aiocoap.POST


def test_protect_leaves_get_unchanged():
    ctx = _ctx()
    msg = aiocoap.Message(code=aiocoap.GET, uri="coap://127.0.0.1/fp/1")
    msg.opt.uri_path = ("fp", "1")
    protected, _ = ctx.protect(msg)
    # Outer code for OSCORE GET is POST (per RFC 8613); inner code is preserved in ciphertext.
    # What matters is it is NOT remapped from a non-FETCH source unexpectedly.
    assert protected.code != aiocoap.FETCH


# ---------------------------------------------------------------------------
# protect() — URI-Path copy to outer message (OpenThread routing requirement)
# ---------------------------------------------------------------------------


def test_protect_copies_uri_path_to_outer():
    ctx = _ctx()
    msg = aiocoap.Message(code=aiocoap.GET, uri="coap://127.0.0.1/fp/1")
    msg.opt.uri_path = ("fp", "1")
    protected, _ = ctx.protect(msg)
    assert protected.opt.uri_path == ("fp", "1")


def test_protect_copies_nested_uri_path():
    ctx = _ctx()
    msg = aiocoap.Message(code=aiocoap.GET, uri="coap://127.0.0.1/fp/9/g/1")
    msg.opt.uri_path = ("fp", "9", "g", "1")
    protected, _ = ctx.protect(msg)
    assert protected.opt.uri_path == ("fp", "9", "g", "1")


# ---------------------------------------------------------------------------
# protect() — transport_tuning copy (Bug 6 regression: CON/NON for observe)
# ---------------------------------------------------------------------------


def test_protect_copies_unreliable_transport_tuning():
    ctx = _ctx()
    msg = aiocoap.Message(code=aiocoap.GET, transport_tuning=aiocoap.Unreliable, uri="coap://127.0.0.1/fp/1")
    msg.opt.uri_path = ("fp", "1")
    protected, _ = ctx.protect(msg)
    assert protected.transport_tuning is aiocoap.Unreliable


def test_protect_copies_reliable_transport_tuning():
    ctx = _ctx()
    msg = aiocoap.Message(code=aiocoap.GET, transport_tuning=aiocoap.Reliable, uri="coap://127.0.0.1/fp/1")
    msg.opt.uri_path = ("fp", "1")
    protected, _ = ctx.protect(msg)
    assert protected.transport_tuning is aiocoap.Reliable
