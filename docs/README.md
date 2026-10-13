# eventing-lab documentation

This directory is the repo's documentation spine. Five kinds of document live here, each
with a different lifecycle — the table below is the router, and
[§ Documentation lifecycle](#documentation-lifecycle) is the rule that decides where a new
document goes.

## Start here

| If you want to…                                      | Read                                                 |
| ---------------------------------------------------- | ---------------------------------------------------- |
| Understand the whole system in one sitting           | [`eventing-lab-overview.md`](eventing-lab-overview.md)                            |
| Know **why** a decision was made                     | [`adrs/`](adrs/) — one decision per file, permanent  |
| Get the **full design** of a subsystem               | [`specs/`](specs/) — deep dives, living              |
| Know what a phase **actually built and measured**    | [`reports/`](reports/) — append-only, never rewritten |
| **See it work**, step by step                        | [`demos/`](demos/) — performed walkthroughs          |
| Look up a term                                       | [`glossary.md`](glossary.md)                         |
| Find an investigation note, runbook or dead end      | [`notes/`](notes/)                                   |
| Install or run it                                    | [root README](../README.md)                          |

Implementation plans live in [`plans/`](plans/) and are **local-only** — gitignored by
design. See that directory's README for why.

## Documentation lifecycle

Code is the source of truth for _how_. The durable value of these docs is the _**why**_ —
decisions and the alternatives we rejected — and the _**what actually happened**_ — measured
results, including the parts that went wrong.

| Artifact     | Answers                                           | Retention                                                      | Home                        |
| ------------ | ------------------------------------------------- | -------------------------------------------------------------- | --------------------------- |
| **Overview** | the whole picture, briefly                        | committed, **kept current**                                    | [`eventing-lab-overview.md`](eventing-lab-overview.md) |
| **Spec**     | _what & why_, in depth — alternatives, trade-offs | committed, **revised in place** with a version changelog       | [`specs/`](specs/)          |
| **ADR**      | one significant decision + consequences           | committed, **permanent & immutable**                           | [`adrs/`](adrs/)            |
| **Report**   | what a phase built, **measured**; deltas; findings | committed, **append-only — never rewritten**                   | [`reports/`](reports/)      |
| **Demo**     | how to show it working, out loud                  | committed, updated when the commands change                    | [`demos/`](demos/)          |
| **Note**     | an investigation, a runbook, a dead end           | committed, dated, left as-is                                   | [`notes/`](notes/)          |
| **Plan**     | _how, in what order_                              | **local-only, ephemeral** — delete once coded; never committed | [`plans/`](plans/)          |

### Specs revise; reports do not

This is the distinction that most often gets blurred, so it is stated once, here:

- A **spec** is written **before** the code and says what _will_ be true. When reality
  corrects it, you **edit the spec** and record the correction in its version changelog
  (`Version: 1.3 — v1.1: corrections from the implementation; v1.2: …`). A reader should be
  able to open the current spec and trust it.
- A **report** is written **after** a phase lands and says what _is_ true, measured. It is
  **never corrected away.** If a later phase changes the answer, write a new report. A report
  that quietly loses its wrong prediction has destroyed the only evidence that the prediction
  was wrong.

So: the spec tells you what the system is meant to do; the report tells you what it did when
someone ran it, and what that cost.

### Status vocabulary

Every overview, spec, ADR and report carries a `Status:` line drawn from:

> `Proposed` → `Accepted` → `Implemented` → `Superseded by <link>` (or `Deprecated`)

When a design is replaced, set the old document's status to `Superseded by <link>` rather
than deleting it. The record of what we once thought, and why we changed, has value.

## Conventions

- **Spec filenames are dated and descriptive:** `YYYY-MM-DD-<topic>-design.md`. If the repo
  uses canonical milestone ids, they live in [`specs/README.md`](specs/README.md), not in the
  filename — that avoids rename churn when a plan is renumbered.
- **ADR filenames are numbered:** `NNNN-<kebab-title>.md`, zero-padded, next free number,
  never reused.
- **Report filenames mirror their spec:** `<topic>-report.md`, or
  `phase<N>-<topic>-report.md` for phase-structured work.
- **Cross-link relatively** (`../adrs/0004-foo.md`). CI checks that these resolve, so a
  rename that breaks a link fails the build rather than rotting silently.


The documents below are this project's own: five phase designs, the reports that measured
them, and the runbooks that drive them. They came over from `rossoctl/examples` with the
code and were remapped onto the convention above.

## Reading order — the eventing phase documents

- **Deciding whether to fund, demo or depend on this?**
  [`eventing-lab-overview.md`](eventing-lab-overview.md) — what each phase
  decided, what is built as opposed to designed, and the KubeCon commitment with its
  gaps. It cites these documents rather than restating them, and it is the only one
  written for a reader who will not open the code.
- **Running it?** [`demos/phase1-demo.md`](demos/phase1-demo.md), or the [Phase 1
  section](../README.md#phase-1--kubernetes-and-keda) of the component README for
  the short version.
- **Changing it?** [`specs/0001-phase0-local-wire-contract.md`](specs/0001-phase0-local-wire-contract.md) for the wire contract that
  must not change, then [`specs/0002-phase1-keda-kubernetes.md`](specs/0002-phase1-keda-kubernetes.md) for the deployment
  model.
- **Working on auth or identity?** [`specs/0003-phase2-identity-on-the-event-path.md`](specs/0003-phase2-identity-on-the-event-path.md) §2.1 first:
  the opaque-token constraint is what rules out the design most people reach for.
- **Debugging something odd in the tenancy code?**
  [`reports/phase3-report.md`](reports/phase3-report.md) §4 — the findings are there with
  the symptom that hid each one, and §5 lists what is not verified at all.
- **Debugging something odd?** [`reports/phase1-report.md`](reports/phase1-report.md)
  §4 — most surprises are already recorded there with their cause.
- **Writing a claim about a control, in any phase?**
  [`specs/0003-phase2-identity-on-the-event-path.md`](specs/0003-phase2-identity-on-the-event-path.md) §8.5 for the five questions to ask before it
  ships, then §8.1-§8.3 for what thirteen such claims in this repository got wrong and
  how. The findings are Phase 2's; the questions are not.
- **Presenting it?** [`specs/0003-phase2-identity-on-the-event-path.md`](specs/0003-phase2-identity-on-the-event-path.md) §2.6 and §3, for what the
  identity controls do _not_ prove — and §8, because four of them promise more than
  the code delivers.
- **Working on the KubeCon talk?** [`notes/kubecon-na-2026.md`](notes/kubecon-na-2026.md) — §2 for
  what is and is not backed by code, §3 for the SPIRE decision that has to be made
  first, §5 for the 30-minute shape. §7 records what the review that produced it found
  that no single document states.
- **Working on multi-tenancy, agent definitions or triggers?**
  [`specs/0004-phase3-tenancy-agents-triggers.md`](specs/0004-phase3-tenancy-agents-triggers.md) — §9 for the rollout order (the first four steps are
  refactors; everything risky is behind a flag that defaults off), §3.6 for what isolation does
  and does not claim without Kafka ACLs, and §7.7 before writing any of the trigger code.
- **Working on notifications, enrollment or human-in-the-loop approval?**
  [`specs/0005-phase4-enrollment-and-permission.md`](specs/0005-phase4-enrollment-and-permission.md) §2 first — it is the measured mechanism the rest
  of the phase rests on, and §2.1 rules out the design the documentation points at. Then
  §5.4 for the control that matters most (the approval is bound to the tool input, not
  just the approval id) and §9.4 for the hole that remains.

The `PHASE0` documents describe a laptop demo and remain accurate for that; where
Phase 1 supersedes them it says so explicitly rather than editing them in place.

---

_Assisted-By: Claude Code_
