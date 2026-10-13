# <PROJECT> documentation

This directory is the repo's documentation spine. Five kinds of document live here, each
with a different lifecycle — the table below is the router, and
[§ Documentation lifecycle](#documentation-lifecycle) is the rule that decides where a new
document goes.

## Start here

| If you want to…                                      | Read                                                 |
| ---------------------------------------------------- | ---------------------------------------------------- |
| Understand the whole system in one sitting           | `<PROJECT>-overview.md`                              |
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
| **Overview** | the whole picture, briefly                        | committed, **kept current**                                    | `docs/<PROJECT>-overview.md` |
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

---

_Assisted-By: Claude Code_
