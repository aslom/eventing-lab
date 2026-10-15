"""The approved-key set, and the `kid` that selects from it.

Also covers the sign -> Kafka wire -> verify path, which had no test at all: the
existing signing tests sign and verify the same in-memory object, and the codec
tests never touch signing. So nothing checked that the signature travels as a
`ce_signature` header, that the `kid` survives, or that tampering between producer
and consumer is caught.
"""
from __future__ import annotations

import base64
import binascii
import json

import pytest

from shared import ce, keyset
from shared import signing as S

# RFC 8032 test vector 1 — the same seed the signing tests use.
SEED = binascii.unhexlify(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
PUB = S.public_key(SEED)

SEED2 = binascii.unhexlify(
    "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
PUB2 = S.public_key(SEED2)


def _write(tmp_path, obj) -> str:
    p = tmp_path / "agents.json"
    p.write_text(json.dumps(obj))
    return str(p)


def _event(**over):
    attrs = {"type": ce.TYPE_RESPONSE, "source": "rossoctl://eventrunner/test",
             "datacontenttype": "application/json",
             "correlationid": "brave-otter-4718",
             "sessionuuid": "8f1b6d2e-0000-5000-8000-000000000000",
             "sequence": 3, "phase": "result", "final": "true"}
    attrs.update(over)
    return ce.new_event(data={"text": "hello"}, **attrs)


# ---- loading ----------------------------------------------------------------

def test_load_accepts_hex_base64_and_base64url(tmp_path):
    ks = keyset.load(_write(tmp_path, {
        "hex": PUB.hex(),
        "b64": base64.b64encode(PUB).decode(),
        "b64u": base64.urlsafe_b64encode(PUB).decode().rstrip("="),
    }))
    assert len(ks) == 3
    assert ks.select("hex") == ks.select("b64") == ks.select("b64u") == PUB


def test_kids_are_sorted_and_loggable(tmp_path):
    ks = keyset.load(_write(tmp_path, {"z": PUB.hex(), "a": PUB2.hex()}))
    assert ks.kids == ("a", "z")


def test_membership(tmp_path):
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    assert "runner-01" in ks and "runner-99" not in ks


@pytest.mark.parametrize("bad", [
    {"k": "not-hex-at-all"},
    {"k": PUB.hex()[:40]},          # too short
    {"k": (PUB + b"\x00").hex()},   # 33 bytes
    {"k": 1234},                    # not a string
    {"": PUB.hex()},                # empty kid
])
def test_load_rejects_a_malformed_entry_rather_than_partially_loading(tmp_path, bad):
    """An authorization list that quietly dropped an entry would fail closed for a
    legitimate agent, which reads as a broken deploy rather than a control."""
    with pytest.raises(ValueError):
        keyset.load(_write(tmp_path, bad))


def test_load_rejects_a_non_object(tmp_path):
    with pytest.raises(ValueError):
        keyset.load(_write(tmp_path, ["not", "an", "object"]))


def test_load_if_set_returns_none_when_unconfigured():
    assert keyset.load_if_set(None) is None
    assert keyset.load_if_set("") is None


# ---- selection --------------------------------------------------------------

def test_select_returns_the_named_key(tmp_path):
    ks = keyset.load(_write(tmp_path, {"a": PUB.hex(), "b": PUB2.hex()}))
    assert ks.select("a") == PUB
    assert ks.select("b") == PUB2


def test_select_unknown_kid_returns_none(tmp_path):
    ks = keyset.load(_write(tmp_path, {"a": PUB.hex()}))
    assert ks.select("nope") is None


def test_select_none_kid_works_for_a_single_key_set(tmp_path):
    """A one-key deployment should not need every signer to name its key."""
    ks = keyset.load(_write(tmp_path, {"only": PUB.hex()}))
    assert ks.select(None) == PUB


def test_select_none_kid_is_ambiguous_with_several_keys(tmp_path):
    """Guessing would mean accepting a signature from ANY approved agent for an
    event that named none of them."""
    ks = keyset.load(_write(tmp_path, {"a": PUB.hex(), "b": PUB2.hex()}))
    assert ks.select(None) is None


# ---- kid in the token -------------------------------------------------------

def test_sign_event_without_kid_keeps_the_original_header():
    """Backward compatible: an unnamed token is byte-identical to before."""
    token = S.sign_event(_event(), SEED)
    header = json.loads(S._b64u_dec(token.split(".")[0]))
    assert header == {"alg": "EdDSA", "typ": "ce+jws"}
    assert S.token_kid(token) is None


def test_sign_event_with_kid_puts_it_in_the_protected_header():
    token = S.sign_event(_event(), SEED, kid="runner-01")
    header = json.loads(S._b64u_dec(token.split(".")[0]))
    assert header == {"alg": "EdDSA", "kid": "runner-01", "typ": "ce+jws"}
    assert S.token_kid(token) == "runner-01"


def test_a_kid_bearing_token_still_verifies():
    e = _event()
    e.attrs["signature"] = S.sign_event(e, SEED, kid="runner-01")
    ok, why = S.verify_signature(e, PUB)
    assert ok, why


def test_the_kid_is_covered_by_the_signature():
    """Swapping the kid to point at another approved key must not verify —
    otherwise an attacker could relabel an event as coming from someone else."""
    e = _event()
    token = S.sign_event(e, SEED, kid="runner-01")
    protected, _, sig = token.split(".")
    forged_header = S._b64u(json.dumps(
        {"alg": "EdDSA", "kid": "runner-02", "typ": "ce+jws"},
        separators=(",", ":"), sort_keys=True).encode())
    e.attrs["signature"] = f"{forged_header}..{sig}"
    ok, _ = S.verify_signature(e, PUB)
    assert not ok


@pytest.mark.parametrize("token", ["", "a.b", "not a token", "...", "a..b..c"])
def test_token_kid_tolerates_garbage(token):
    assert S.token_kid(token) is None


def test_token_kid_ignores_a_non_string_kid():
    header = S._b64u(json.dumps({"alg": "EdDSA", "kid": 7}).encode())
    assert S.token_kid(f"{header}..sig") is None


# ---- the end-to-end check: sign -> Kafka wire -> verify ---------------------

def test_signature_survives_the_kafka_binary_roundtrip():
    """The gap this file exists to close.

    Before this, nothing tested signing across the codec: the signing tests sign
    and verify the same in-memory object, and the codec tests never sign. So the
    signature travelling as a `ce_signature` header, the `kid` surviving the trip,
    and tampering between producer and consumer were all unverified.

    Type stability is the reason it works. `ce.new_event` stringifies attributes on
    construction, so `sequence` is already `"3"` when it is signed and is still
    `"3"` after `from_kafka_binary` — nothing is coerced in between. That is worth
    pinning: signing over values that changed representation on the wire would
    fail for every event, and only an end-to-end test catches it.
    """
    e = _event(sequence=3)
    e.attrs["signature"] = S.sign_event(e, SEED, kid="runner-01")
    headers, value = ce.to_kafka_binary(e)
    back = ce.from_kafka_binary(headers, value)

    assert S.token_kid(back.get("signature")) == "runner-01"
    ok, why = S.verify_signature(back, PUB)
    assert ok, why


def test_roundtrip_verification_fails_with_the_wrong_key():
    e = _event()
    e.attrs["signature"] = S.sign_event(e, SEED, kid="runner-01")
    back = ce.from_kafka_binary(*ce.to_kafka_binary(e))
    ok, _ = S.verify_signature(back, PUB2)
    assert not ok


def test_tampering_on_the_wire_is_detected():
    """Rewrite an attribute after signing, as anything with topic write access
    could, and the signature must fail."""
    e = _event()
    e.attrs["signature"] = S.sign_event(e, SEED, kid="runner-01")
    headers, value = ce.to_kafka_binary(e)
    headers = [(k, b"hostile-otter-0001" if k == "ce_correlationid" else v)
               for k, v in headers]
    back = ce.from_kafka_binary(headers, value)
    ok, _ = S.verify_signature(back, PUB)
    assert not ok


def test_payload_tampering_on_the_wire_is_detected():
    e = _event()
    e.attrs["signature"] = S.sign_event(e, SEED, kid="runner-01")
    headers, _ = ce.to_kafka_binary(e)
    back = ce.from_kafka_binary(
        headers, json.dumps({"text": "Transfer approved."}).encode())
    ok, _ = S.verify_signature(back, PUB)
    assert not ok


def _tampered_on_the_wire(attr, value, **signed_attrs):
    """Sign an event, then rewrite one `ce_` header in flight. Returns (ok, why)."""
    e = _event(**signed_attrs)
    e.attrs["signature"] = S.sign_event(e, SEED, kid="runner-01")
    headers, body = ce.to_kafka_binary(e)
    headers = [(k, value if k == f"ce_{attr}" else v) for k, v in headers]
    return S.verify_signature(ce.from_kafka_binary(headers, body), PUB)


def test_the_submitter_is_covered_by_the_signature():
    """What makes the claim in eventbridge/auth.py true rather than aspirational.

    `submitter` records who EventBridge believes submitted a request. Until it was in
    SIGNED_ATTRS, anything with topic write access could rewrite that name — so the
    audit trail named whoever the last writer chose.
    """
    ok, why = _tampered_on_the_wire("submitter", b"attacker",
                                    submitter="mrsabath", submitteriss="github")
    assert not ok and "does not verify" in why


def test_the_submitter_issuer_is_covered_by_the_signature():
    """Without this, a forged `submitteriss=github` upgrades a name an operator typed
    into an env var to a login GitHub appears to have verified."""
    ok, _ = _tampered_on_the_wire("submitteriss", b"github",
                                  submitter="mrsabath", submitteriss="static")
    assert not ok


def test_the_groupid_is_covered_by_the_signature():
    """docs/specs/0002-phase1-keda-kubernetes.md §21.9.9 requires it: a forged `groupid` moves a response into
    another batch and corrupts that batch's fan-in counts."""
    ok, _ = _tampered_on_the_wire("groupid", b"g-victim", groupid="g-mine")
    assert not ok


def test_adding_a_groupid_after_signing_is_detected():
    """The absence of an attribute is signed too, not just its value — otherwise an
    ungrouped response could be adopted into a batch it was never part of."""
    e = _event()                       # no groupid
    e.attrs["signature"] = S.sign_event(e, SEED, kid="runner-01")
    headers, body = ce.to_kafka_binary(e)
    headers.append(("ce_groupid", b"g-victim"))
    ok, _ = S.verify_signature(ce.from_kafka_binary(headers, body), PUB)
    assert not ok


def test_text_plain_payload_roundtrips():
    e = _event(datacontenttype="text/plain")
    e.data = "hello"
    e.attrs["signature"] = S.sign_event(e, SEED)
    back = ce.from_kafka_binary(*ce.to_kafka_binary(e))
    ok, why = S.verify_signature(back, PUB)
    assert ok, why


# ---- the two together: kid selects, then verification decides ---------------

def test_approved_agent_verifies_and_unapproved_does_not(tmp_path):
    """The intended mechanism, composed by hand: the keyset as authorization list.

    Kept hand-wired even though `signing.verify_with_keyset` now does exactly this in
    production, because a test that reaches through the primitives one at a time says
    where a failure is — `select` returned nothing, or the signature did not verify —
    which a single boolean from the composed function cannot.
    """
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))

    approved = _event()
    approved.attrs["signature"] = S.sign_event(approved, SEED, kid="runner-01")
    back = ce.from_kafka_binary(*ce.to_kafka_binary(approved))
    pub = ks.select(S.token_kid(back.get("signature")))
    assert pub is not None
    assert S.verify_signature(back, pub)[0]

    # Same event, signed by a key nobody approved, naming a kid not in the set.
    rogue = _event()
    rogue.attrs["signature"] = S.sign_event(rogue, SEED2, kid="runner-99")
    back2 = ce.from_kafka_binary(*ce.to_kafka_binary(rogue))
    assert ks.select(S.token_kid(back2.get("signature"))) is None


