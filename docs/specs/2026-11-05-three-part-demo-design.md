# DESIGN — The three-part ykt1 demo

Version: 1.3 — November 2026 (v1.3: the two helpers and the command sheet are now in-repo and
covered by CI — see §0.3. v1.2: the audit pod ships its own `shared/`, streams progress
live, and surfaces highlighted ids where they can be seen — see §0.2. v1.1: the label is now a
signed CloudEvent attribute, so the audit filters on it directly — see §0.1)
Status: Accepted — partly implemented <!-- the script, both helpers and the command sheet are in-repo and tested; Part 2's BOUND chain is still unexercised -->
Milestone: **Demo 3P**, registered in [the registry](README.md)
Builds on (reuse, no redesign): Phase 1 (`0002-phase1-keda-kubernetes.md`) for the KEDA scale-from-zero path; Phase 2 (`0003-phase2-identity-on-the-event-path.md`) §8.5 for the five questions asked of every control claim below.
Report: `../reports/three-part-demo-report.md` <!-- once the rehearsal in §7 has run -->

> **The one-sentence thesis.** Restructure the ykt1 demo from five flat beats into three
> questions — *does it work, how would you know, what if someone lies* — so that the audit
> tool is read twice, once against honest traffic and once against a forgery, and the second
> reading lands against a baseline the room has already seen.

The previous script (`demo.txt`, five parts) demonstrated the same system but interleaved the
scaling story with the trust story: identity gating was Part 1, scaling Part 2, forgery Part 4,
audit Part 5. A viewer saw the audit tool exactly once, after the forgery, with nothing to
compare it against. This design regroups the existing beats; it does **not** change
EventBridge, EventRunner, the wire contract, or any manifest.

---

## 0. Decisions taken during design

| Question | Decision |
| --- | --- |
| What is driving this work | **The KubeCon run-of-show needs the trust claim to be legible in one pass.** An audit shown once, after an attack, cannot be read as evidence — there is no "before". |
| How many parts, and on what axis | **Three, split by the question each answers** (works / provable / holds). Rejected: keeping five beats grouped by subsystem (the audit's two readings stay far apart, and the identity beat duplicates Part 1's POST); two parts folding audit into the forgery (loses the baseline, which is the whole point). |
| Who drives Part 1 — a human typing `curl`, or an agent | **Headless `claude -p` through the `/eventbridge` skill.** The room sees an agent operating the fleet, which is the talk's subject. Rejected: raw `curl` (works, but demonstrates an HTTP API rather than an agent); interactive `claude` as the primary (kept as the documented fallback — it is better live, worse on camera). |
| Which invocation Part 2 audits | **The single run from §1b, not the 100-agent batch.** One chain fits on screen and every field stays legible. Rejected: auditing the batch (100 chains scroll past; the binding becomes a wall of text). |
| What Part 3 injects | **An unsigned *request* onto the requests topic.** It forges the *work*, so refusal is visible as "woke, declined, left". Rejected: forging a *response* (refused by a consumer that was already running — a weaker picture, and `k8s_demo_flow.py` already asserts it). |
| Whether to drive Part 3 with `k8s_demo_flow.py` | **No — a standalone 40-line injector.** The flow script runs ~15 other assertions, takes minutes, and cannot be narrowed: its `--skip`/`--phase` flags are accepted by argparse and **never consumed** (verified 2026-11-04). Rejected: `--skip 4,14` as the old Part 4 did — it silently did nothing. |
| What the audit is claimed to prove in Part 3 | **Corroboration, not detection.** The *runner* refuses the forgery; the audit shows the attempt is on record. See §3 — the audit's verdict for this case is weaker than the old script claimed. |
| How the audit knows which run to show | **`--label`/`--highlight-label`, resolved off the requests topic.** The label is a signed CloudEvent attribute ([ADR-0003](../adrs/0003-agent-labels-are-signed-event-attributes.md)), so the audit needs no access to EventBridge. Rejected: resolving the label over HTTP (gives the audit a dependency it deliberately lacks); keeping the label store-only ([ADR-0001](../adrs/0001-agent-labels-are-store-only.md), superseded — it left the one tool that most needs a label unable to use one). |

