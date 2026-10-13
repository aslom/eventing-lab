# Architecture Decision Records (ADRs)

This directory is the **permanent decision spine** of the repo. An ADR captures **one
significant decision** — the context that forced it, the choice made, and the consequences we
accepted — in a form that stays true even as the code around it changes.

An ADR is short. The reasoning lives in the linked [spec](../specs/); the ADR exists so that
someone can scan the index below and learn, in one pass, every load-bearing choice this
system rests on.

## Index

| #                                    | Decision              | Status   |
| ------------------------------------ | --------------------- | -------- |
| `0001-<kebab-title>.md`              | <The decision, as a claim — "Persist session state through a pluggable backend"> | Proposed |

<!-- One row per ADR, in number order. Numbers are permanent and never reused. -->

## What an ADR is (and isn't)

|           | ADR (here)                          | Spec ([`../specs/`](../specs/))                             | Report ([`../reports/`](../reports/)) | Plan ([`../plans/`](../plans/)) |
| --------- | ----------------------------------- | ----------------------------------------------------------- | ------------------------------------- | ------------------------------- |
| Answers   | _what we decided & why_             | _what & why, in depth_ (alternatives, trade-offs, deferred) | _what was built and measured_         | _how, in what order_            |
| Size      | short (one decision)                | long (a whole design)                                       | long (measurements + findings)        | a checklist                     |
| Retention | **permanent, immutable**            | committed, revised in place                                 | committed, append-only                | **local-only, ephemeral**       |
| On change | write a **new** ADR that supersedes | **edit it** and bump the version                            | **append** — never rewrite            | delete once coded               |

## Rules

1. **Immutable.** Once accepted, an ADR is never edited except to change its `Status` line
   (e.g. to `Superseded by ADR-0007`). To change a decision, write a **new** ADR that
   references and supersedes the old one. The record of _why we once thought otherwise_ has
   value.
2. **Numbered, monotonic.** Files are `NNNN-kebab-title.md`, zero-padded, next free number.
   Numbers are never reused.
3. **One decision per ADR.** If you're recording two, write two.
4. **Link to the spec.** The ADR states the decision; the spec carries the reasoning.
5. **Name the alternatives.** A decision recorded without the options it beat is not a
   decision, it is an announcement.

## Status vocabulary

`Proposed` → `Accepted` → `Superseded by ADR-NNNN` (or `Deprecated`). Same vocabulary as
specs (see [the lifecycle](../README.md#status-vocabulary)).

CI fails an ADR missing `Status:`, `Date:` or `Deciders:`.

## Creating one

```sh
cp docs/adrs/0000-adr-template.md docs/adrs/NNNN-your-decision.md
# fill in Context / Decision / Consequences; set Status: Accepted; link the spec
# add a row to the Index above
```

---

_Assisted-By: Claude Code_