def test_a_rogue_key_claiming_an_approved_kid_is_refused(tmp_path):
    """The nastier case: the attacker knows an approved kid but not its key."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    e = _event()
    e.attrs["signature"] = S.sign_event(e, SEED2, kid="runner-01")
    back = ce.from_kafka_binary(*ce.to_kafka_binary(e))
    pub = ks.select(S.token_kid(back.get("signature")))
    assert pub == PUB                      # kid resolves...
    assert not S.verify_signature(back, pub)[0]   # ...but the signature does not


# ---- sign_into: the one place that decides "signing is enabled" --------------

def test_sign_into_is_a_no_op_without_a_seed():
    """The default path, asserted rather than assumed: no seed, no attribute."""
    e = _event()
    assert S.sign_into(e, None) is False
    assert "signature" not in e.attrs


def test_sign_into_signs_and_the_result_verifies():
    e = _event()
    assert S.sign_into(e, SEED, "runner-01") is True
    assert S.verify_signature(e, PUB) == (True, "ok")
    assert S.token_kid(e.attrs["signature"]) == "runner-01"


def test_sign_into_degrades_rather_than_raising_on_a_bad_seed():
    """A key-config mistake must not take out publishing entirely.

    Unsigned is safe because the verifying side refuses unsigned events when
    enforcement is on; a raise here would reject 100% of traffic at the producer.
    """
    e = _event()
    assert S.sign_into(e, b"\x01" * 31) is False   # 31 bytes is not an Ed25519 seed
    assert "signature" not in e.attrs


# ---- verify_with_keyset: the composed authorization check --------------------

def test_verify_with_keyset_accepts_an_approved_signer(tmp_path):
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    e = _event()
    S.sign_into(e, SEED, "runner-01")
    back = ce.from_kafka_binary(*ce.to_kafka_binary(e))
    ok, why = S.verify_with_keyset(back, ks)
    assert ok, why


def test_verify_with_keyset_reports_a_missing_signature(tmp_path):
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    ok, why = S.verify_with_keyset(_event(), ks)
    assert not ok and "no ce_signature" in why


def test_verify_with_keyset_reports_an_unapproved_kid(tmp_path):
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    e = _event()
    S.sign_into(e, SEED2, "runner-99")
    ok, why = S.verify_with_keyset(e, ks)
    assert not ok and "'runner-99' is not in the approved key set" in why


def test_verify_with_keyset_reports_an_ambiguous_unnamed_token(tmp_path):
    """Two approved keys and a token naming neither: guessing would accept a
    signature from *any* approved agent for an event that claimed none of them."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex(),
                                       "runner-02": PUB2.hex()}))
    e = _event()
    S.sign_into(e, SEED)                      # no kid
    ok, why = S.verify_with_keyset(e, ks)
    assert not ok and "ambiguous" in why


