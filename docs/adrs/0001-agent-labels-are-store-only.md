# ADR-0001: An agent label is store-only metadata, never a CloudEvent attribute

- **Status:** Superseded by [ADR-0003](0003-agent-labels-are-signed-event-attributes.md)
- **Date:** 2026-11-06
- **Deciders:** aslom
- **Spec:** [`../specs/2026-11-05-three-part-demo-design.md`](../specs/2026-11-05-three-part-demo-design.md)

## Context

Driving the demo required reading a correlationid off the screen and retyping it, because
nothing could answer "which run was that?":

```sh
CORR=sweet-crab-0956      # ← paste from the line above
```

`label` already existed, but only on **groups** (`store.py`, `groups` table). The `sessions`
table had no label column, `POST /v0/agents` ignored a `label` body field, `runmany --label`
was client-side display that never left the CLI, and there was no agent-list endpoint at all —
so `GET /v0/agents?limit=1` answered `not found`.

`scripts/watch_topics.py:latest_group` had already solved this shape for groups, and its
docstring states the problem exactly: re-resolve the id every frame, "otherwise a demo has a
copy-paste step in the middle: you cannot know the id until `group run` has printed it, by
which point the interesting first seconds are gone."

The question was not whether to add a label but **where it lives** — on the wire, or only in
the store.

## Decision

We will carry an agent label as **a column on `sessions` in SQLite, and never as a CloudEvent
attribute.** `POST /v0/agents` accepts and validates `label`; `GET /v0/agents?label=X` returns
the most recently *started* run carrying it.

### Alternatives considered

- **A CloudEvent attribute in `signing.SIGNED_ATTRS`** — adding a name changes
  canonicalisation, so a signer and verifier on different versions disagree about *every*
  signature; both services must then upgrade together. The demo cluster enforces signing in
  both directions, so this risks breaking the beat the label exists to make easier to reach.
  Nothing downstream reads a label either: `publish_request` takes no such parameter and
  EventRunner never consumes one.
- **A CloudEvent attribute *outside* `SIGNED_ATTRS`** — strictly worse than both other
  options. It would be mutable in flight, and `kafka_requests_mirror` writes session rows from
  topic data with the target store chosen by `ce_userkey`, which spec 0003 §8.2 records as
  forger-controlled (eventing-lab#5, open). That makes an unsigned label a cross-tenant
  injection path into the very lookup the demo trusts.
- **A client-side shell helper** writing corr→label to a local file — no API surface, but
  per-machine, lost on cleanup, and invisible to anything but the one shell.
- **Reusing groups as a batch of one** — needs no new code, but Part 1b stops being "one
  agent" and the notification story changes from one message to a group start/finish pair.

## Consequences

- Positive: no wire change, so no coordinated upgrade and no signature risk. `CORR=$(… latest
  incident)` works from any pane, and re-running a labelled beat just moves the label — no
  cleanup step between rehearsal takes.
- Positive: the lookup is **authenticated unconditionally**, unlike `list_groups` which
  resolves a caller only to pick a store. A label is a *guessable* word, and `/continue` is
  deliberately open, so an ungated lookup would convert "demo" into a capability to post a
  turn into someone's conversation.
- Negative / accepted cost: the label cannot reach the audit tool, which reads topic bytes and
  has no store access. Filtering an audit by label therefore takes two steps — resolve the
  label to a correlationid, then pass that to `--highlight`/`--corr`.
- Negative / accepted cost: ordering is on `created_utc`, so "latest" means most recently
  *started*. A `/continue` on an older labelled run does not promote it. `rowid` breaks
  same-millisecond ties.
- Negative / accepted cost: three unrelated things are now called "label" — a run tag
  (`run --label`), a per-agent display name (`runmany --label`) and a batch name
  (`group run --label`). `SKILL.md` carries a table disambiguating them; renaming an existing
  flag would be a breaking change for a one-word gain.
- Follow-up owed: there is deliberately **no bare agent list**. Spec 0003 §8.2 already files
  `GET /v0/groups` publishing correlation ids without sign-in as an open bug
  (eventing-lab#1); an agent list would be the same bug with every row a continuable
  conversation. `label` is therefore required, and a missing one is a 400.
