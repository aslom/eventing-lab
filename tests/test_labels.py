"""Agent labels: tag a run, then resolve the tag to its latest correlationid.

The feature exists to delete a copy-paste step from the middle of a demo — the operator
tags a run and asks for the tag back, instead of reading a correlationid off the screen.
So the tests that matter most are the ones about *which* run a label resolves to, and the
two that pin deliberate deviations from the code they were modelled on:

* `test_latest_is_most_recently_started_not_most_recently_touched` — ordering is on
  `created_utc`, so a `/continue` on an older labelled run cannot make it win.
* `test_the_lookup_is_authenticated_even_in_single_tenant_mode` — unlike `list_groups`,
  this route authenticates wherever any auth is configured, because a label is a
  *guessable* string and `/continue` is open by design.
"""
import io
import json
import pathlib

import pytest

from eventbridge.config import Cfg as EbCfg
from eventbridge.handlers import LABEL_REGEX, Handlers
from eventbridge.store import Store


class FakeProducer:
    def __init__(self):
        self.requests = []

    def publish_request(self, **kw):
        self.requests.append(kw)
        return f"evt-{len(self.requests)}"


class SeqMinter:
    def __init__(self):
        self.n = 0

    def mint(self):
        self.n += 1
        return f"test-agent-{self.n:04d}"


def _call(handler, *, body=None, method="GET", qs="", **kw):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    raw = json.dumps(body).encode() if body is not None else b""
    environ = {"wsgi.input": io.BytesIO(raw),
               "CONTENT_LENGTH": str(len(raw)) if raw else "",
               "QUERY_STRING": qs, "REQUEST_METHOD": method}
    environ.update(kw.pop("environ", {}))
    out = handler(environ, start_response, **kw)
    return captured["status"], b"".join(out)


@pytest.fixture
def api(tmp_path: pathlib.Path):
    store = Store(tmp_path / "eb")
    cfg = EbCfg(tmpdir=str(tmp_path), public_base_url="http://eb.test")
    return Handlers(cfg, store, FakeProducer(), SeqMinter()), store


# ---- the regex ---------------------------------------------------------------

@pytest.mark.parametrize("label", ["incident", "demo-1b", "a", "A1._-ok", "x" * 64])
def test_a_usable_label_is_accepted(label):
    assert LABEL_REGEX.match(label)


@pytest.mark.parametrize("label", [
    "",            # empty
    "-leading",    # would be eaten as a CLI flag
    ".leading",    # first char must be alphanumeric
    "has space",   # needs quoting in a shell
    "sl/ash",      # path-significant
    "pct%20",      # already encoded
    "quo'te",      # quoting puzzle
    "x" * 65,      # over the bound
])
def test_a_label_that_would_need_escaping_is_rejected(label):
    """The set is what is safe unencoded in a URL and unquoted in a shell. A label that
    needs either defeats the `CORR=$(… latest NAME)` form this exists for."""
    assert not LABEL_REGEX.match(label)


# ---- POST /v0/agents ---------------------------------------------------------

def test_a_label_is_stored_and_echoed(api):
    h, store = api
    status, body = _call(h.start_agent, method="POST",
                         body={"prompt": "hi", "label": "incident"})
    assert status.startswith("202")
    out = json.loads(body)
    assert out["label"] == "incident"
    assert store.get_session(out["correlationid"])["label"] == "incident"


def test_a_run_without_a_label_stores_null_not_empty_string(api):
    """NULL is the truthful "no label"; '' would make an empty-label lookup match every
    unlabelled run."""
    h, store = api
    _, body = _call(h.start_agent, method="POST", body={"prompt": "hi"})
    corr = json.loads(body)["correlationid"]
    assert store.get_session(corr)["label"] is None
    assert store.latest_for_label("") is None


def test_a_bad_label_is_refused_before_anything_is_written(api):
    """Every refusal precedes the first write, per the comment in `start_agent`: a 400
    after the session row would leave a transcript for a turn that never ran."""
    h, store = api
    status, body = _call(h.start_agent, method="POST",
                         body={"prompt": "hi", "label": "has space"})
    assert status.startswith("400")
    assert json.loads(body)["error"] == "bad label"
    assert store.all_correlations() == []


# ---- GET /v0/agents?label= ---------------------------------------------------

def test_a_label_resolves_to_its_correlationid(api):
    h, _ = api
    _, body = _call(h.start_agent, method="POST",
                    body={"prompt": "hi", "label": "incident"})
    corr = json.loads(body)["correlationid"]

    status, body = _call(h.find_agent, qs="label=incident")
    assert status.startswith("200")
    out = json.loads(body)
    assert out["correlationid"] == corr
    assert out["label"] == "incident"
    assert out["html_url"] == f"http://eb.test/v0/agents/{corr}"