### 0.1 v1.1 corrections

1. **The agent label is a signed CloudEvent attribute (`ce_label`), and the audit filters
   on it directly.** `audit_topic.py` gained `--label` and `--highlight-label`, which
   resolve labels to correlation ids off the requests topic with no access to
   EventBridge. `sessions.label` remains, because `GET /v0/agents?label=` answers a
   different question — "which run was that?" for a human, versus "which events belong to
   that run?" for the audit.

   The original store-only decision was right about the *danger* and wrong about the
   *remedy*: an unsigned label would be forgeable, so the answer was to sign it rather
   than hide it, exactly as `submitter`, `groupid` and `userkey` are. The accepted cost is
   a canonicalisation break — a signing-enabled deployment must upgrade EventBridge and
   EventRunner together.

### 0.2 v1.2 corrections — three defects found by running Parts 2 and 3

All three were found by executing the beats on ykt1 (2026-11-07), not by reading the code,
and all three had the same shape: the tool was *correct* and *illegible*.

1. **The in-cluster audit printed nothing for 30–60 s, then replayed its progress AFTER the
   summary.** `audit_in_cluster` captured the pod to completion and printed
   `res.out + res.err`. Progress goes to the pod's stderr and results to its stdout, so
   concatenating them in that order put every `classified 610/619` line *below* the verdict
   and the four `PASS` lines — reading as though the audit had restarted. Fixed by
   `proclib.run(stream=True)`, which tees stdout→stdout and stderr→stderr as they arrive
   while still capturing both; the two streams stay separate so `… | tail -8` still receives
   only audit output. `read_topic` also reports during the Kafka read, which is where the
   time actually goes now that verification is native (§ADR-0002) — and which was the one
   phase that printed nothing at all.

2. **`--label` crashed in the pod** with `AttributeError: 'Row' object has no attribute
   'label'`. The pod ran the current `audit_topic.py` but imported `shared` from `/app` —
   the eventrunner image. `audit.Row.label` is ADR-0003; the deployed image predates it, so
   new script met old dataclass. `imagePullPolicy: Always` cannot help: the image is current
   in the registry and stale in its *content*. Fixed by shipping the host's own
   `shared/{__init__,ce,keyset,signing,ed25519_native,audit}.py` into the pod, compressed, and
   putting that directory ahead of `/app` on `sys.path`. The audit now verifies with exactly
   the code the operator is looking at — which is also what makes its verdict attributable —
   and no longer requires redeploying the thing it audits.

3. **A `--label` that matched nothing silently became no filter at all**, printing the whole
   topic. `--label` is documented as hiding everything else; resolving to zero ids left
   `corr_ids` empty, so nothing was hidden. That is the dead-flag shape already catalogued in
   §0 for `k8s_demo_flow.py`'s unconsumed `--skip`/`--phase`, and worse here: the operator
   asked for one run and got a thousand. It now aborts with exit 1 and names the likely cause.
   `--highlight-label` still only warns — marking nothing is weaker than hiding everything,
   and the listing it would otherwise print is complete and true.

   **This is also a live gap, not only a fixed bug.** ykt1's EventBridge does not yet carry
   `ce_label` (verified: `shared.ce.EXT_LABEL` absent from the running image), so no event on
   the topic has a label and `--label` cannot work there until it is redeployed. §2.2's
   `--label`/`--highlight-label` claim is therefore **aspirational on ykt1 today**; the demo
   uses `--highlight "$CORR"`, with `$CORR` still resolved *from the label* by the CLI, which
   reads EventBridge's store rather than the topic.

