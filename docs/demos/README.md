# Demos — guided walkthroughs

Hands-on tours you **drive by hand**, one command at a time, explaining as you go. Each one
shows something a conventional setup cannot do, and is structured in _acts_ so it survives
being performed live in front of an audience.

## What lives here

| Demo                                                           | Shows                                                                            |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| [`phase0-demo.md`](phase0-demo.md)                             | A Kafka message wakes an agent on one laptop, and every step streams back out    |
| [`phase1-demo.md`](phase1-demo.md)                             | The same path on Kubernetes, scaling from zero agent replicas on consumer lag    |
| [`kubecon-na-2026-demo.md`](kubecon-na-2026-demo.md)           | The conference run-of-show on the signed path, every act marked doable or not    |
| [`kubecon-na-2026-screenplay.md`](kubecon-na-2026-screenplay.md) | The six commands actually recorded for the talk, and the order to cut them in  |

All four are runbooks you read while typing, which is why they live here rather than in
[`../notes/`](../notes/). `phase1-demo.md` covers all four ways to run it — locally without
containers, under Docker, on Kind, and on OpenShift — `phase0-demo.md` is still the more
detailed account of the laptop path, and `kubecon-na-2026-demo.md` is the signed path on Kind
with each act marked for whether it can be recorded today.

**The last two are a pair and the split is deliberate.** The run-of-show is the reference:
every act, what it does _not_ prove, the limits to say out loud. The screenplay is the shot
list — six beats, ~4 minutes, nothing else — because a document that answers "why does this
beat exist" is the wrong thing to hold while a camera is running. Change a command in one and
check the other.

## Demo vs. smoke test vs. spec

These three overlap and are easy to confuse:

- **A demo (here)** is _performed_. It optimizes for a human explaining a claim out loud, so
  it narrates why each step matters and deliberately pauses on the moments that are hard to
  believe. Every command is copy-pasteable.
- **A smoke test** is _asserted_. It optimizes for an unattended pass/fail with no narration.
  Most demos here should have a scripted sibling. Prefer the script when you want a pass/fail;
  prefer the demo when you want to _convince someone_.
- **A spec or ADR** ([`../specs/`](../specs/), [`../adrs/`](../adrs/)) records the _**why**_ —
  the decision and its rejected alternatives. A demo shows the _what_, and goes stale when the
  commands change; a decision record does not.

A **runbook** — "how to stand this up in environment X" — is a demo's close cousin. If it is
meant to be read while typing, it belongs here; if it is a reference you consult rather than
perform, it belongs in [`../notes/`](../notes/).

## Conventions

If you add a demo, follow the shape of the existing ones:

- **Open with the claim**, then a table of "what the normal thing needs / what this needs". A
  reader should know in fifteen seconds whether this demo is the one they want.
- **Acts, with lettered sub-steps** (`### 1a.`, `### 1b.`) so you can resume mid-performance
  and so a reviewer can cite a step.
- **Blockquote callouts (`>`) for the narration** — what to _say_, and which traps the step
  defends against. Keep them out of the code blocks so the commands stay copy-pasteable.
- **Show expected output** inline. A demo whose output you cannot compare against is a demo
  that has silently rotted.
- **State the prerequisites once, up front** — cluster, credentials, env vars — with the
  command that verifies each. A demo that fails in act 3 for a missing variable wastes the
  room's attention.
- **End with "What just happened"** (recap the claims, numbered) and **"Cleanup"** (leave the
  environment as you found it — restore any env you flipped _first_).
- **Be honest about limits.** A closing "Notes and limits" section naming what the demo does
  _not_ show is worth more than an extra act, because it is what stops someone
  over-promising in a room.

---

_Assisted-By: Claude Code_
