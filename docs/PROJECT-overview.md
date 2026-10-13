# <PROJECT> — Executive Overview

**Status:** <one line: what is built, what is deferred>
**Repo:** `rossoctl/<PROJECT>`
**Date:** YYYY-MM-DD

> **Rename this file to `<project>-overview.md`** and update the link in
> [`README.md`](README.md).

---

This document carries **all the essential information** about <PROJECT>, at a depth a reader
can absorb in one sitting. Every claim in it is covered in detail by a document in
[`specs/`](specs/) — this is the map; those are the territory.

Unlike a spec, this file is **kept current.** When a design changes, update this document in
the same PR.

## 1. What it is, and what problem it solves

<Two or three paragraphs. The problem first, in terms someone outside the project
understands — then the approach, then what makes it different from the obvious alternative.
Avoid internal jargon on first use; link [the glossary](glossary.md).>

## 2. Architecture

<An ASCII or Mermaid diagram of the real components and the traffic between them. Keep it to
one screen — this is the orientation diagram, not the complete one. Detail lives in the specs.>

```
┌──────────────┐        ┌──────────────┐
│  <component> │ ─────▶ │  <component> │
└──────────────┘        └──────────────┘
```

### Key components

| Component     | Role                      | Spec                                       |
| ------------- | ------------------------- | ------------------------------------------ |
| **<name>**    | <one line>                | `specs/<file>.md`                          |

## 3. How it works

<The main flow end to end, numbered. One paragraph per step. A reader should be able to follow
a single request or event through the whole system from this section alone.>

## 4. The decisions that shape it

<The three to six load-bearing choices, each one line, each linking its ADR. Not a full list —
that is [`adrs/`](adrs/). These are the ones you would mention in the first ten minutes of
explaining the system to a new engineer.>

| Decision          | Why                  | ADR                                       |
| ----------------- | -------------------- | ----------------------------------------- |
| <the choice>      | <the reason>         | `adrs/0001-<slug>.md`                     |

## 5. Current state

| Capability   | Status                                | Evidence                                      |
| ------------ | ------------------------------------- | --------------------------------------------- |
| <capability> | Implemented / Partial / Design only   | `reports/<file>.md`                           |

<Be exact here. "Partial" with a pointer to what is missing is useful; "Implemented" for
something only half-wired is the single most expensive kind of documentation error, because
people build plans on it.>

## 6. What it does not do

<The limits, stated plainly, and what it would take to lift each. This is the section that
stops someone promising this system does something it does not — the most valuable section in
the document for anyone presenting the work.>

- **<Limit>** — <why; what would be needed>.

## 7. Where to go next

| If you want to…              | Read                                            |
| ---------------------------- | ----------------------------------------------- |
| Run it                       | [root README](../README.md)                     |
| See it work                  | [`demos/`](demos/)                              |
| Change a subsystem           | the relevant [`specs/`](specs/) doc             |
| Know why something is so     | [`adrs/`](adrs/)                                |
| Know what was measured       | [`reports/`](reports/)                          |

---

_Assisted-By: Claude Code_