def test_the_newest_run_wins_a_label_collision(api):
    """Latest wins silently: a label is bookkeeping, not a unique key, so re-rehearsing a
    beat with the same tag must not need a cleanup step between takes."""
    h, _ = api
    corrs = []
    for _ in range(3):
        _, body = _call(h.start_agent, method="POST",
                        body={"prompt": "hi", "label": "incident"})
        corrs.append(json.loads(body)["correlationid"])

    _, body = _call(h.find_agent, qs="label=incident")
    assert json.loads(body)["correlationid"] == corrs[-1]


def test_latest_is_most_recently_started_not_most_recently_touched(api):
    """The reason the query orders on `created_utc`: `upsert_session`'s UPDATE bumps
    `updated_utc` on every `/continue`, so ordering on that column would let a resumed
    older run outrank a newer one and "latest" would stop meaning "the run I just
    started"."""
    h, store = api
    _, body = _call(h.start_agent, method="POST",
                    body={"prompt": "one", "label": "incident"})
    older = json.loads(body)["correlationid"]
    _, body = _call(h.start_agent, method="POST",
                    body={"prompt": "two", "label": "incident"})
    newer = json.loads(body)["correlationid"]

    # The `/continue` shape: four positionals, no label.
    sess = store.get_session(older)
    store.upsert_session(older, sess["sessionuuid"], sess["workdir"], None)

    _, body = _call(h.find_agent, qs="label=incident")
    assert json.loads(body)["correlationid"] == newer


def test_a_continue_does_not_wipe_the_label(api):
    """`label` is absent from the UPDATE's SET list, so no caller can clear it by
    resuming. Pinned at the HTTP level as well as the store level."""
    h, store = api
    _, body = _call(h.start_agent, method="POST",
                    body={"prompt": "hi", "label": "incident"})
    corr = json.loads(body)["correlationid"]
    sess = store.get_session(corr)
    store.upsert_session(corr, sess["sessionuuid"], sess["workdir"], None)

    status, body = _call(h.find_agent, qs="label=incident")
    assert status.startswith("200")
    assert json.loads(body)["correlationid"] == corr
    assert store.get_session(corr)["turns"] == 1


def test_a_missing_label_parameter_is_a_400_not_a_listing(api):
    """There is deliberately no bare agent list. Phase 2 §8.2 files `GET /v0/groups`
    publishing correlation ids as an open bug (eventing-lab#1) because §3.1's
    capability-URL argument rests on those ids being unguessable while `/continue` is
    open; an agent list would be that bug with every row a continuable conversation."""
    h, _ = api
    _call(h.start_agent, method="POST", body={"prompt": "hi", "label": "incident"})
    status, body = _call(h.find_agent, qs="")
    assert status.startswith("400")
    err = json.loads(body)["error"]
    assert "label required" in err
    # Nothing resembling a correlation id leaks into the refusal.
    assert "test-agent" not in err


def test_an_unknown_label_is_a_404(api):
    h, _ = api
    status, body = _call(h.find_agent, qs="label=nope")
    assert status.startswith("404")
    assert "no agent with label" in json.loads(body)["error"]


def test_a_malformed_label_in_the_query_is_a_400(api):
    h, _ = api
    status, body = _call(h.find_agent, qs="label=has%20space")
    assert status.startswith("400")
    assert json.loads(body)["error"] == "bad label"


def test_the_lookup_is_authenticated_even_in_single_tenant_mode():
    """The deliberate deviation from `list_groups`, pinned so nobody "fixes" it back.

    `list_groups` resolves a caller only to choose a store, so on a single-tenant
    deployment WITH auth — which is what the demo cluster is — that shape would leave
    this route open. A label is a guessable human string, so an open lookup is worse
    than a published correlationid: the word "demo" would hand over a continuable
    conversation.
    """
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    store = Store(tmp / "eb")
    cfg = EbCfg(tmpdir=str(tmp), public_base_url="http://eb.test",
                auth_tokens={"s3cret": "alice"})
    h = Handlers(cfg, store, FakeProducer(), SeqMinter())

    status, _ = _call(h.find_agent, qs="label=incident")
    assert status.startswith("401")

    # With the credential, the same request is answered (404 — nothing stored yet),
    # which is what shows the 401 above came from auth and not from the lookup.
    status, _ = _call(h.find_agent, qs="label=incident",
                      environ={"HTTP_AUTHORIZATION": "Bearer s3cret"})
    assert status.startswith("404")


