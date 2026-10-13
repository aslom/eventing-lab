# <Phase or slice> — Implementation report

Status: Partial <!-- Partial | Complete. "Partial" is normal: extend this file as later steps land. -->
Date: YYYY-MM-DD
Spec: `../specs/<file>.md`  <!-- make this a real link once filled in -->
Covers: <which sections or steps of the spec actually landed>

> **What this report says in one sentence.** <What was built, and the one result that matters
> most — including if that result is "it does not work yet, for this reason".>

**Append-only.** This document is not rewritten when a later phase changes the answer — write
a new report or append a dated section. See
[the lifecycle note](../README.md#specs-revise-reports-do-not).

---

## 1. What landed

<The scope that is actually in the tree, keyed to the spec's section or step numbers so a
reader can diff intent against reality. Name what did NOT land here too, with a pointer to
§5.>

| Spec ref | What it was    | Landed | Where            |
| -------- | -------------- | ------ | ---------------- |
| §9 step 1 | <the thing>   | Yes    | `path/to/file`   |
| §9 step 5 | <the thing>   | No     | see §5           |

## 2. Measurements

<Real numbers, with the method named and the spec's prediction alongside. If the spec made no
prediction, say so — that is itself a finding about the spec.>

| What was measured | Spec predicted | Measured | Method |
| ----------------- | -------------- | -------- | ------ |
| <the quantity>    | <§N.N: X>      | <Y>      | <how, on what hardware/cluster, how many runs> |

<For each number that disagrees with its prediction, one short paragraph on why.>

## 3. Deltas from the design

<Where the implementation diverged from the spec, each recorded as a decision rather than an
accident. State whether the spec was corrected (and its new version) or the divergence was
accepted.>

1. **<The delta, stated as what is now true.>** <Why it diverged.> <Spec corrected in v1.N /
   accepted as-is.>

## 4. Findings

<The bugs, surprises and near-misses, each with the symptom that hid it. This is the section a
future debugger greps. Be specific enough to be falsifiable, and include the embarrassing
ones — they are the valuable ones.>

### 4.1 <Finding, stated as the problem>

- **Symptom:** <what was observed, and why it was easy to miss>
- **Root cause:** <the actual mechanism>
- **Fix:** <what changed, or the issue filed>

## 5. What is NOT verified

<The section everyone wants to skip and the one most often cited later. Anything the tests do
not exercise, any control asserted but never attempted, any environment not tried. A claim
nobody tested is a claim, not a control.>

- **<The unverified thing>** — <why not; what it would take to verify>.

## 6. Follow-ups

| What                | Why it was deferred | Issue |
| ------------------- | ------------------- | ----- |
| <the work>          | <reason>            | #NNN  |

---

_Assisted-By: Claude Code_