4. **A highlighted chain was invisible on a busy topic.** With ~1000 chains at 24 h retention,
   `--highlight forged-…` marked the row three screens above the summary, between
   `witty-osprey` and `witty-otter`. Dimming 999 others does not help when they scroll past,
   and the operator's report — "it prints lots of output but nothing about `$CORR_FORGED`" —
   was a fair description of a tool that had printed it. Highlighted chains now sort first,
   and a `focus` block after the summary restates each id with its event count, verdicts and
   chain state. An id matching nothing prints `NO EVENTS` by name, so a typo and a quiet
   success stop looking alike.

### 0.3 v1.3 — the helpers come in-repo

`inject-unsigned.sh` and `watch-refusals.sh` were developed in the demo working directory
and ran only by hand, which §4's cons listed as a known weakness: neither was tested nor
reviewed. They are now `scripts/inject-unsigned.sh` and `scripts/watch-refusals.sh`, the
command sheet is `scripts/demo-commands.sh`, and the walkthrough is
[`../demos/ykt1-three-part-demo.md`](../demos/ykt1-three-part-demo.md).

Bringing them in found two defects that hand-running had hidden:

1. **The injector hardcoded its image reference** — `rossoctl-eventrunner:local1` against
   one cluster's internal registry. ykt1 runs `rossoctl-eventrunner-claude:local1`, so the
   pod producing the "byte-identical" forgery was built from a *different image* than the
   runner it impersonates, and the claim in §2.3 rested on the two sharing `shared.ce`. It
   now reads the image from `deploy/eventrunner`. This is the same class of bug
   `tests/test_script_image_refs.py` already existed for, which is where the new gates went.

2. **The command sheet carried one operator's absolute home path in seven places**, plus a
   `. eventing-lab./.demo-env` typo that would have failed at the first beat. Paths now
   resolve from `$REPO`, defaulting to the repo root.

`demo-commands.sh` is a **paste sheet, not a program**, and deliberately has no `set -e`: a
beat that returns non-zero (an expected `400`, an audit that aborts) must not kill the shell
being demoed in. `bash -n` still gates it, so a typo fails CI rather than the performance.

---

## 1. Context

Three forces, all discovered by executing the old script rather than reading it:

1. **The audit had no baseline.** Read once, post-attack, `--chains` output is just a list. Read
   twice, the delta is the argument.
2. **Three pre-flight failures were silent.** The `/eventbridge` skill was not installed, so
   `claude -p` answered from memory instead of failing — indistinguishable from success on
   camera. The CLI reads `$EVENTBRIDGE_TOKEN`, not the `$DEMO_GH_TOKEN` the rest of the demo
   exports. And `kubectl logs deploy/eventrunner -f` does not wait for a pod at zero replicas —
   it exits immediately with `error: timed out waiting for the condition`, so the watch meant to
   capture the refusal was already dead when the forgery landed.
3. **One narration claim was wrong.** The old script said the forgery "shows as ORPHANED". It
   does not; see §3.

## 2. Design

Three parts, ~6½ minutes of screen time, five panes. Pane B is reassigned mid-demo: replica
count is Part 1's story, the refusal line is Part 3's.

### 2.1 Part 1 — does it work? (~3 min)

Idle at zero → one agent → 100 agents from one POST → back to zero. Driven by
`claude -p "/eventbridge run …"`, with the skill's own echoed command line kept on screen so the
invocation is checkable rather than narrated. Pane C (`watch_topics.py`) splits lag into claimed
vs unclaimed; pane E (`ntfy subscribe`) shows the notification feed live, which is what makes
"2 notifications for 100 agents" visible rather than asserted.

### 2.2 Part 2 — how would you know? (~90 s)

`audit_topic.py` twice against the §1b run: once listing events with verdicts, once with
`--chains` for the result→wake binding. On ykt1 today both runs use
`--highlight "$CORR"` rather than `--label`, because the deployed EventBridge does not
yet emit `ce_label` (§0.2 item 3) — the label still does the work of finding `$CORR`, via
the CLI and the store, so no id is typed either way. Run unfiltered — a single run is a handful of events, and
`grep`-ing audit output is how a missing verdict becomes invisible on stage. `--verify` is
deliberately **not** used: it asserts a clean topic, which is correct in CI and wrong here,
because Part 3 is about to put a forgery on that topic.

