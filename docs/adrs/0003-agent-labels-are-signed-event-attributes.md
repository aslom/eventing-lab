# ADR-0003: An agent label is a signed CloudEvent attribute

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** aslom
- **Supersedes:** [ADR-0001](0001-agent-labels-are-store-only.md)
- **Spec:** [`../specs/2026-11-05-three-part-demo-design.md`](../specs/2026-11-05-three-part-demo-design.md)

## Context

[ADR-0001](0001-agent-labels-are-store-only.md) made the agent label store-only, and named
the cost it was accepting: "the label cannot reach the audit tool, which reads topic bytes and
has no store access. Filtering an audit by label therefore takes two steps."

That cost turned out to be the one that mattered. `audit_topic.py` is where a label is most
useful — it is the tool that reads a whole topic and needs to be told which run to care
about — and it was the one consumer that could not use one. The demo script had to resolve a
label to a correlationid through EventBridge and then pass the id to `--highlight`, which
means the audit's view of "this run" depended on a second service being reachable and
agreeing. For a tool whose entire purpose is to read the broker log as the authoritative
record, borrowing an identifier from a database beside it is the wrong shape.

ADR-0001's reasoning for staying off the wire was not that the wire was the wrong place. It
was that an **unsigned** attribute would be forgeable, and specifically that
`kafka_requests_mirror` rebuilds session rows from topic data with the target store chosen by
`ce_userkey` — which spec 0003 §8.2 records as forger-controlled (eventing-lab#5, open). A
mutable label would therefore be a cross-tenant injection path into the lookup it serves.

That objection is answered by signing the attribute, not by hiding it. `submitter`, `groupid`,
`userkey`, `depth` and `agent` are all on the wire and all trustworthy for exactly that
reason.

## Decision

We will carry the label as **`ce_label`, a CloudEvent extension attribute included in
`signing.SIGNED_ATTRS`.** `publish_request` sets it; `audit.Row` exposes it; `audit_topic.py`
gains `--label` (filter) and `--highlight-label` (mark), both resolving labels to correlation
ids off the requests topic with no access to EventBridge.

The `sessions.label` column stays. It is what `GET /v0/agents?label=` reads, and that endpoint
answers "which run was that?" for a human at a terminal, which is a different question from
"which events belong to that run?".

### Alternatives considered

- **Keep it store-only (ADR-0001)** — the status quo. Rejected because it leaves the audit
  unable to use the one identifier an operator actually remembers, and makes the broker log's
  interpretation depend on a database.
- **An unsigned `ce_label`** — rejected for exactly ADR-0001's reason, which still holds and
  is now pinned by `test_the_label_is_covered_by_the_signature`. Unsigned, it would be worse
  than absent: a guessable string resolving to a capability an attacker can steer.
- **Teach the audit to call EventBridge** and resolve labels over HTTP — gives the audit an
  HTTP dependency it does not have, and the tool's design note is explicit that it speaks to a
  broker and a keyset and nothing else. It would also make the audit's answers depend on a
  service that may be down or lying.
- **Match audit rows on `ce_label` directly** rather than resolving to correlation ids —
  simpler, and wrong. The label rides on the wake event only; responses carry `correlationid`
  and `causationid`. Matching on the label would show the request and hide every answer to it.

## Consequences

- Positive: `audit_topic.py --label incident` works with no EventBridge, no store access and
  no copy-paste. The demo's Part 2 and Part 3 filter by the same word the operator typed in
  Part 1.
- Positive: the label is now as trustworthy as `submitter` beside it. Rewriting it to hijack
  someone else's view breaks the signature — exercised, not asserted, by
  `test_rewriting_the_label_breaks_the_signature`.
- Negative / accepted cost: **this breaks canonicalisation.** Adding a name to
  `SIGNED_ATTRS` changes the signed blob, so a signer and a verifier on different versions
  disagree about *every* signature, not just labelled ones. A deployment with signing enabled
  **must upgrade EventBridge and EventRunner together**; ykt1 enforces in both directions, so
  a staggered rollout there means total refusal of genuine traffic until both are current.
  With signing off (the default) there is nothing to coordinate. Landed as its own change with
  nothing else in it, per spec 0003 §8.3's one-canonicalisation-break rule.
- Negative / accepted cost: an older EventBridge accepts a `label` body field and silently
  drops it, so a run submitted through one has no `ce_label` and `--label` will not find it.
  The audit says so rather than reporting an empty result as a clean topic.
- Negative / accepted cost: two places now hold a label — `sessions.label` and `ce_label` —
  and they can disagree if a row is written by something other than `publish_request`. The
  store copy is what the HTTP lookup reads; the event copy is what the audit reads. Neither is
  derived from the other, which is a seam worth remembering.
- Follow-up owed: `--label`, like `--corr`, **hides** everything it was not given. The
  demo uses `--highlight-label` in the beat where a forgery is on the topic, because an audit
  that showed a clean topic seconds after an attack would be worse than no audit.
