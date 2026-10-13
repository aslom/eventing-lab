# Notes — investigations, runbooks and dead ends

The home for writing that is **too durable to delete, too small or too specific for a spec**.
A note is dated, narrow, and left alone once written.

## What belongs here

- **Investigation notes** — `YYYY-MM-DD-<topic>.md`. What you found while chasing something
  down, enough that the next person does not repeat the chase.
- **Dead ends** — `<topic>-dead-ends.md`. The approaches that did **not** work, and why. This
  is some of the highest-value documentation in a repo and almost nobody writes it: it is the
  only thing that stops the next person spending a week on the path you already eliminated.
- **Facts files** — `<thing>-facts.md`. Pinned details about an external dependency that are
  hard to rediscover: an API's undocumented behaviour, an image's layout, a version's quirk.
- **Runbooks** — `<topic>-runbook.md`. Reference procedures you consult rather than perform.
  If it is meant to be read aloud while typing, it is a [demo](../demos/) instead.
- **Campaign results** — raw measurement runs that back a [report](../reports/) but are too
  bulky to inline.

## What does not belong here

| If it is…                                  | It goes in                      |
| ------------------------------------------ | ------------------------------- |
| a design for something not yet built       | [`../specs/`](../specs/)        |
| a decision with consequences               | [`../adrs/`](../adrs/)          |
| what a phase built and measured            | [`../reports/`](../reports/)    |
| a sequence of steps to build something     | [`../plans/`](../plans/) (local) |
| something you will perform for an audience | [`../demos/`](../demos/)        |

`notes/` is the right answer when none of the above fit — not the default when you cannot
decide. If a note grows past a few pages and starts describing a design, promote it to a spec.

## Conventions

- **Date-prefix anything time-bound** (`2026-10-07-…`); omit the date for reference material
  that stays true (`<thing>-facts.md`).
- **Open with one line on what the note is for** and whether it is still current. A note
  nobody can date is a note nobody can trust.
- **Notes are not maintained.** Unlike a spec, a note is a point-in-time record — it is fine
  for it to age, as long as it says when it was written.

---

_Assisted-By: Claude Code_
