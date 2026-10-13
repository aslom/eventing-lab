# Implementation reports

What a phase **actually built**, measured rather than estimated — including the parts that
went wrong, with their root causes, and an honest list of what is still not verified.

A report is the counterweight to a spec. The spec says what will be true; the report says what
was true when someone ran it. Both are committed, and they are kept separate on purpose.

## Index

| Report                                           | Covers                | Spec                                              | Status  |
| ------------------------------------------------ | --------------------- | ------------------------------------------------- | ------- |
| `<topic>-report.md`                              | <what phase/slice>    | `../specs/<file>.md`                              | Partial |

<!-- One row per report. "Status: Partial" is normal and useful — see below. -->

## The one rule: a report is never rewritten

A spec is **revised in place**; a report is **append-only.** When a later phase changes the
answer, write a new report or append a dated section — never go back and quietly correct an
old number.

This matters because the most valuable content in a report is the record of a wrong
prediction: _"the design estimated X; it measured Y."_ Edit the Y and you have destroyed the
only evidence that the estimate was off, and with it the reason to estimate more carefully
next time.

Corollary: it is fine — expected, even — for a report to disagree with its spec. That
disagreement is the finding. Record it as a delta (§6 of the template) rather than resolving
it silently in either document.

## What makes a report worth reading

The weak version of this document is a changelog: _"implemented A, B and C; tests pass."_ That
tells a later reader nothing they could not get from `git log`. The strong version is
specific and falsifiable:

- **Measured, not estimated.** Real numbers against the spec's predictions, with the method
  named: _"the per-tenant file-descriptor cost against §6.1's estimate"_, _"the 270 ms that
  settles §2.6's objection to seeding"_.
- **Findings with root causes, not symptoms.** Each finding records the symptom that hid it,
  so the next person recognises the shape. _"A `single`-mode regression that shipped behind a
  headline claiming it could not happen"_ is worth ten lines of "fixed a bug".
- **Deltas from the design, recorded as decisions.** Where the implementation diverged, and
  whether that was a correction to the design or a concession to reality.
- **What is NOT verified.** The section everyone wants to skip and the one most often cited
  later. If a control was never exercised, say so plainly — a claim nobody tested is a claim,
  not a control.
- **Partial is a valid status.** A report for in-flight work says what has landed so far and
  is extended as later steps merge, rather than waiting for a tidy ending that never comes.

## Required header

```markdown
# <Phase or slice> — Implementation report

Status: Partial <!-- Partial | Complete -->
Date: YYYY-MM-DD
Spec: `../specs/<file>.md`
Covers: <which sections/steps of the spec landed>
```

CI fails a report missing `Status:`, `Spec:`, or its "not verified" section.

## Creating one

```sh
cp docs/reports/0000-report-template.md docs/reports/<topic>-report.md
# measure first, write second. Add a row to the Index above and
# link the report from its spec's header.
```

---

_Assisted-By: Claude Code_