### 2.3 Part 3 — what if someone lies? (~2 min)

Start the refusal watch, inject one correctly-encoded unsigned request, then re-read the same
audit. The injector runs in a throwaway pod on the eventrunner image and imports the real
`shared.ce` codec, so the event is byte-identical in shape to a genuine wake — keyed by
correlation id, `mode=start`, matching data shape. The only difference is the absent signature.
That matters: the demo must refuse a *correct* forgery, or it proves nothing about the signature
check.

The evidence, verified verbatim on ykt1 2026-11-04:

```text
[consume] rejecting unsigned/badly-signed request corr=demo3-1793813145:
no ce_signature attribute (ER_REQUIRE_SIGNATURE=true) — offset committed, not retried
```

Every clause is a separate claim worth naming: why it was refused, under which policy, and what
became of the event (committed, so not retried forever).

### 2.4 Two new helpers

| Script | Why it exists |
| --- | --- |
| `inject-unsigned.sh` | Part 3b. Extracted from `FORGE_REQUEST_SRC` in `scripts/k8s_demo_flow.py` so the production codec is reused rather than reimplemented — a reimplementation of the guard's input proves less about the guard (Phase 2 §8.6). |
| `watch-refusals.sh` | Part 3a. Polls for the eventrunner pod, tails it, and survives it dying, because the refusing pod lives ~5 s and `kubectl logs -f` cannot wait for it. |

Both now live in `scripts/`, with the command sheet as `scripts/demo-commands.sh` and the
walkthrough as [`../demos/ykt1-three-part-demo.md`](../demos/ykt1-three-part-demo.md) — see §0.3.

## 3. What the audit does and does not catch

Stated plainly because the old script overclaimed it, and because this is exactly the shape of
error Phase 2 §8 catalogues.

- The forged chain reads **`OPEN` · "no terminal yet — still running, or read too early"**, not
  `ORPHANED`. `ORPHANED` means results naming an absent wake; this is the reverse — a wake with
  no results. On a slide, `OPEN` reads as *it's working*.
- The forged request audits **`unsigned-ok`**, not `unsigned`. The verdict flags missing
  signatures only on terminal and group-lifecycle events (`shared/audit.py:148`); a request is
  neither, so the row is indistinguishable from a legitimately-unsigned streamed frame.

So the detector is the **runner**, and the audit's narrower job is to show the attempt is on
record and the chain unanswered. Claiming otherwise would be the unchecked control claim the
talk is about.

## 4. Pros and cons

**Pros**

- The audit is read twice, so Part 3's finding has a baseline; this is the structural change.
- Each part answers one question, so a part can be cut for time without orphaning another.
- Part 1 shows an agent driving the fleet, which is the talk's subject, not an HTTP API.
- Part 3 is seconds and one pod instead of a multi-minute full-flow run, and is re-runnable
  mid-rehearsal with a fresh correlation id.
- Nothing in the system changed — no manifest, no code, no wire contract — so the demo cannot
  drift from what the repo does.

**Cons**

- **Four pre-flight dependencies, three of them silent when wrong** (skill installed, token
  variable bridged, token unexpired). Mitigated by pre-flight checks that print the failure, but
  the failure modes remain quiet by nature.
- **Parts 2 and 3 depend on Part 1 having just run.** Topic retention is 24 h, so an audit run
  cold shows an empty topic. The ordering is load-bearing, not presentational.
- **Part 3's strongest artifact is a log line**, which is less visually legible than a replica
  count, and it is gone ~5 s after the pod drains.
- ~~**Two helper scripts live outside the repo**~~ — fixed in v1.3; both are in `scripts/`
  with syntax, path and behaviour gates in `tests/test_script_image_refs.py`.
- `--chains` state vocabulary does not have a word for "a wake nobody answered because it was
  refused", so the narration has to supply what the tool's output does not.

## 5. What this does not do