def test_verify_with_keyset_accepts_an_unnamed_token_against_a_single_key(tmp_path):
    """The friendly path: one approved key means nothing has to name it."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    e = _event()
    S.sign_into(e, SEED)                      # no kid
    ok, why = S.verify_with_keyset(e, ks)
    assert ok, why


def test_expect_kid_pins_the_signer_to_one_identity(tmp_path):
    """What stops an approved runner forging an EventBridge group event."""
    ks = keyset.load(_write(tmp_path, {"eventbridge": PUB.hex(),
                                       "runner-01": PUB2.hex()}))
    e = _event(type=ce.TYPE_GROUP_STARTED)
    S.sign_into(e, SEED2, "runner-01")        # a genuinely approved key...
    ok, why = S.verify_with_keyset(e, ks, expect_kid="eventbridge")
    assert not ok and "expected a signature from kid 'eventbridge'" in why

    bridge = _event(type=ce.TYPE_GROUP_STARTED)
    S.sign_into(bridge, SEED, "eventbridge")
    assert S.verify_with_keyset(bridge, ks, expect_kid="eventbridge")[0]


# ---- response_decision: enabled? verified? enforced? -------------------------

def test_response_decision_accepts_everything_when_no_keyset_is_configured():
    """Today's behaviour, which must survive untouched as the default. `verified=True`
    here is the same reading as the unsigned non-terminal passthrough: nothing being
    configured to check is an acceptance by policy, not a failed check — so
    `accept and not verified` stays the audit-mode population with no caller-side
    keyset guard."""
    ok, why, verified = S.response_decision(_event(), None, require=True)
    assert ok and verified and "not enabled" in why


def test_response_decision_accepts_a_verified_response(tmp_path):
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    e = _event()
    S.sign_into(e, SEED, "runner-01")
    assert S.response_decision(e, ks, require=True) == (True, "ok", True)


def test_response_decision_rejects_an_unsigned_response_when_enforcing(tmp_path):
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    ok, why, verified = S.response_decision(_event(), ks, require=True)
    assert not ok and "no ce_signature" in why


def test_response_decision_reports_but_accepts_in_audit_mode(tmp_path):
    """The two-flag rollout in one assertion: a keyset alone verifies and explains,
    without yet rewriting anything a user sees."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    ok, why, verified = S.response_decision(_event(), ks, require=False)
    assert ok, "audit mode must not reject"
    assert "no ce_signature" in why, "but it must still say what was wrong"
    assert not verified, \
        "and it must be distinguishable from a clean pass, which is what makes the " \
        "reject rate countable without parsing the reason prose"


