"""`k8s_demo_flow.py`'s overlay wiring and the forgery it publishes. eventing-lab#2.

The stages themselves need a cluster, but three things that decide whether the demo
proves anything do not — and one of them was silently wrong before this:

  * which overlay runs in mock mode (`self.mock`)
  * which overlay runs the signed beats (`self.signed_beats`)
  * whether the forged event the demo publishes is the event the consumer refuses

The third is the one worth a test rather than a comment. A forgery that is malformed
would be refused for the wrong reason — `from_kafka_binary` would simply fail to decode
it — and the demo would appear to pass while proving nothing about the signature check.
"""
from __future__ import annotations

import binascii
import json
import sys

import k8s_demo_flow as flow

from shared import ce, keyset
from shared import signing as S

SEED_EB = binascii.unhexlify(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
SEED_R1 = binascii.unhexlify(
    "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")


def _flow(overlay: str, *extra):
    """A Flow with no Kubectl and no Checks — only the constructor's own decisions.

    Calls the REAL `Flow.__init__` rather than recomputing `mock`/`signed_beats`
    here. An earlier version of this helper duplicated those two expressions, so it
    was asserting a copy of the logic and not the logic: the `signed_beats` fix for
    `kind-signed-auth` landed in the constructor and these tests kept passing against
    the stale copy. That is §8.6's rule — call the real function; a reimplementation of
    the decision proves nothing about the decision.
    """
    args = flow.build_args(["--overlay", overlay, *extra])
    return flow.Flow(None, None, args)


# ---- which overlay means what ------------------------------------------------

def test_kind_signed_is_an_accepted_overlay():
    """`k8s_deploy.py` already accepted it and already required --signing-keys for it;
    the demo flow's `--overlay` was the one place the chain broke, so the overlay
    existed and nothing could drive it."""
    assert flow.build_args(["--overlay", "kind-signed"]).overlay == "kind-signed"


def test_only_the_demo_overlay_runs_a_real_agent():
    """The bug this fixes. `self.mock` read `overlay == "test"`, but only the `demo`
    overlay sets `ER_MOCK_CLAUDE=false` — so `kind` and `kind-signed` run mocked while
    the flow asserted a real-agent reply.

    Worse than a clean failure: the mock reply is `MOCK-REPLY[<prompt>]` and the prompt
    contains the text stage 2 looks for, so the assertion could pass by accident.
    """
    assert _flow("test").mock is True
    assert _flow("kind").mock is True
    assert _flow("kind-signed").mock is True
    assert _flow("demo").mock is False


def test_the_signed_beats_default_on_only_for_the_signed_overlay():
    """§8.5.2 — state the default. `kind-signed` exists for nothing else, and the beats
    would fail on an overlay that does not enforce."""
    assert _flow("kind-signed").signed_beats is True
    assert _flow("test").signed_beats is False
    assert _flow("kind").signed_beats is False
    assert _flow("demo").signed_beats is False


def test_the_signed_beats_can_be_forced_either_way():
    """So the forgery can be demonstrated against any enforcing deployment, and skipped
    on `kind-signed` when only the scaling beats are wanted."""
    assert _flow("kind", "--signed-beats").signed_beats is True
    assert _flow("kind-signed", "--no-signed-beats").signed_beats is False


# ---- the forgery is the real thing ------------------------------------------

def _run_forge(topic="kev1-responses", gid="test-group-0001",
               corr="test-agent-0001", bootstrap="broker:9092", seq=9001):
    """Execute FORGE_SRC with a fake KafkaProducer, returning what it published.

    The source is run rather than parsed, because the thing under test is that the
    events it builds decode and get refused — which only executing it can show.
    """
    sent: list[tuple] = []

    class _Producer:
        def __init__(self, **_kw):
            pass

        def send(self, topic, *, key, value, headers):
            sent.append((topic, key, value, headers))

        def flush(self, timeout=None):
            pass

    src = flow.FORGE_SRC % {"topic": topic, "gid": gid, "corr": corr,
                            "bootstrap": bootstrap, "seq": seq}
    fake_kafka = type(sys)("kafka")
    fake_kafka.KafkaProducer = _Producer
    saved = sys.modules.get("kafka")
    sys.modules["kafka"] = fake_kafka
    try:
        exec(compile(src, "<forge>", "exec"), {"__name__": "__forge__"})
    finally:
        if saved is not None:
            sys.modules["kafka"] = saved
        else:
            sys.modules.pop("kafka", None)
    return sent


def test_the_forgery_publishes_both_events_onto_the_response_topic():
    sent = _run_forge()
    assert len(sent) == 2, "a member terminal and a group.completed"
    assert {t for t, _k, _v, _h in sent} == {"kev1-responses"}
    # Keyed so the member lands on its correlation's partition, as a genuine
    # publisher's would — ordering per correlation is the Phase 0 contract.
    assert sent[0][1] == b"test-agent-0001"
    assert sent[1][1] == b"test-group-0001"


def test_the_forged_events_decode_as_well_formed_cloudevents():
    """If they did not, `from_kafka_binary` would fail before any signature check and
    the demo would be refusing a malformed message rather than an unsigned one."""
    for topic, _key, value, headers in _run_forge():
        evt = ce.from_kafka_binary([(k, v) for k, v in headers], value)
        assert evt.get("specversion") == "1.0"
        assert evt.get("source") == "rossoctl://attacker/forge"
        assert evt.get("groupid") == "test-group-0001"
        assert not evt.get("signature"), "an attacker has no approved key to sign with"


def test_the_forged_events_are_refused_by_the_real_decision_function():
    """The assertion that makes the beat worth demoing: these exact bytes are what
    `response_decision` refuses under enforcement — not a near-miss."""
    ks = keyset.KeySet({"eb-01": S.public_key(SEED_EB),
                        "runner-01": S.public_key(SEED_R1)})
    for _topic, _key, value, headers in _run_forge():
        evt = ce.from_kafka_binary([(k, v) for k, v in headers], value)
        ok, why, _verified = S.response_decision(evt, ks, require=True,
                                                 bridge_kid="eb-01")
        assert not ok, f"the forgery must be refused, got accept with {why!r}"
        assert "signature" in why


def test_the_forged_member_terminal_claims_to_be_terminal():
    """It has to be `final=true` to be refused at all: §4.4 passes an unsigned
    NON-terminal frame through by policy, because signing every stdout frame would add
    minutes to a chatty run. A forgery that forgot this would be accepted and the demo
    would assert the opposite of the control."""
    member = _run_forge()[0]
    evt = ce.from_kafka_binary([(k, v) for k, v in member[3]], member[2])
    assert str(evt.get("final")).lower() == "true"
    assert evt.get("phase") == "error"
    assert "Ship the goods" in str(evt.data)


def test_the_forged_terminal_does_not_reuse_the_genuine_runs_first_sequence():
    """Found in review, before the first cluster run: a forgery at `sequence=1` always
    collides, because `Emitter.next_seq` returns 1 for a genuine run's first event.

    examples#955's store rule then refuses the rejected row rather than storing it — the
    STRONGER outcome — but it leaves no row carrying `data.signature_rejected`, so the
    "the refusal is on record" assertion finds nothing and the beat fails for a reason
    that has nothing to do with signing. The default must therefore not collide.
    """
    member = _run_forge()[0]
    evt = ce.from_kafka_binary([(k, v) for k, v in member[3]], member[2])
    assert int(evt.get("sequence")) > 1, \
        "the default forgery must not land on a sequence the genuine run holds"


def test_the_collision_case_is_still_reachable_on_purpose():
    """It is the more interesting outcome, so it stays available — just not as the
    default, and with the assertion pointed at the surviving genuine row instead."""
    assert _flow("kind-signed").args.forge_collide is False
    assert _flow("kind-signed", "--forge-collide").args.forge_collide is True


# ---- the forged REQUEST: the same attack in the other direction --------------
#
# Different topic, different direction, different refusing function. The response
# forgery above forges an ANSWER to real work; this forges the WORK ITSELF, which is
# what the abstract's "the broker hands off events before signature check" names.

def _run_forge_request(topic="kev1-requests", corr="forged-0001",
                       bootstrap="broker:9092"):
    """Execute FORGE_REQUEST_SRC with a fake KafkaProducer. Same approach as
    `_run_forge`: run the attacker's program rather than parse it, because what is
    under test is that the bytes it produces are refused."""
    sent: list[tuple] = []

    class _Producer:
        def __init__(self, **_kw):
            pass

        def send(self, topic, *, key, value, headers):
            sent.append((topic, key, value, headers))

        def flush(self, timeout=None):
            pass

    src = flow.FORGE_REQUEST_SRC % {"topic": topic, "corr": corr,
                                    "bootstrap": bootstrap}
    fake_kafka = type(sys)("kafka")
    fake_kafka.KafkaProducer = _Producer
    saved = sys.modules.get("kafka")
    sys.modules["kafka"] = fake_kafka
    try:
        exec(compile(src, "<forge-request>", "exec"), {"__name__": "__forge__"})
    finally:
        if saved is not None:
            sys.modules["kafka"] = saved
        else:
            sys.modules.pop("kafka", None)
    return sent


def test_the_forged_request_lands_on_the_requests_topic():
    """The whole point of the beat is the topic. On the responses topic this would be
    the other forgery, refused by a different function."""
    sent = _run_forge_request()
    assert len(sent) == 1
    topic, key, _value, _headers = sent[0]
    assert topic == "kev1-requests"
    # Keyed by correlation id, as `publish_request` does — per-correlation FIFO is the
    # Phase 0 wire contract and an attacker wanting to be processed would honour it.
    assert key == b"forged-0001"


def test_the_forged_request_decodes_as_a_well_formed_request():
    """If it did not, `from_kafka_binary` would fail before any signature check and the
    demo would be refusing a malformed message rather than an unsigned one — proving
    nothing about the control."""
    _topic, _key, value, headers = _run_forge_request()[0]
    evt = ce.from_kafka_binary([(k, v) for k, v in headers], value)
    assert evt.get("specversion") == "1.0"
    assert evt.get("type") == ce.TYPE_REQUEST
    assert evt.get("source") == "rossoctl://attacker/forge"
    assert evt.get("correlationid") == "forged-0001"
    # The runner derives the session from the correlation id, so a forgery that got this
    # wrong would be refused by the router rather than by the signature check.
    assert evt.get("sessionuuid") == ce.session_uuid("forged-0001")
    assert not evt.get("signature"), "an attacker has no approved key to sign with"


def test_the_forged_request_is_refused_by_the_real_verify_function():
    """`verify_request` is what `consume.py` calls under ER_REQUIRE_SIGNATURE=true, so
    these exact bytes are what the runner refuses. Calling the real function rather than
    restating its rule is §8.6's first rule — a reimplementation of the guard proves
    nothing about the guard."""
    ks = keyset.KeySet({"eb-01": S.public_key(SEED_EB),
                        "runner-01": S.public_key(SEED_R1)})
    _topic, _key, value, headers = _run_forge_request()[0]
    evt = ce.from_kafka_binary([(k, v) for k, v in headers], value)
    ok, why = S.verify_request(evt, _RequireCfg(), ks)
    assert not ok, f"the forged request must be refused, got accept with {why!r}"


def test_a_GENUINELY_signed_request_is_accepted_by_the_same_function():
    """The positive control, and it is not optional.

    §8 observation 3: the first attempt at the group-forgery test reported no issue
    because it used the wrong event-type constants — a negative result from a harness
    that has never produced a positive is worth very little. So prove the harness can
    say yes: the same bytes, signed by an approved key, are accepted. Without this,
    `verify_request` returning False above could mean the keyset is empty, the codec
    changed, or the event is malformed, and the test would still pass.
    """
    ks = keyset.KeySet({"eb-01": S.public_key(SEED_EB),
                        "runner-01": S.public_key(SEED_R1)})
    _topic, _key, value, headers = _run_forge_request()[0]
    evt = ce.from_kafka_binary([(k, v) for k, v in headers], value)
    S.sign_into(evt, SEED_EB, "eb-01")
    ok, why = S.verify_request(evt, _RequireCfg(), ks)
    assert ok, f"a request signed by an approved key must be accepted, got {why!r}"


def test_signing_the_forged_request_with_an_unapproved_key_does_not_help():
    """The keyset is an allowlist, not a "was it signed at all" check. An attacker can
    always sign with a key they generated; what they cannot do is get its `kid` into the
    operator's approved set."""
    ks = keyset.KeySet({"eb-01": S.public_key(SEED_EB)})
    _topic, _key, value, headers = _run_forge_request()[0]
    evt = ce.from_kafka_binary([(k, v) for k, v in headers], value)
    S.sign_into(evt, SEED_R1, "attacker-01")
    ok, why = S.verify_request(evt, _RequireCfg(), ks)
    assert not ok
    assert "not in the approved key set" in why


def test_the_signature_binds_the_PROMPT_not_just_the_envelope():
    """A signature over attributes alone would let an attacker keep a genuine signed
    envelope and swap the prompt — i.e. make an approved submitter appear to have asked
    for something they never asked for. `datadigest` is what closes that, so assert it
    rather than trusting the docstring: re-encode a signed event with a different body,
    as an attacker on the wire would."""
    ks = keyset.KeySet({"eb-01": S.public_key(SEED_EB)})
    _topic, _key, value, headers = _run_forge_request()[0]
    evt = ce.from_kafka_binary([(k, v) for k, v in headers], value)
    S.sign_into(evt, SEED_EB, "eb-01")
    signed_headers, _ = ce.to_kafka_binary(evt)
    tampered = ce.from_kafka_binary(
        [(k, v) for k, v in signed_headers],
        json.dumps({"prompt": "something else entirely",
                    "model": None, "max_turns": 1}).encode())
    ok, why = S.verify_request(tampered, _RequireCfg(), ks)
    assert not ok, "a swapped payload under a genuine signature must be refused"
    assert "does not verify" in why


class _RequireCfg:
    """The attributes `verify_request` reads. A real `eventrunner.config.Cfg` would do,
    but importing it here would couple this test to unrelated config defaults."""
    require_signature = True
    verify_key_path = ""
    verify_keyset_path = "/etc/ce-keyset/agents.json"


# ---- the identity gate: who ASKED (eventing-lab#2 follow-on) -----------------
#
# The signed beats prove which approved KEY signed. Nothing in the flow proved which
# PERSON asked. `assert_identity_gate` does, and then shows the answer is
# tamper-evident. These cover the parts that decide whether the beat proves anything,
# none of which needs a cluster.

import os  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402
from pathlib import Path  # noqa: E402

from proclib import Checks  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOGIN = "mrsabath"
TOKEN = "ghp_demo_token_not_real"


def _flow_src() -> str:
    return (REPO / "scripts" / "k8s_demo_flow.py").read_text()


def _call_lines(src: str, name: str) -> list[int]:
    """Line numbers where `name` is CALLED — not defined, not named in a comment.

    Same helper, and same reason, as `tests/test_dockerfile_packaging.py`: an earlier
    guard there passed against code with the call deleted, because a comment and the
    `def` both matched a raw text search.
    """
    out = []
    for n, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(rf"(?<!def )\b{re.escape(name)}\s*\(", code):
            out.append(n)
    return out


class _Gate(BaseHTTPRequestHandler):
    """A fake EventBridge. Class attributes let each test bend one behaviour.

    `do_GET` answers `/v0/agents/<corr>/turns` — the ONLY route that carries
    `submitter` for a real deployment (`handlers.get_turns`, handlers.py:631). It
    used to answer with an `events.jsonl`-shaped line instead, which is a route that
    real EventBridge never produces `submitter` on at all (that field lives on the
    `prompts` table, not `responses`) — so every one of these tests was passing
    against a fixture that encoded a false belief about the system rather than its
    actual behaviour.
    """

    accept_any = False
    open_door = False
    submitter = LOGIN

    def do_POST(self):
        a = self.headers.get("Authorization")
        ok = self.open_door or (self.accept_any and a) or a == f"Bearer {TOKEN}"
        if ok:
            self.send_response(202)
            payload = b'{"correlationid":"brave-otter-4718"}'
        else:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="eventbridge"')
            payload = b'{"error":"authentication required"}'
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        body = {"turns": [{"turn_index": 0, "submitter": self.submitter}]}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


def _run_gate(handler_cls, *, token=TOKEN, login=LOGIN) -> Checks:
    """Drive the real `assert_identity_gate` against a fake bridge. Returns Checks.

    Calls the REAL `Flow.__init__` with real `build_args` output, for the reason
    `_flow` does: a hand-built `args` namespace would make these tests fail with
    AttributeError the day the stage reads one more argument, rather than report
    anything about the stage. Only `base` is overridden — `__init__` leaves it `""`
    and `stage0()` normally fills it in from the Route or `--public-url`.
    """
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Checks(prefix="test")
    args = flow.build_args(["--overlay", "kind-signed-auth", "--gh-login", login])
    f = flow.Flow(None, c, args)
    f.base = f"http://127.0.0.1:{srv.server_address[1]}"
    prior = os.environ.get("DEMO_GH_TOKEN")
    try:
        if token is None:
            os.environ.pop("DEMO_GH_TOKEN", None)
        else:
            os.environ["DEMO_GH_TOKEN"] = token
        f.assert_identity_gate()
    finally:
        srv.shutdown()
        if prior is None:
            os.environ.pop("DEMO_GH_TOKEN", None)
        else:
            os.environ["DEMO_GH_TOKEN"] = prior
    return c


def test_get_json_forwards_headers_and_is_unchanged_without_them():
    """The prerequisite. `get_json` had no `headers` parameter, so no stage could send
    an Authorization header at all."""
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.update(self.headers)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/x"
    try:
        status, _ = flow.get_json(url, headers={"Authorization": "Bearer abc"})
        assert status == 200
        assert seen.get("Authorization") == "Bearer abc"
        seen.clear()
        flow.get_json(url)
        assert "Authorization" not in seen
    finally:
        srv.shutdown()


def test_post_expecting_refusal_returns_the_refusal_instead_of_raising():
    """`urlopen` raises HTTPError on 4xx, and the 401's WWW-Authenticate header — half
    of what makes the refusal correct — is only reachable from the exception. A stage
    built on `get_json` would surface a refusal as a crash."""
    c = _run_gate(_Gate)
    assert c.failed == 0, c.failures
    code, headers, body = flow.post_expecting_refusal(
        "http://127.0.0.1:1/unreachable", body=b"{}")
    assert code is None, "no HTTP answer must be distinguishable from a refusal"
    assert headers == {} and body is None


def test_the_identity_gate_passes_against_a_correctly_configured_bridge():
    c = _run_gate(_Gate)
    assert c.failed == 0, c.failures
    assert c.passed == 9, f"expected 9 assertions, got {c.passed}"


def test_the_identity_gate_skips_without_a_token_rather_than_failing():
    """Absent `DEMO_GH_TOKEN` the beat is not runnable. A red check there would mean
    "you did not export a secret", which is not a finding about the system."""
    c = _run_gate(_Gate, token=None)
    assert (c.passed, c.failed) == (0, 0)


def test_the_identity_gate_never_logs_the_token(capsys):
    """The token is a live credential. It must not reach a terminal transcript that
    gets pasted into an issue — the same rule `gen_signing_keys` follows for seeds.

    Checks stdout/stderr as well as the recorded failures and notes: `Checks.log`/
    `ok`/`fail` all print, and a pasted terminal is the actual exposure.
    `failures`/`notes` alone cover the two places the token is LEAST likely to be."""
    c = _run_gate(_Gate)
    out = capsys.readouterr()
    assert TOKEN not in out.out, "token reached stdout"
    assert TOKEN not in out.err, "token reached stderr"
    assert TOKEN not in " ".join(c.failures)
    for key, val in (c.notes or {}).items():
        assert TOKEN not in f"{key}{val}", f"token leaked into note {key!r}"


def test_the_identity_gate_fails_when_authentication_is_off():
    """The case that matters most: an open door must not read as a pass."""
    class Open(_Gate):
        open_door = True
    c = _run_gate(Open)
    assert c.failed >= 1, "an unauthenticated POST that succeeds must fail the beat"


def test_the_identity_gate_fails_when_any_bearer_token_is_accepted():
    """The subtle one. A gate that merely checks for a non-empty Authorization header
    would refuse the no-credential request and accept a garbage token — passing a
    naive beat. This is why the stage sends a deliberately invalid token."""
    class AcceptAny(_Gate):
        accept_any = True
    c = _run_gate(AcceptAny)
    assert c.failed >= 1, "accepting an unrecognised token must fail the beat"


def test_the_identity_gate_fails_when_the_submitter_is_not_stamped():
    """Authentication without attribution is half the beat: if the event does not
    carry who asked, the downstream allowlist has nothing trustworthy to check."""
    class NoSub(_Gate):
        submitter = ""
    c = _run_gate(NoSub)
    assert c.failed >= 1


def test_submitter_and_issuer_are_covered_by_the_signature():
    """The premise the beat rests on, asserted against the real tuple rather than
    quoted from a comment. If these left SIGNED_ATTRS, a forger could relabel who
    asked without breaking the signature and the beat would be narrating a control
    that no longer exists."""
    assert "submitter" in S.SIGNED_ATTRS
    assert "submitteriss" in S.SIGNED_ATTRS


def test_the_signed_beats_default_on_for_the_auth_overlay_too():
    """The bug this caught before it shipped. `signed_beats` read
    `overlay == "kind-signed"`, so `kind-signed-auth` defaulted the beats OFF — every
    signed assertion, including the identity gate that overlay exists for, would have
    been skipped while the flow still reported success."""
    assert _flow("kind-signed-auth").signed_beats is True
    assert _flow("kind-signed").signed_beats is True
    assert _flow("kind").signed_beats is False
    assert _flow("kind-signed-auth", "--no-signed-beats").signed_beats is False


def test_kind_signed_auth_is_an_accepted_overlay_everywhere_it_must_be():
    """An overlay the deploy accepts but the flow rejects is the gap that left
    `kind-signed` undriveable for a whole phase."""
    assert flow.build_args(
        ["--overlay", "kind-signed-auth"]).overlay == "kind-signed-auth"
    for script in ("k8s_deploy.py", "k8s_preflight.py"):
        src = (REPO / "scripts" / script).read_text()
        assert "kind-signed-auth" in src, f"{script} does not accept the overlay"


def test_the_identity_gate_is_called_from_main():
    """The wiring guard. A stage nothing dispatches is the shape of the bugs the
    cluster run found. Mutation-checked: removing the call from `main()` fails this."""
    src = _flow_src()
    assert _call_lines(src, "f.assert_identity_gate"), \
        "assert_identity_gate is defined but never called from main()"
    assert hasattr(flow.Flow, "assert_identity_gate")


def test_the_token_is_read_from_the_environment_not_from_argv():
    """A credential on the command line reaches `ps` and shell history. The flag
    carries only the LOGIN, which is public."""
    src = _flow_src()
    assert 'os.environ.get("DEMO_GH_TOKEN"' in src
    assert "--demo-gh-token" not in src and "--gh-token" not in src, \
        "the token must not be an argparse flag"
