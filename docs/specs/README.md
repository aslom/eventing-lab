# <PROJECT> — Design specs

A spec is a **deep dive on one subsystem or one slice of work**: what we are building, why,
what we considered and turned down, and what we are knowingly deferring. It is the document
you read before changing the thing it describes.

**This file is the registry.** It is the source of truth for milestone ids and status; the
filenames are dated and descriptive so they never need renaming when numbering shifts.

## Index

| ID  | Title                   | Spec                                                              | Status      | Report |
| --- | ----------------------- | ----------------------------------------------------------------- | ----------- | ------ |
| M1  | <first design>          | `YYYY-MM-DD-<topic>-design.md`                                    | Proposed    | —      |

<!--
Add a row per spec. Keep the canonical ID here rather than in the filename.
If the work is phase-structured instead of milestone-numbered, use Phase 0/1/2… as the ID.
Link the report in the last column once the phase lands.
-->

## What a spec is (and isn't)

|                | Spec (here)                                         | ADR ([`../adrs/`](../adrs/))        | Report ([`../reports/`](../reports/)) | Plan ([`../plans/`](../plans/)) |
| -------------- | --------------------------------------------------- | ----------------------------------- | ------------------------------------- | ------------------------------- |
| Answers        | _what & why, in depth_                              | _one decision & its consequences_   | _what was built and measured_         | _how, in what order_            |
| Written        | **before** the code                                 | when the decision is taken          | **after** the phase lands             | before the code                 |
| Size           | long (a whole design)                               | short (one decision)                | long (measurements + findings)        | a checklist                     |
| On being wrong | **edit it**, bump the version, log the correction   | write a **new** ADR that supersedes | **append** — never rewrite            | delete it                       |
| Retention      | committed, living                                   | committed, permanent, immutable     | committed, append-only                | **local-only, gitignored**      |

A spec is **revised in place.** Unlike an ADR, it is meant to stay true: when the
implementation teaches you something, correct the spec and record what changed in the version
changelog. A reader opening the current spec should be able to trust it.

That is also why a spec is _not_ the place for measured results. Numbers from an actual run
go in a [report](../reports/), which is never rewritten — see
[the lifecycle note](../README.md#specs-revise-reports-do-not).

## Required header

Every spec opens with this block:

```markdown
# <Title> — Design

Version: 1.0 — <Month Year>
Status: Proposed
Milestone: **<ID>**, registered in [the registry](README.md)
Builds on (reuse, no redesign): <ID> (`<file>.md`) (<one line on what is reused>)
Report: `../reports/<topic>-report.md`  <!-- once it exists -->

> **The one-sentence thesis.** <The whole design in one sentence. If you cannot write it,
> the design is not settled yet.>
```

CI fails a spec missing `Version:` or `Status:`.

### The version changelog

Once a spec has been corrected, the `Version:` line carries its own history, and each set of
corrections gets a numbered subsection:

```markdown
Version: 1.3 — <Month Year> (v1.1: corrections from the implementation plan;
v1.2: corrections from the implementation and its review; v1.3: corrections from PR 2)

### 0.1 v1.1 corrections

1. **<What was wrong, stated as the correction.>** <Why the original was wrong.>
```

Write the correction as the _new_ fact, not as "we said X but actually Y" — the reader wants
the current truth first, the history second.

## Recommended shape

Beyond the header, specs in this repo tend to carry:

- **`## 0. Decisions taken during design`** — a two-column table of
  _question_ → _decision, with the rejected options named_. This is the highest-value section
  in the document and the one reviewers read first. Name what you turned down and why; a
  decision with no rejected alternative was not a decision.
- **The design proper** — numbered sections, so reviewers and later specs can cite `§4.2`.
- **`## N. What this does not do`** — the scope you are deliberately leaving out, and what
  would have to change to add it. Honest limits here prevent someone over-promising later.
- **`## N. Risks`** — what could still go wrong, and what would tell you it had.

## Creating one

```sh
cp docs/specs/0000-spec-template.md \
   docs/specs/$(date +%F)-<topic>-design.md
# fill in the header, write § 0 Decisions first, then the design
# add a row to the Index above
```

---

_Assisted-By: Claude Code_
