# Implementation plans (local-only, gitignored)

Plans that describe **how** to build something — ordered steps, checklists, task
breakdowns — live here **on your machine only**. Everything in this directory except this
README is gitignored (see the repo `.gitignore`), and CI fails the build if anything else
is committed here.

## Why plans aren't committed

A plan answers _how, in what order_. The moment the code merges, the code becomes the source
of truth for _how_ — the plan is now a stale, lower-fidelity copy of it. The durable value of
our docs is the _**why**_ and the _**what actually happened**_. Those live in:

- **[`../specs/`](../specs/)** — design docs (_what & why_, in depth; revised in place).
- **[`../adrs/`](../adrs/)** — permanent decision records (_one decision + consequences_).
- **[`../reports/`](../reports/)** — what a phase built and measured (_append-only_).

So a plan's whole life is: write it → execute it → **delete it**. Committing it would just
create a maintenance burden that goes stale and misleads.

If a plan contains something you want to keep, it belongs in one of the three above. Ask which
one:

- a decision you made while planning → an **ADR**
- a design detail the spec was missing → **edit the spec**
- a number you measured while executing → a **report**

## Workflow

1. Write your plan here: `docs/plans/YYYY-MM-DD-<topic>.md` (named for the spec it
   implements).
2. Execute it.
3. **Delete it** once the work is coded and merged, after moving anything durable into a spec,
   ADR or report.

---

_Assisted-By: Claude Code_