def test_response_decision_requires_the_bridge_kid_on_group_events(tmp_path):
    ks = keyset.load(_write(tmp_path, {"eventbridge": PUB.hex(),
                                       "runner-01": PUB2.hex()}))
    forged = _event(type=ce.TYPE_GROUP_COMPLETED)
    S.sign_into(forged, SEED2, "runner-01")
    ok, why, verified = S.response_decision(forged, ks, require=True, bridge_kid="eventbridge")
    assert not ok and "expected a signature from kid 'eventbridge'" in why

    # A plain response from that same runner is still fine — the pin is per-class.
    resp = _event()
    S.sign_into(resp, SEED2, "runner-01")
    assert S.response_decision(resp, ks, require=True, bridge_kid="eventbridge")[0]


def test_an_unsigned_non_terminal_frame_is_accepted(tmp_path):
    """Matching the signing policy, not a loophole.

    `emit()` signs terminal events only, because it runs per stdout frame at
    ~150-200 ms a signature. Verifying all-or-nothing would rewrite every streamed
    frame of every genuine run to phase=error — which is what happened the first time
    this was run against a real broker.
    """
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    frame = _event(sequence=1, phase="stdout", final="false")
    ok, why, verified = S.response_decision(frame, ks, require=True)
    assert ok, why
    assert "non-terminal" in why
    assert verified, \
        "accepted by policy, not in spite of a failure — otherwise an audit-mode " \
        "counter would log every streamed frame of every genuine run"


