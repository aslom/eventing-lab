# <PROJECT> — agent instructions

<One paragraph: what this repo is, and the one thing an agent most needs to know before
changing it.>

## Repository layout

```
<PROJECT>/
├── docs/              # the documentation spine — see docs/README.md
└── <your code>/
```

## Documentation — where things go

This repo follows the rossoctl docs convention. **Read [`docs/README.md`](docs/README.md)
before writing any document**; the rule that decides where a document goes is there. The short
version:

| You are writing…                            | It goes in                                        | Lifecycle                       |
| ------------------------------------------- | ------------------------------------------------- | ------------------------------- |
| a design for something not yet built        | `docs/specs/YYYY-MM-DD-<topic>-design.md`         | revised in place, versioned     |
| one decision and its consequences           | `docs/adrs/NNNN-<kebab-title>.md`                 | **permanent, immutable**        |
| what a phase built and measured             | `docs/reports/<topic>-report.md`                  | **append-only, never rewritten** |
| ordered steps to build something            | `docs/plans/YYYY-MM-DD-<topic>.md`                | **LOCAL ONLY — never commit**   |
| a walkthrough to perform for an audience    | `docs/demos/<name>-demo.md`                       | updated when commands change    |
| an investigation, runbook or dead end       | `docs/notes/`                                     | dated, left as-is               |
| the whole system, briefly                   | `docs/<project>-overview.md`                      | **kept current**                |

### The rules that are easy to get wrong

1. **Plans are never committed.** `docs/plans/` is gitignored except its README, and CI fails
   if anything else lands there. A plan's life is: write it → execute it → **delete it**.
   Before deleting, move anything durable out: a decision → an ADR; a design detail → edit the
   spec; a measured number → a report.

2. **Specs are revised; reports are not.** When the implementation teaches you something,
   **edit the spec** and log the correction in its `Version:` changelog. Never retro-edit a
   report — a report that loses its wrong prediction has destroyed the evidence that the
   prediction was wrong. Write a new one instead.

3. **ADRs are immutable.** To change a decision, write a new ADR that supersedes the old one;
   only the `Status:` line of an existing ADR may be edited.

4. **Copy the template, don't improvise.** `docs/specs/0000-spec-template.md`,
   `docs/adrs/0000-adr-template.md`, `docs/reports/0000-report-template.md`. CI checks the
   required headers.

5. **Update the index.** Adding a spec, ADR or report means adding its row to that
   directory's `README.md`. The indexes are how anyone finds these documents.

6. **Name the rejected alternatives.** In a spec's `§0 Decisions` table and an ADR's
   `Alternatives considered`. A decision recorded without the options it beat is an
   announcement, not a decision.

### Writing style for docs

- **Be specific and falsifiable.** "Reduced latency" is worthless; "p99 fell from 840 ms to
  210 ms on the 3-node rig, 20 runs" can be checked.
- **State limits plainly.** Every spec gets a "What this does not do"; every report gets a
  "What is NOT verified". These are the sections readers cite later, and the ones that stop
  someone over-promising the work in a room.
- **Write the correction as the new fact**, not as "we said X but actually Y". Current truth
  first, history second.
- **Cross-link relatively** (`../adrs/0004-foo.md`). CI checks these resolve.

## Commits and PRs

- Commit trailer is **`Assisted-By:`**, never `Co-Authored-By:` — the `commit-msg` hook in
  `scripts/hooks/` rewrites it automatically. Install hooks with `pre-commit install
  --hook-type commit-msg`.
- Governance, contribution process and the code of conduct live in
  [`rossoctl/rossoctl`](https://github.com/rossoctl/rossoctl); see
  [`GOVERNANCE.md`](GOVERNANCE.md).

## Checks to run before pushing

```sh
./scripts/check-no-committed-plans.sh   # nothing committed under docs/plans/
./scripts/check-doc-headers.sh          # required headers present
pre-commit run --all-files              # markdownlint, gitleaks
```

---

_Assisted-By: Claude Code_
