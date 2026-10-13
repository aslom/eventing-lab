# <Title> — Design

Version: 1.0 — <Month Year>
Status: Proposed <!-- Proposed → Accepted → Implemented → Superseded by <link> / Deprecated -->
Milestone: **<ID>**, registered in [the registry](README.md)
Builds on (reuse, no redesign): <<ID> (`<file>.md`) — what is reused, one line — or "nothing">
Report: <`../reports/<topic>-report.md` — make it a real link once the phase lands>

> **The one-sentence thesis.** <The entire design in one sentence, in the active voice. If you
> cannot write this sentence, the design is not settled — keep designing, don't start writing.>

<One short paragraph: what exists today, and what this changes. Name the deployment paths or
components affected, and any that are deliberately untouched.>

---

## 0. Decisions taken during design

<The highest-value section in the document. Each row: the question that had to be settled, the
answer, and the options rejected with a reason. A decision with no rejected alternative was
not a decision — it was a default.>

| Question                        | Decision                                                                                       |
| ------------------------------- | ---------------------------------------------------------------------------------------------- |
| What is driving this work       | **<The forcing function.>** <Why now.>                                                         |
| <The central design question>   | **<The choice.>** Rejected: <option> (<why not — one line>); <option> (<why not>).             |
| <A scope question>              | **<In or out.>** <What it would take to add later, if out.>                                     |

### 0.1 v1.1 corrections

<Delete this section until there are corrections. Then, for each: state the NEW fact first,
then why the original was wrong. The reader wants current truth first, history second.>

---

## 1. Context

<What forces this now: the constraint, the limit hit, the thing that does not work today.
Facts and forces — not the answer. Enough that a reader a year from now understands why this
was even a question.>

## 2. Design

<The design proper. Use numbered subsections (§2.1, §2.2) so reviewers and later specs can
cite them. Include the concrete artifacts: schemas, interfaces, wire formats, sequence
diagrams, config keys with their defaults. An abstract design cannot be reviewed.>

### 2.1 <Component or flow>

## 3. <Further sections as the design needs>

---

## N. What this does not do

<The scope deliberately left out, and what would have to change to add it. This section is
worth more than another design section: it is what stops someone promising this work does
something it does not.>

- **<Thing it does not do>** — <why out of scope; what would be needed>.

## N+1. Risks

| Risk            | Signal that it is happening | Mitigation / fallback |
| --------------- | --------------------------- | --------------------- |
| <What could go wrong> | <What you would observe> | <What you would do>   |

## N+2. Test plan

<What proves this works, at what level, and what remains unverified after it passes. The
measured outcome belongs in the report, not here.>

---

_Assisted-By: Claude Code_