def test_an_unsigned_terminal_response_is_still_rejected(tmp_path):
    """The line the exemption must not cross: the terminal event is the one the
    transcript presents as the answer, so refusing it unsigned is the whole control."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    ok, why, verified = S.response_decision(_event(phase="result", final="true"), ks, require=True)
    assert not ok and "no ce_signature" in why


def test_a_non_terminal_frame_that_claims_a_signature_is_still_verified(tmp_path):
    """The exemption is for events carrying NO signature. One that presents a bad
    signature is lying about something, and gets checked."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    frame = _event(sequence=1, phase="stdout", final="false")
    S.sign_into(frame, SEED2, "runner-01")          # approved kid, wrong key
    ok, why, verified = S.response_decision(frame, ks, require=True)
    assert not ok and "does not verify" in why


def test_an_unsigned_group_event_is_rejected_despite_having_no_final_attribute(tmp_path):
    """Group events carry no `final` at all, so testing that alone would classify one
    as an unsigned intermediate frame and wave it through — which is precisely the
    forged `group.completed` the bridge kid exists to catch."""
    ks = keyset.load(_write(tmp_path, {"eventbridge": PUB.hex()}))
    grp = _event(type=ce.TYPE_GROUP_COMPLETED, groupid="g-1")
    grp.attrs.pop("final", None)
    assert "final" not in grp.attrs
    ok, why, verified = S.response_decision(grp, ks, require=True, bridge_kid="eventbridge")
    assert not ok and "no ce_signature" in why