- ~~**It does not upstream the helpers.**~~ **Done in v1.3** — see §0.3. Both helpers, the
  command sheet and the walkthrough are in-repo and covered by CI.
- **It does not fix the audit verdict for forged requests.** Extending the `unsigned` verdict to
  non-terminal requests when `ER_REQUIRE_SIGNATURE=true` would make Part 3d self-evident, but it
  changes `shared/audit.py` semantics and belongs behind its own design and tests.
- **It does not add the identity gate as its own part.** The old Part 1 (401 / bad token /
  approved user) is cut for time; `k8s_demo_flow.py --gh-login` still asserts it, and
  `EB_ALLOWED_USERS` is visibly enforced in Part 1's successful POST.
- **It does not make replies real.** The runner mounts no Anthropic credential, so it
  auto-selects mock mode (`eventrunner/config.py`, `has_api_credentials`) and replies are
  `MOCK-REPLY[...]`. Scaling, signing and audit are all genuine; only the agent's text is not.
- **It does not remove the `--skip`/`--phase` dead flags** from `k8s_demo_flow.py`. They are
  documented as dead at the foot of the script so they are not copied again.

## 6. Risks

| Risk | Signal that it is happening | Mitigation / fallback |
| --- | --- | --- |
| `/eventbridge` skill not visible to `claude -p` | A plausible answer with no echoed command line or endpoint line | Pre-flight #3 asks the model directly and expects `YES`; run `claude` from `eventing-lab/` |
| `DEMO_GH_TOKEN` expired | `401`, or `✗ not signed in` from the CLI | Pre-flight #2 calls `api.github.com/user`; refresh via `scripts/login.py` and rewrite `.demo-env` |
| `$EVENTBRIDGE_TOKEN` unset | Every skill-driven POST fails while `curl` beats still work | Pre-flight #3b exports it from `$DEMO_GH_TOKEN` |
| Refusal line missed in Part 3 | Empty `refusals.log` after injection | `watch-refusals.sh` started **before** injecting; it re-attaches across pods |
| VPN drops mid-demo | `lookup api.ykt1… no such host`; the in-cluster audit pod times out | Pre-flight #1; observed once while building this (2026-11-04) |
| Ingress drops POSTs (~40%, eventing-lab#9) | A batch silently short of its count | Port-forward on `127.0.0.1:18080` everywhere; never the Route's `html_url` |
| `OPEN` misread as success on camera | — | §3 narration pairs 3d with the 3c refusal line |

## 7. Test plan

The demo *is* the test, so "proved" means executed against ykt1. As of 2026-11-04:

| Beat | State |
| --- | --- |
| Part 3 end to end — inject, pod 0→1, refuse, 0 in ~5 s; refusal line captured; `400` on the forged transcript; audit re-read | **executed** |
| `audit_topic.py`, both views, against the live keyset (kids `eb-01`, `runner-01`) | **executed** |
| Signing enforced both directions (`ER_`/`EB_REQUIRE_*_SIGNATURE=true`) | **executed** |
| ntfy round trip on the rotated topic; ntfy CLI 2.37.2 | **executed** |
| Headless `claude -p` resolving the skill | **executed** (after installing it) |
| Part 1's POSTs, and Part 2's `BOUND` chain | **not executed** — blocked by the expired token |
| Part 2/3 audit: live progress, shipped `shared/`, highlighted-first chains, `focus` block | **executed** 2026-11-07 against ykt1 (`--chains --verify --highlight forged-…`: 4 PASS, forged chain first, focus block correct) |
| `--label` on the audit against ykt1 | **executed, and it correctly ABORTS** — the deployed EventBridge emits no `ce_label` (§0.2 item 3). Re-run after redeploying both services together per ADR-0003. |

**The one open gap** is Part 2's `BOUND`: with no genuine signed wake on the topic, the only
chains observed were the two forged ones, both `OPEN`. Refresh the token and rehearse §1b → §2b
once. If it returns `result-only` or `open`, the response-side signing path is where to look.

Measured timings and the rehearsal outcome belong in a report, not here.