# ---- ADR-0003: the label on the wire ----------------------------------------

def test_the_label_is_covered_by_the_signature():
    """The premise ADR-0003 rests on, asserted against the real tuple rather than
    quoted from a comment.

    ADR-0001 kept the label out of events entirely BECAUSE an unsigned one would be
    mutable in flight, and `kafka_requests_mirror` rebuilds session rows from topic
    data. If `label` ever left SIGNED_ATTRS while staying on the event, anything with
    topic write access could point a label lookup at its own correlationid — a
    guessable string resolving to a capability someone else steers. That is strictly
    worse than the store-only design it replaced, so this assertion is the thing
    standing between the two.
    """
    from shared import signing as S
    assert "label" in S.SIGNED_ATTRS


def test_rewriting_the_label_breaks_the_signature():
    """The control itself, exercised rather than asserted about.

    A forger who relabels a run to hijack `--label` in the audit must fail
    verification, not merely be impolite.
    """
    from shared import audit, ce, keyset, signing
    seed = bytes(range(32))
    ev = ce.new_event(type=ce.TYPE_REQUEST, source="s",
                      correlationid="test-otter-0003", sequence=1, mode="start",
                      data={"prompt": "x"}, **{ce.EXT_LABEL: "incident"})
    ev.attrs["signature"] = signing.sign_event(ev, seed, kid="k1")
    ks = keyset.KeySet({"k1": signing.public_key(seed)})

    assert audit.row_for(ev, 1, ks).verdict == audit.VERDICT_OK

    forged = ce.CloudEvent(attrs=dict(ev.attrs), data=ev.data)
    forged.attrs[ce.EXT_LABEL] = "attacker-chosen"
    row = audit.row_for(forged, 2, ks)
    assert row.verdict == audit.VERDICT_BAD, (
        "a rewritten label must break the signature, or --label in the audit is "
        "steerable by anyone with topic write access")


def test_the_label_reaches_the_published_event(api):
    """End to end: a labelled POST puts `ce_label` on the wake event, which is what
    lets `audit_topic.py --label` work without touching this service's store."""
    h, _store = api
    status, _ = _call(h.start_agent, method="POST",
                      body={"prompt": "hi", "label": "incident"})
    assert status.startswith("202")
    published = h.producer.requests[-1]
    assert published["label"] == "incident"


def test_an_unlabelled_run_publishes_no_label_attribute(api):
    """Absent, not empty. `to_kafka_binary` drops empty attributes, and an event
    carrying `ce_label=""` would canonicalise differently from one carrying none —
    which is the kind of difference that makes two versions disagree about a
    signature."""
    h, _store = api
    _call(h.start_agent, method="POST", body={"prompt": "hi"})
    assert h.producer.requests[-1]["label"] is None


def test_the_audit_row_exposes_the_label(api):
    """`audit.Row.label` is what `--label` keys on."""
    from shared import audit, ce, keyset, signing
    seed = bytes(range(32))
    ev = ce.new_event(type=ce.TYPE_REQUEST, source="s",
                      correlationid="test-otter-0004", sequence=1, mode="start",
                      data={"prompt": "x"}, **{ce.EXT_LABEL: "kubecon-100"})
    ev.attrs["signature"] = signing.sign_event(ev, seed, kid="k1")
    ks = keyset.KeySet({"k1": signing.public_key(seed)})
    assert audit.row_for(ev, 1, ks).label == "kubecon-100"


def test_label_resolution_follows_a_run_to_its_responses():
    """`--label` must resolve to correlation ids, not match rows on `ce_label`.

    The label rides on the WAKE event only — responses carry `correlationid` and
    `causationid`. Matching on the label alone would show the request and hide every
    answer to it, which is the opposite of the intent.
    """
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import audit_topic

    from shared import audit

    def row(off, corr, label=""):
        return audit.Row(offset=off, event_id=f"id{off}", type="t", correlationid=corr,
                         groupid="", sequence="1", phase="result", final=False,
                         kid="k", verdict=audit.VERDICT_OK, reason="", causationid="",
                         label=label)

    reqs = [row(1, "aaa-bbb-0001", "incident"),
            row(2, "ccc-ddd-0002", "other"),
            row(3, "eee-fff-0003", "incident")]
    found = audit_topic.corrs_for_labels(reqs, ["incident"])
    assert found == ["aaa-bbb-0001", "eee-fff-0003"]
    # And the id filter then catches the RESPONSES, which carry no label at all.
    resp = row(9, "aaa-bbb-0001")
    assert audit_topic.row_matches(resp, found)