def test_response_decision_does_not_mutate_the_event(tmp_path):
    """What "pure" buys: the caller owns the rewrite, so this is safe to call on the
    hot path of a consumer loop without copying first."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    e = _event()
    before_attrs, before_data = dict(e.attrs), dict(e.data)
    S.response_decision(e, ks, require=True)
    assert e.attrs == before_attrs and e.data == before_data


# --- An enforcing service must refuse an empty approved set (eventing-lab#12) -------
#
# `refusal_if_enforcing_empty` is the decision; the two `__main__`s own the exit. The
# bug it closes was found on a cluster and not by any of these tests' ancestors: a
# 0-kid set verified nothing, so the runner refused every request including the
# system's own, while the startup banner read ENFORCING. Four indicators were green.


def _empty(tmp_path):
    return _write(tmp_path, {})


def test_an_enforcing_service_refuses_to_start_on_a_zero_kid_set(tmp_path):
    """The bug: 0 kids admits nothing, so enforcement refuses everything silently."""
    ks = keyset.load(_empty(tmp_path))
    assert len(ks) == 0
    why = keyset.refusal_if_enforcing_empty(
        ks, enforcing=True, service="eventrunner",
        path="/keys/keyset.json", env_var="ER_VERIFY_KEYSET_PATH")
    assert why is not None


def test_the_refusal_names_the_variable_and_the_path(tmp_path):
    """The message is the whole point: an empty ConfigMap has to be diagnosable from
    `kubectl logs` alone, which is where an operator meets this."""
    ks = keyset.load(_empty(tmp_path))
    why = keyset.refusal_if_enforcing_empty(
        ks, enforcing=True, service="eventbridge",
        path="/keys/keyset.json", env_var="EB_VERIFY_KEYSET_PATH")
    assert "EB_VERIFY_KEYSET_PATH" in why
    assert "/keys/keyset.json" in why
    assert "0 kid(s)" in why
    assert "[eventbridge]" in why


def test_audit_only_mode_still_starts_on_a_zero_kid_set(tmp_path):
    """Not enforcing means admit-and-report, which an empty set does coherently: it
    reports nothing as approved and blocks nothing. Refusing here would break a
    documented rollout step."""
    ks = keyset.load(_empty(tmp_path))
    assert keyset.refusal_if_enforcing_empty(
        ks, enforcing=False, service="eventbridge",
        path="p", env_var="EB_VERIFY_KEYSET_PATH") is None


def test_no_keyset_at_all_under_enforcement_is_left_alone(tmp_path):
    """`None` is the single-key path (`ER_VERIFY_KEY_PATH`), not a misconfiguration.
    Returning a refusal here would break every single-key deployment."""
    assert keyset.refusal_if_enforcing_empty(
        None, enforcing=True, service="eventrunner",
        path=None, env_var="ER_VERIFY_KEYSET_PATH") is None


def test_a_populated_set_under_enforcement_starts(tmp_path):
    """The configuration the control is for."""
    ks = keyset.load(_write(tmp_path, {"runner-01": PUB.hex()}))
    assert keyset.refusal_if_enforcing_empty(
        ks, enforcing=True, service="eventrunner",
        path="p", env_var="ER_VERIFY_KEYSET_PATH") is None


def test_an_empty_set_really_does_verify_nothing(tmp_path):
    """The premise, asserted rather than assumed — this is why the refusal is right.
    Without it the fix is a policy claim; with it, the policy is derived."""
    ks = keyset.load(_empty(tmp_path))
    assert ks.select(None) is None
    assert ks.select("runner-01") is None
    e = _event()
    S.sign_into(e, SEED, kid="runner-01")
    ok, why, _ = S.response_decision(e, ks, require=True)
    assert not ok, "a 0-kid set must not admit a correctly signed event"


def test_both_services_actually_call_the_refusal_and_exit_on_it():
    """The guard the other tests cannot provide. `refusal_if_enforcing_empty` returning
    a string proves nothing if no service calls it, or calls it and drops the result —
    which is the exact shape of the bug this fixes: a check that verified an intent
    rather than a state. Mutation-checked both ways: removing either the call or the
    `raise` fails this.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    for svc, env_var in (("eventbridge", "EB_VERIFY_KEYSET_PATH"),
                         ("eventrunner", "ER_VERIFY_KEYSET_PATH")):
        src = (root / svc / "__main__.py").read_text()
        tree = ast.parse(src)
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "refusal_if_enforcing_empty"
        ]
        assert len(calls) == 1, f"{svc} must call the refusal exactly once"

        # The call has to be inside a statement that exits, not merely evaluated.
        exits = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and any(isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "refusal_if_enforcing_empty"
                    for c in ast.walk(n.test))
            and any(isinstance(s, ast.Raise) for s in ast.walk(n))
        ]
        assert exits, f"{svc} must raise on the refusal, not just compute it"
        assert env_var in src, f"{svc} must pass its own env var name"
