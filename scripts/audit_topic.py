#!/usr/bin/env python3
"""List the events in a topic, say which are signed, and chain results to their wakes.

R4 of the KubeCon demo list — "the event broker log as tamper-evident audit". The
abstract claims a completion can be bound back to the signed wake event that caused it;
this prints that, per event, against a real topic.

  python3 scripts/audit_topic.py                            # both topics, in-cluster
  python3 scripts/audit_topic.py --topic kev1-responses
  python3 scripts/audit_topic.py --chains                   # the binding, not the list
  python3 scripts/audit_topic.py --highlight sweet-crab-0001,forged-123
  python3 scripts/audit_topic.py --corr sweet-crab-0001     # ONLY that run
  python3 scripts/audit_topic.py --local --bootstrap localhost:9092 \
      --keyset ~/.eventing-keys/agents.json                 # laptop Kafka

**Where it runs.** By default it runs itself inside a throwaway pod built on the
eventrunner image, because that image already has `shared/`, `kafka-python` and the
keyset ConfigMap mounted — so there is no port-forward, nothing to install, and the
keyset it verifies against is literally the one the deployment uses. `--local` skips that
and talks to a broker directly, for a laptop Kafka.

**What the verdicts mean**, and the three limits the output states rather than implies:

    signed-ok     a signature verified against the approved key set
    signed-BAD    a signature was present and did NOT verify — a forgery, or a key that
                  is no longer approved
    unsigned      no signature on an event that policy says should have one (a terminal,
                  or a group lifecycle event)
    unsigned-ok   no signature on a streamed non-terminal, which is CORRECT: `emit()`
                  signs terminals only, because ~222 ms per signature would add minutes
                  to a chatty run
    no-keyset     nothing to check against; the tool is listing, not auditing

  * A signature proves the signer held an approved key. It does not make the content
    true. What it does bind is `causationid`, which is in `SIGNED_ATTRS`, so the LINK
    between a result and its wake event cannot be rewritten in flight.
  * A topic written before signing was enabled is entirely unsigned, and no signature
    can distinguish that from events an attacker wrote. Both read `unsigned`. That is
    the honest answer, not a gap in the tool.
  * Reading a topic mid-run shows chains in state `open`. That is not a finding either.

**Narrowing the output: `--highlight` vs `--corr`.** Both take correlation ids OR group
ids, repeatable and comma-separated (`--highlight a,b --highlight c`). They are not
interchangeable, and the difference is the one this tool exists to protect:

    --highlight   marks those ids and dims the rest. NOTHING is dropped.
    --corr        shows ONLY those ids. Everything else is hidden, including events an
                  attacker wrote — so it also prints a SCOPE line with the count it hid.

Prefer `--highlight` whenever anyone is watching. An audit filtered to the run you meant
to discuss will read `signed-BAD 0` and look like a clean topic, which is exactly the
misreading the verdict vocabulary above is built to prevent. `--corr` is for a busy topic
where you genuinely want one run's events and will read the SCOPE line.

Colour needs a tty, and the in-cluster pod's stdout is a pipe, so matches are marked `►`
there instead of bolded. `--no-color` forces that form anywhere.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# `proclib` only: this module re-executes ITSELF inside a pod on the eventrunner
# image, and that image carries `shared/` and `eventrunner/` but NOT `scripts/`
# (Dockerfile-eventrunner). So every scripts/-local import has to be reachable in
# both places, and `audit_in_cluster` ships proclib.py into /tmp beside this file.
#
# `imagelib` and `k8slib` are imported lazily inside `audit_in_cluster` instead,
# because only the HOST side needs them — a module-level import of either fails in
# the pod with ModuleNotFoundError before argparse ever runs.
from proclib import Checks, die  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from shared import audit, ce, keyset  # noqa: E402

PREFIX = "audit"

_STATE_NOTE = {
    "bound": "wake and result both verified",
    "result-only": "the result verified; its wake event did not",
    "contested": "a verified answer AND a forgery are both on the topic",
    "unverified": "a terminal exists and did not verify",
    "open": "no terminal yet — still running, or read too early",
    "orphaned": "results naming a wake event that is not on this topic",
    "unknown": "no keyset: nothing was checked",
}


def _image_default(which: str) -> str | None:
    """`imagelib`'s registry/tag default on the host, None inside the pod.

    The pod has no `scripts/`, so this import cannot succeed there — and it does not
    need to: the in-pod invocation passes `--local` and never builds an image ref.
    Returning None keeps argparse working in both places without pinning a copy of
    the registry name in a second file, which would be a value to keep in sync.
    """
    try:
        import imagelib
    except ModuleNotFoundError:
        return None
    return {"registry": imagelib.DEFAULT_REGISTRY,
            "tag": imagelib.DEFAULT_TAG}[which]


def _progress(msg: str, *, done: bool = False) -> None:
    """One self-overwriting progress line on STDERR.

    stderr, never stdout: the audit's stdout is its OUTPUT, and `… | tail -8` is in
    the demo script. Progress in that pipe would push real rows out of it.

    `\r` with no newline until `done`, so a long read occupies one line instead of
    scrolling the rows away. A non-tty (the in-cluster pod's stderr is a pipe) gets
    plain lines, because `\r` in a captured log renders as one unreadable smear —
    which is how the pod's progress reached the operator before this existed.
    """
    tty = sys.stderr.isatty()
    end = "\n" if (done or not tty) else ""
    pad = " " * 12 if tty else ""
    print(f"{chr(13) if tty else ''}[{PREFIX}] {msg}{pad}",
          end=end, file=sys.stderr, flush=True)


def read_topic(bootstrap: str, topic: str, *, limit: int, timeout_ms: int):
    """Consume a topic from the beginning. Returns [(offset, CloudEvent)].

    No consumer group and no commit: this is a reader, and joining the live group would
    both wait for a rebalance and move the offsets the live consumer owns.

    Reports progress while consuming. This is where the time actually goes once the
    native verifier is in play: classification of 619 events is milliseconds, the read
    is seconds to tens of seconds, and it was the one phase that printed nothing. A
    tool silent for 30s is indistinguishable from a hung one, and on ykt1 the usual
    cause of a real hang — a dropped VPN — looks exactly like a slow read.
    """
    from kafka import KafkaConsumer, TopicPartition
    _progress(f"{topic}: connecting to {bootstrap}")
    c = KafkaConsumer(bootstrap_servers=bootstrap, enable_auto_commit=False,
                      group_id=None, consumer_timeout_ms=timeout_ms)
    try:
        parts = c.partitions_for_topic(topic)
        if not parts:
            _progress(f"{topic}: no such topic, or no partitions", done=True)
            return []
        tps = [TopicPartition(topic, p) for p in sorted(parts)]
        c.assign(tps)
        for tp in tps:
            c.seek_to_beginning(tp)
        # The end offsets give a DENOMINATOR, so progress is "n of N" rather than a
        # number that climbs with no idea where it stops. Best-effort: an older broker
        # or a permissions edge can refuse it, and a read that works must not fail
        # because its progress bar could not be labelled.
        try:
            ends = c.end_offsets(tps)
            begins = c.beginning_offsets(tps)
            expect = min(sum(ends[t] - begins[t] for t in tps), limit)
        except Exception:  # noqa: BLE001
            expect = 0
        _progress(f"{topic}: {len(tps)} partition(s), ~{expect or '?'} event(s) to read")
        out = []
        last = time.monotonic()
        for rec in c:
            try:
                out.append((rec.offset,
                            ce.from_kafka_binary(rec.headers or [], rec.value)))
            except Exception as e:  # noqa: BLE001 - a junk record must not stop the audit
                print(f"[{PREFIX}] offset {rec.offset}: undecodable ({e!r})")
            # Time-based, not count-based: the point is to prove liveness, and a count
            # tick says nothing during the stall between two slow batches.
            if time.monotonic() - last >= 0.5:
                _progress(f"{topic}: read {len(out)}"
                          + (f"/{expect}" if expect else "") + " event(s)")
                last = time.monotonic()
            if len(out) >= limit:
                break
        _progress(f"{topic}: read {len(out)} event(s)", done=True)
        return out
    finally:
        c.close()


_BOLD = "\033[1m"
_DIM  = "\033[2m"
_OFF  = "\033[0m"


def _colour_ok() -> bool:
    """Is ANSI styling usable on this stdout?

    Defined here rather than reusing `proclib._tty`: that is private to a module this
    file ships into a pod as source, and importing a private name across that boundary
    is how `NameError: name '_tty' is not defined` reached the cluster once already.
    In the pod stdout is a pipe, so this is False there and `--highlight` falls back to
    the ► marker.
    """
    return not os.environ.get("NO_COLOR") and sys.stdout.isatty()


def ids_from(values: list[str] | None) -> list[str]:
    """Flatten a repeatable, comma-separated id option into a list.

    `--corr a --corr b,c` and `--corr a,b,c` are the same thing. Comma-separated is what
    a demo actually types (one label lookup per id, pasted together); repeatable is what
    a script generates. Supporting only one of them would make the other a surprise.
    """
    out: list[str] = []
    for v in values or []:
        out.extend(part.strip() for part in v.split(",") if part.strip())
    return out


def corrs_for_labels(rows: list[audit.Row], labels: list[str]) -> list[str]:
    """Correlation ids whose REQUEST carried one of these labels (ADR-0003).

    Two-step on purpose, and the reason is the wire contract rather than convenience.
    `ce_label` is set by `publish_request`, so it rides on the WAKE event only — the
    responses an agent streams back carry `correlationid` and `causationid`, not the
    label. Matching rows on `ce_label` alone would therefore show the request and hide
    every answer to it, which is the opposite of what `--label` is for.

    So: read the labels off the requests topic, collect the correlation ids, and let the
    existing id filter do the rest. The label is only trustworthy enough to key on this
    way because it is in `signing.SIGNED_ATTRS` — an unsigned one could be rewritten in
    flight to pull another run's events into the view.
    """
    want = set(labels)
    return sorted({r.correlationid for r in rows
                   if r.label in want and r.correlationid})


def row_matches(r: audit.Row, ids: list[str]) -> bool:
    """Does this event belong to one of `ids`?

    Checks `correlationid` AND `groupid`, because a group's lifecycle events
    (`group.started` / `group.completed`) carry no correlationid at all — filtering a
    batch on its members' ids alone would silently drop the two events that say the batch
    began and ended. A group id is accepted here for the same reason `render_rows`
    already falls back to it when printing `who`.
    """
    return bool(ids) and (r.correlationid in ids or r.groupid in ids)


def render_rows(rows: list[audit.Row], title: str, *,
                highlight: list[str] | None = None, paint: bool = False) -> str:
    """One line per event. `highlight` bolds matching rows and dims the rest.

    Highlighting rather than filtering is the demo-safe default: nothing disappears, so
    the audit still shows every event an attacker wrote while the eye goes to the run
    being discussed. `--corr` is the filtering option, and §"what to filter" in `main`
    says when each is appropriate.
    """
    hl = highlight or []
    shown = sum(1 for r in rows if row_matches(r, hl)) if hl else 0
    head = f"\n{title} — {len(rows)} event(s)"
    if hl:
        head += f", {shown} highlighted"
    out = [head, "-" * 78]
    if not rows:
        out.append("  (empty)")
        return "\n".join(out)
    for r in rows:
        who = r.correlationid or r.groupid or "-"
        kind = (r.type or "").rsplit(".", 2)[-2] if "." in (r.type or "") else r.type
        flag = "T" if r.is_terminal else " "
        line = (f"  {r.offset:>6} {flag} {kind:<9} {who:<22} "
                f"seq={r.sequence or '-':<4} kid={(r.kid or '-'):<11} "
                f"{r.verdict}")
        if hl and paint:
            line = (f"{_BOLD}{line}{_OFF}" if row_matches(r, hl)
                    else f"{_DIM}{line}{_OFF}")
        elif hl:
            # No colour available (a pipe, NO_COLOR, or the in-cluster pod, whose stdout
            # is not a tty). A leading marker still separates the run under discussion
            # from the rest, which is the whole point of the option.
            line = ("►" + line[1:]) if row_matches(r, hl) else line
        out.append(line)
        if r.verdict in (audit.VERDICT_BAD, audit.VERDICT_UNSIGNED):
            out.append(f"         └─ {r.reason}")
    return "\n".join(out)


def render_chains(cs: list[audit.Chain], *,
                  highlight: list[str] | None = None, paint: bool = False) -> str:
    """The result→wake binding, one block per chain.

    HIGHLIGHTED CHAINS SORT FIRST, and that ordering is the fix for a real demo
    failure rather than a preference. With ~1000 runs on a 24h-retention topic,
    `--highlight $CORR_FORGED` did mark the forged chain — three screens above
    the summary, between `witty-osprey` and `witty-otter`, with nothing to draw
    the eye to it. The operator's report was that it "printed lots of output but
    nothing about CORR_FORGED"; it had printed it, invisibly, which on stage is
    the same thing. Dimming the other 999 does not help when they scroll past.

    Secondary order is unchanged (bound-first, then by id), so an unhighlighted
    run reads exactly as before.
    """
    hl = highlight or []
    out = ["\nresult → wake binding", "-" * 78]
    if not cs:
        out.append("  (nothing to bind)")
        return "\n".join(out)
    if hl:
        out.append(f"  {sum(1 for x in cs if x.correlationid in hl)} highlighted "
                   f"chain(s) first, then the rest")
    for c in sorted(cs, key=lambda x: (x.correlationid not in hl,
                                       x.state != "bound", x.correlationid)):
        label = (c.request_id[:8] if c.request_id else "(none)")
        on = bool(hl) and c.correlationid in hl
        head = (f"  wake {label}  {c.correlationid or '-':<22} "
                f"{len(c.results):>2} result(s)  {c.state.upper()}")
        if hl and paint:
            head = f"{_BOLD}{head}{_OFF}" if on else f"{_DIM}{head}{_OFF}"
        elif hl and on:
            head = "►" + head[1:]
        out.append(head)
        out.append(f"         {_STATE_NOTE.get(c.state, '')}")
        t = c.terminal
        if t:
            out.append(f"         terminal seq={t.sequence} kid={t.kid or '-'} "
                       f"{t.verdict}")
    return "\n".join(out)


def render_focus(ids: list[str], rows: list[audit.Row],
                 cs: list[audit.Chain]) -> str:
    """Restate the highlighted ids AFTER the summary, with their verdicts.

    `--highlight` marks rows in place, which is right — nothing is dropped. But on a
    topic holding a day of traffic the marked rows are thousands of lines above where
    the operator is looking when the command ends, so "it printed nothing about
    $CORR_FORGED" was a fair description of a tool that had printed it.

    This block is the answer to the question actually being asked on stage — "what
    happened to THIS id?" — in the place the eye lands. It adds no new analysis: every
    number here is recomputed from the same rows and chains the list above rendered, so
    it cannot disagree with them. An id that matched nothing says so by name, which is
    the case that used to be indistinguishable from a quiet success.
    """
    out = [f"\nfocus — the {len(ids)} highlighted id(s)", "-" * 78]
    by_corr: dict[str, list[audit.Row]] = {}
    for r in rows:
        for key in (r.correlationid, r.groupid):
            if key and key in ids:
                by_corr.setdefault(key, []).append(r)
    chain_by_corr = {x.correlationid: x for x in cs if x.correlationid}
    for i in ids:
        mine = by_corr.get(i, [])
        if not mine:
            # Said loudly. A typo'd id, an aged-out run and a successful filter all
            # produce zero rows, and silence would read as the third.
            out.append(f"  {i:<24} NO EVENTS on either topic — wrong id, or aged "
                       f"out of the topic's 24h retention")
            continue
        verdicts: dict[str, int] = {}
        for r in mine:
            verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1
        ch = chain_by_corr.get(i)
        state = ch.state.upper() if ch else "no wake event on the requests topic"
        out.append(f"  {i:<24} {len(mine):>4} event(s)  {state}")
        out.append(f"         {'  '.join(f'{k}={v}' for k, v in sorted(verdicts.items()))}")
        if ch:
            out.append(f"         {_STATE_NOTE.get(ch.state, '')}")
            t = ch.terminal
            out.append(f"         terminal seq={t.sequence} kid={t.kid or '-'} "
                       f"{t.verdict}" if t else "         no terminal event")
    return "\n".join(out)


def audit_locally(args, c: Checks) -> int:
    ks = None
    if args.keyset:
        try:
            ks = keyset.load(args.keyset)
            c.log(f"keyset {args.keyset} — {len(ks)} kid(s): {', '.join(ks.kids)}")
        except Exception as e:  # noqa: BLE001
            die(f"could not load the keyset: {e}", prefix=PREFIX)
    else:
        c.log("no --keyset: listing events without verifying any of them")

    # Which verifier is doing the work. Said out loud because the difference is 183 ms
    # vs 0.12 ms per signature — on a 500-signature topic that is 90 seconds against a
    # tenth of one, and an operator watching a slow run deserves to know it is the
    # fallback rather than the network.
    from shared import ed25519_native
    if ed25519_native.load() is not None:
        c.log("signatures: libcrypto (native, ~0.12 ms each)")
    else:
        c.log(f"signatures: pure Python (~183 ms each) — no libcrypto here "
              f"({ed25519_native.unavailable_reason()}). Correct, just slow.")

    corr_ids = ids_from(args.corr)
    hl_ids = ids_from(args.highlight)
    paint = _colour_ok() and not args.no_color
    if corr_ids:
        c.log(f"--corr: showing only {len(corr_ids)} correlation/group id(s): "
              f"{', '.join(corr_ids)}")
        # Said plainly, because a filtered audit is a partial one and the output no
        # longer shows that by itself. §8.5.2's rule: state the default, state the scope.
        c.log("  EVERYTHING ELSE ON THE TOPIC IS HIDDEN, including events an attacker "
              "wrote. Use --highlight instead to narrow attention without dropping "
              "evidence.")
    if hl_ids:
        c.log(f"--highlight: marking {len(hl_ids)} id(s): {', '.join(hl_ids)}"
              + ("" if paint else " (no colour here, so matches are marked ►)"))

    topics = args.topic or [args.request_topic, args.response_topic]
    # Requests FIRST when a label has to be resolved: `ce_label` rides on the wake event
    # only, so the responses topic cannot be filtered until the requests topic has told
    # us which correlation ids those labels name.
    filter_labels = ids_from(args.label)
    mark_labels = ids_from(args.highlight_label)
    if (filter_labels or mark_labels) and args.request_topic in topics:
        topics = ([args.request_topic]
                  + [t for t in topics if t != args.request_topic])

    rows_by_topic: dict[str, list[audit.Row]] = {}
    dropped = 0
    for topic in topics:
        events = read_topic(args.bootstrap, topic, limit=args.limit,
                            timeout_ms=args.timeout_ms)
        rows = []
        # Progress, because this used to be a silent 93-second wait. Even with the
        # native verifier (which makes the whole pass sub-second) the Kafka read is
        # seconds, so a line per topic is worth keeping — a tool that prints nothing
        # for a minute is indistinguishable from one that has hung.
        total = len(events)
        step = max(total // 10, 1)
        for i, (off, e) in enumerate(events, 1):
            rows.append(audit.row_for(e, off, ks, bridge_kid=args.bridge_kid or None))
            if total > 50 and (i % step == 0 or i == total):
                _progress(f"{topic}: verified {i}/{total} signature(s)",
                          done=(i == total))
        # ADR-0003: a label names a run; turn it into that run's correlation ids as soon
        # as the requests topic has been read, then reuse the id machinery below.
        if (filter_labels or mark_labels) and topic == args.request_topic:
            for labels, bucket, flag in ((filter_labels, "filter", "--label"),
                                         (mark_labels, "mark", "--highlight-label")):
                if not labels:
                    continue
                found = corrs_for_labels(rows, labels)
                if found:
                    c.log(f"{flag} {','.join(labels)} -> {len(found)} run(s): "
                          f"{', '.join(found)}")
                elif bucket == "filter":
                    # ABORT, do not carry on. `--label` is documented as a filter that
                    # HIDES everything else, and a label resolving to zero ids used to
                    # leave `corr_ids` empty — so no filter was applied and the whole
                    # topic printed. A flag that silently becomes its own opposite is
                    # the dead-flag shape this repo already got bitten by
                    # (`k8s_demo_flow.py`'s unconsumed --skip/--phase), and here it is
                    # worse: the operator asked for ONE run and got a thousand, with
                    # nothing in the output saying the filter had not applied.
                    #
                    # Observed on ykt1 2026-11-07 with `--label incident`: the deployed
                    # EventBridge predates `ce_label` (`shared.ce.EXT_LABEL` absent from
                    # the running image), so no event on the topic carried a label.
                    die(f"{flag} {','.join(labels)} matched no request on {topic}, so "
                        f"there is nothing to show.\n"
                        f"A label rides on the WAKE event only (ADR-0003), and only for "
                        f"runs submitted through a label-aware EventBridge — an older "
                        f"one accepts the field and drops it. Check with:\n"
                        f"  kubectl -n $NS exec deploy/eventbridge -- \\\n"
                        f"    python -c 'from shared import ce; print(ce.EXT_LABEL)'\n"
                        f"If that says ABSENT, redeploy EventBridge; until then pass "
                        f"--corr/--highlight with the correlation id itself.",
                        prefix=PREFIX)
                else:
                    # --highlight-label is additive, not a filter: marking nothing is a
                    # weaker failure than hiding everything, and the audit it would
                    # otherwise print is still complete and still true. So warn and
                    # continue, rather than refusing to show a correct listing.
                    c.warn(f"{flag} {','.join(labels)} matched no request on {topic}, "
                           f"so nothing is marked. A label rides on the WAKE event only "
                           f"(ADR-0003) and needs a label-aware EventBridge.")
                if bucket == "filter":
                    corr_ids = sorted(set(corr_ids) | set(found))
                else:
                    hl_ids = sorted(set(hl_ids) | set(found))
        if corr_ids:
            kept = [r for r in rows if row_matches(r, corr_ids)]
            dropped += len(rows) - len(kept)
            rows = kept
        rows_by_topic[topic] = rows
        if not args.chains:
            print(render_rows(rows, topic, highlight=hl_ids, paint=paint))

    all_rows = [r for rows in rows_by_topic.values() for r in rows]
    reqs = rows_by_topic.get(args.request_topic, [])
    resps = rows_by_topic.get(args.response_topic, [])
    if args.chains or args.verify:
        print(render_chains(audit.chains(reqs, resps),
                            highlight=hl_ids, paint=paint))

    s = audit.summarise(all_rows)
    print("\nsummary", "-" * 70)
    if corr_ids:
        # The summary counts only what survived the filter, so say so next to it. A
        # reader who sees `signed-BAD 0` under a filter must not read that as "the topic
        # is clean" — that is the misreading this whole tool exists to prevent.
        print(f"  {'SCOPE':<13} --corr filter active: {dropped} event(s) on the "
              f"topic are NOT counted below")
    for k in (audit.VERDICT_OK, audit.VERDICT_BAD, audit.VERDICT_UNSIGNED,
              audit.VERDICT_EXEMPT, audit.VERDICT_UNKNOWN):
        if s.get(k):
            print(f"  {k:<13} {s[k]}")
    print(f"  {'terminals':<13} {s['terminals']} "
          f"({s['terminals_verified']} verified)")
    # After the summary, because that is where the operator is looking when the command
    # ends — and on a day-old topic the highlighted rows are thousands of lines above.
    if hl_ids:
        print(render_focus(hl_ids, all_rows, audit.chains(reqs, resps)))
    if args.json:
        print("\n" + json.dumps({"summary": s, "chains": [
            {"wake": c.request_id, "correlationid": c.correlationid,
             "state": c.state, "results": len(c.results)}
            for c in audit.chains(reqs, resps)]}, indent=2))

    if not args.verify:
        # Listing mode asserts nothing, and `Checks.summary()` exits non-zero when no
        # assertion ran — correctly, since a harness that asserted nothing has proven
        # nothing. Reading a topic is not a harness, so return 0 directly rather than
        # inventing a pass.
        return 0
    # --verify turns the audit into an assertion, for the demo flow and for CI.
    c.expect(s.get(audit.VERDICT_BAD, 0) == 0,
             "no event on either topic carries a signature that failed to verify",
             f"signed-BAD={s.get(audit.VERDICT_BAD)}")
    c.expect(s.get(audit.VERDICT_UNSIGNED, 0) == 0,
             "every event that policy says must be signed is signed",
             f"unsigned={s.get(audit.VERDICT_UNSIGNED)}")
    c.expect(s["terminals"] > 0 and s["terminals_verified"] == s["terminals"],
             f"every terminal verified ({s['terminals_verified']}/{s['terminals']})")
    bad_chains = [x for x in audit.chains(reqs, resps)
                  if x.state in ("contested", "unverified", "orphaned")]
    c.expect(not bad_chains,
             "every result chains back to a verified wake event",
             "; ".join(f"{x.correlationid or x.request_id[:8]}={x.state}"
                       for x in bad_chains))
    return c.summary()


def audit_in_cluster(args, c: Checks) -> int:
    """Run this same script inside a throwaway pod on the eventrunner image."""
    # Host-only imports, deliberately not at module level — see the import block.
    from imagelib import Images
    from k8slib import Kubectl, dig

    k = Kubectl(context=args.context, namespace=args.namespace,
                kubeconfig=args.kubeconfig)
    cfg = k.get("configmap", "eventing-config", namespace=args.namespace) or {}
    bootstrap = dig(cfg, "data", "KAFKA_BOOTSTRAP") or args.bootstrap
    req_t = dig(cfg, "data", "REQUEST_TOPIC") or args.request_topic
    res_t = dig(cfg, "data", "RESPONSE_TOPIC") or args.response_topic
    ks_path = dig(cfg, "data", "EB_VERIFY_KEYSET_PATH") or ""
    c.log(f"bootstrap={bootstrap} topics={req_t},{res_t} keyset={ks_path or '(none)'}")
    if not ks_path:
        c.log("  the deployment has no keyset configured, so nothing will be verified "
              "— this is a listing, not an audit (§8.5.2: state the default)")

    # Everything the pod runs travels WITH it, as source. Nothing is imported from
    # the image except third-party wheels (kafka-python).
    #
    # This is the fix for a real cluster failure, and the failure mode is the point:
    #
    #     AttributeError: 'Row' object has no attribute 'label'
    #
    # The pod ran THIS file's current source but imported `shared` from `/app` — the
    # image. `--label` needs `audit.Row.label` (ADR-0003), the deployed
    # `eventrunner-claude:local1` predates that field, and so a brand-new script met a
    # months-old dataclass. `imagePullPolicy: Always` cannot help: the image is not
    # stale in the registry, it is stale in its CONTENT, and only a rebuild would move
    # it. Pinning the audit to a deployment's image lifecycle is the wrong coupling
    # anyway — reading a topic should not require redeploying the thing being audited.
    #
    # So the host's own `shared/` is shipped alongside, placed FIRST on sys.path, and
    # the image's copy is left unused. The audit now verifies with exactly the code the
    # operator is looking at, which is also what makes its verdict attributable.
    #
    # Compressed and base64'd rather than repr'd: the six modules are ~119 KB of source
    # and `repr()` of that approaches Linux's 131072-byte MAX_ARG_STRLEN per argument,
    # at which point execve fails with E2BIG and the symptom is an empty pod log. zlib
    # takes it to ~51 KB, with room to grow.
    ship = {}
    here = pathlib.Path(__file__).resolve().parent
    root = here.parent
    for mod in ("__init__", "ce", "keyset", "signing", "ed25519_native", "audit"):
        ship[f"shared/{mod}.py"] = (root / "shared" / f"{mod}.py").read_text()
    # proclib.py rides along for the same reason it always did: the eventrunner image
    # has no `scripts/`, so the copy of this file running in the pod cannot import it
    # from anywhere else. Shipping source beats adding scripts/ to a runtime image.
    ship["proclib.py"] = (here / "proclib.py").read_text()
    ship["audit_topic.py"] = pathlib.Path(__file__).read_text()
    blob = base64.b64encode(zlib.compress(json.dumps(ship).encode(), 9)).decode()
    inner = ["python", "-c",
             "import sys,json,zlib,base64,pathlib;"
             "d=pathlib.Path('/tmp/auditpkg');"
             f"f=json.loads(zlib.decompress(base64.b64decode({blob!r})));"
             "[((d/k).parent.mkdir(parents=True,exist_ok=True),"
             "  (d/k).write_text(v)) for k,v in f.items()];"
             "p=d/'audit_topic.py';"
             "sys.argv=['audit_topic.py','--local',"
             f"'--bootstrap',{bootstrap!r},"
             f"'--request-topic',{req_t!r},'--response-topic',{res_t!r},"
             + (f"'--keyset',{ks_path!r}," if ks_path else "")
             + (f"'--bridge-kid',{args.bridge_kid!r}," if args.bridge_kid else "")
             + ("'--chains'," if args.chains else "")
             + ("'--verify'," if args.verify else "")
             + ("'--json'," if args.json else "")
             # Forwarded, or the flags would be accepted on the host and silently do
             # nothing in the pod that actually reads the topic — the same dead-flag
             # shape as `k8s_demo_flow.py`'s unconsumed --skip/--phase.
             + "".join(f"'--corr',{v!r}," for v in ids_from(args.corr))
             + "".join(f"'--highlight',{v!r}," for v in ids_from(args.highlight))
             + "".join(f"'--label',{v!r}," for v in ids_from(args.label))
             + "".join(f"'--highlight-label',{v!r},"
                       for v in ids_from(args.highlight_label))
             # The pod's stdout is a pipe, never a tty, so colour is stripped there
             # regardless; pass the flag through so `--no-color` is still honoured by
             # anything that checks it and the intent survives in the command.
             + ("'--no-color'," if args.no_color else "")
             + f"'--limit',{str(args.limit)!r}];"
             # /app LAST and /tmp/auditpkg FIRST: `shared` must resolve to the
             # source shipped above, not to the image's older copy. /app stays on
             # the path for the third-party wheels (kafka-python) that are the only
             # thing the audit still legitimately borrows from the image.
             "sys.path.insert(0,'/app');sys.path.insert(0,str(d));"
             "exec(compile(p.read_text(),'audit_topic.py','exec'),"
             "{'__name__':'__main__','__file__':str(p)})"]
    name = f"audit-{int(time.time())}"
    # The keyset has to be MOUNTED, not just named. The docstring above promises "the
    # keyset it verifies against is literally the one the deployment uses", and the
    # path comes from the deployment's own ConfigMap — but a plain `kubectl run`
    # mounts no volumes, so the pod died on:
    #
    #     [audit] ABORT could not load the keyset:
    #     [Errno 2] No such file or directory: '/etc/ce-keyset/agents.json'
    #
    # `--overrides` adds the same ConfigMap volume the Deployments use. `optional:
    # true` matches the base manifests: a deployment with no keyset configured is a
    # valid listing-only audit, which the `if not ks_path` branch above already
    # explains.
    overrides = {}
    if ks_path:
        mount_dir = str(pathlib.PurePosixPath(ks_path).parent)
        overrides = {"spec": {
            "containers": [{
                "name": name,
                "image": Images(args.registry, args.tag).eventrunner,
                # ALWAYS, because this pod runs THIS script's own source against a
                # MUTABLE tag. `kubectl run` defaults to IfNotPresent, so a node that
                # has ever pulled `:local1` keeps serving its cached layers after a
                # rebuild — and the failure is not "old code", it is an ImportError for
                # a module the new source expects and the stale image does not have:
                #
                #   ImportError: cannot import name 'ed25519_native' from 'shared'
                #
                # Diagnosed exactly that way once. The audit reads a topic and exits,
                # so re-pulling costs seconds and buys the guarantee that the pod is
                # running the code that was built.
                "imagePullPolicy": "Always",
                "command": inner,
                "volumeMounts": [{"name": "keyset", "mountPath": mount_dir,
                                  "readOnly": True}],
            }],
            "volumes": [{"name": "keyset",
                         "configMap": {"name": "eventing-keyset", "optional": True}}],
        }}
    run_argv = ["run", name, "--rm", "-i", "--restart=Never", "--quiet"]
    if overrides:
        run_argv += ["--overrides", json.dumps(overrides)]
    c.log(f"starting pod {name} (image pull + Kafka read; output streams below)")
    # stream=True, and this is the whole reason `run(stream=…)` exists.
    #
    # This used to capture the pod to completion and print it afterwards as
    # `res.out + res.err`, which produced two distinct lies on stage:
    #
    #   1. NOTHING appeared for 30-60s. A pull, a Kafka read and a hung cluster all
    #      look identical when a tool prints nothing, and the operator finds out
    #      which only when it ends.
    #   2. The pod's PROGRESS (stderr) was concatenated AFTER its RESULTS (stdout),
    #      so every "classified 610/619" line replayed below the summary and the
    #      four PASS lines — reading as though the audit had restarted. That is the
    #      output in the bug report, and it was never a buffering problem.
    #
    # Streaming fixes both: stdout and stderr are teed to the matching stream as
    # they arrive, so progress interleaves with rows in real time and in the right
    # order, and a caller piping our stdout still gets only audit output.
    res = k.call(run_argv + [
                  # Images(), not an f-string: DEFAULT_REGISTRY already ends in "/",
                  # so interpolating another one renders "quay.io/aslomnet//rossoctl-…"
                  # which podman rejects as an invalid reference. `normalize_prefix`
                  # exists for exactly this and is what Images goes through.
                  "--image", Images(args.registry, args.tag).eventrunner,
                  "--command", "--", *inner],
                 namespace=args.namespace, timeout=args.pod_timeout, stream=True)
    out = (res.out or "") + (res.err or "")
    # NOT re-printed: streaming already showed every line. Printing here as well
    # would duplicate the entire audit, which is the bug one layer up.
    if not res.ok:
        die("the audit pod did not complete; see its output above", prefix=PREFIX)
    return 0 if "FAIL" not in out else 1


def build_args(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace", "-n", default=os.environ.get("NS", "kev1"))
    ap.add_argument("--kubeconfig", default=None)
    ap.add_argument("--context", default=None)
    ap.add_argument("--local", action="store_true",
                    help="talk to --bootstrap directly instead of via a pod")
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--topic", action="append", default=None,
                    help="audit only this topic (repeatable)")
    ap.add_argument("--request-topic", default="kev1-requests")
    ap.add_argument("--response-topic", default="kev1-responses")
    ap.add_argument("--keyset", default=None,
                    help="approved-key set to verify against; without it nothing is "
                         "verified and the tool only lists")
    ap.add_argument("--bridge-kid", default="",
                    help="the only kid accepted on group lifecycle events")
    ap.add_argument("--corr", action="append", default=None, metavar="ID[,ID...]",
                    help="show ONLY events for these correlation or group ids "
                         "(repeatable, comma-separated). A filtered audit is a partial "
                         "one: events an attacker wrote are hidden too, so prefer "
                         "--highlight when showing this to an audience")
    ap.add_argument("--highlight", action="append", default=None, metavar="ID[,ID...]",
                    help="mark these correlation or group ids and dim the rest, without "
                         "dropping anything (repeatable, comma-separated). Bold where "
                         "there is colour, a leading ► where there is not")
    ap.add_argument("--label", action="append", default=None, metavar="NAME[,NAME...]",
                    help="show ONLY the runs whose wake event carried one of these "
                         "labels (ADR-0003; repeatable, comma-separated). Resolved to "
                         "correlation ids off the requests topic, so it needs no access "
                         "to EventBridge. Same caveat as --corr: it HIDES everything "
                         "else, forgeries included")
    ap.add_argument("--highlight-label", action="append", default=None,
                    metavar="NAME[,NAME...]",
                    help="mark the runs carrying these labels and dim the rest, "
                         "dropping nothing. The label-keyed form of --highlight, and "
                         "the one to use in front of an audience")
    ap.add_argument("--no-color", action="store_true",
                    help="never emit ANSI codes; --highlight then marks matches with ►")
    ap.add_argument("--chains", action="store_true",
                    help="show the result-to-wake binding instead of the event list")
    ap.add_argument("--verify", action="store_true",
                    help="assert the audit holds and exit non-zero if it does not")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--timeout-ms", type=int, default=4000)
    ap.add_argument("--pod-timeout", type=float, default=240.0)
    # argparse runs in BOTH places, so these defaults cannot come from a
    # module-level `imagelib` import. In the pod the flags are unused (it is invoked
    # with --local), so falling back to None there is correct rather than a guess;
    # on the host the real defaults are read from imagelib as before.
    ap.add_argument("--registry", default=os.environ.get("REGISTRY_PREFIX",
                                                         _image_default("registry")))
    ap.add_argument("--tag", default=os.environ.get("TAG", _image_default("tag")))
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = build_args(argv)
    c = Checks(prefix=PREFIX)
    if args.local:
        return audit_locally(args, c)
    return audit_in_cluster(args, c)


if __name__ == "__main__":
    raise SystemExit(main())