def test_comma_separated_labels_are_flattened():
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import audit_topic
    assert audit_topic.ids_from(["a,b", "c"]) == ["a", "b", "c"]


def test_an_open_deployment_still_resolves_labels():
    """With no auth configured, `resolve` returns allowed-anonymous (§2.4) — the thing
    that makes the laptop demo work out of the box. The gate above must not change that."""
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp())
    store = Store(tmp / "eb")
    cfg = EbCfg(tmpdir=str(tmp), public_base_url="http://eb.test")
    h = Handlers(cfg, store, FakeProducer(), SeqMinter())
    _, body = _call(h.start_agent, method="POST",
                    body={"prompt": "hi", "label": "incident"})
    corr = json.loads(body)["correlationid"]
    status, body = _call(h.find_agent, qs="label=incident")
    assert status.startswith("200")
    assert json.loads(body)["correlationid"] == corr


# ---- audit output: finding the id you asked about ---------------------------
#
# `--highlight` marked the right rows all along, but on a topic holding 24h of
# traffic (~1000 chains on ykt1) the marked chain rendered three screens above the
# summary, between `witty-osprey` and `witty-otter`. The operator's report was that
# the audit "prints lots of output but nothing about CORR_FORGED" — it had printed
# it, invisibly, which on stage is the same thing. Dimming 999 others does not help
# when they scroll past.

def _audit_topic():
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import audit_topic
    return audit_topic


def _row(off, corr, *, label="", final=False, verdict=None, caus=""):
    from shared import audit
    return audit.Row(offset=off, event_id=f"id{off}", type="t.request",
                     correlationid=corr, groupid="", sequence="1", phase="result",
                     final=final, kid="k", verdict=verdict or audit.VERDICT_OK,
                     reason="", causationid=caus, label=label)


def test_highlighted_chains_sort_before_everything_else():
    """The ordering fix. A marked chain must not be buried among a thousand others."""
    at = _audit_topic()
    from shared import audit
    noise = [audit.Chain(request_id=f"r{i}", correlationid=f"witty-otter-{i:04d}",
                         request_verdict=audit.VERDICT_OK) for i in range(50)]
    mine = audit.Chain(request_id="rX", correlationid="forged-999",
                       request_verdict=audit.VERDICT_OK)
    out = at.render_chains(noise + [mine], highlight=["forged-999"])
    # Match on "wake " after stripping the marker: a highlighted row's leading
    # space is REPLACED by ► (there is no colour in a test), so an indentation-based
    # filter silently skips the very row under test. The header line reads
    # "result → wake binding", hence the lstrip-then-startswith rather than `in`.
    body = [ln for ln in out.splitlines()
            if ln.lstrip("► ").startswith("wake ")]
    assert "forged-999" in body[0], (
        f"the highlighted chain must be first, got {body[0]!r}")
    assert "1 highlighted chain(s) first" in out


def test_the_focus_block_restates_each_highlighted_id_with_its_verdicts():
    """The summary is where the eye lands when the command ends, so the answer to
    'what happened to THIS id?' belongs there — not only thousands of lines above."""
    at = _audit_topic()
    from shared import audit
    rows = [_row(1, "forged-999"),
            _row(2, "forged-999", verdict=audit.VERDICT_EXEMPT),
            _row(3, "someone-else-0001")]
    out = at.render_focus(["forged-999"], rows, [])
    assert "forged-999" in out
    assert "2 event(s)" in out
    assert f"{audit.VERDICT_OK}=1" in out and f"{audit.VERDICT_EXEMPT}=1" in out
    # Scoped to the id asked about.
    assert "someone-else-0001" not in out


def test_the_focus_block_says_so_when_an_id_matched_nothing():
    """A typo'd id, an aged-out run and a correct filter all yield zero rows.
    Silence would read as the third, which is the quiet failure worth naming."""
    at = _audit_topic()
    out = at.render_focus(["nope-0000"], [_row(1, "other-0001")], [])
    assert "nope-0000" in out
    assert "NO EVENTS" in out and "retention" in out


def test_the_focus_block_reports_the_chain_state_for_a_highlighted_run():
    """Part 3's beat: the forged wake is OPEN — a question nobody answered."""
    at = _audit_topic()
    from shared import audit
    req = _row(1, "forged-999")
    ch = audit.Chain(request_id="id1", correlationid="forged-999",
                     request_verdict=audit.VERDICT_OK)
    out = at.render_focus(["forged-999"], [req], [ch])
    assert "OPEN" in out
    assert "no terminal yet" in out
