# The ykt1 three-part demo

The OpenShift (`ykt1`) run-of-show, restructured from five flat beats into **three
questions**: does it work, how would you know, what if someone lies. The design and the
rejected alternatives are in
[`../specs/2026-11-05-three-part-demo-design.md`](../specs/2026-11-05-three-part-demo-design.md);
this file is the walkthrough, and
[`../../scripts/demo-commands.sh`](../../scripts/demo-commands.sh) is the paste-one-block-at-a-time
command sheet that goes with it.

Parts 2 and 3 read the **same** audit command, so Part 3's finding lands against a
baseline the room has already seen. That re-reading is the structural point of the split.

## The three parts

| Part | Question | Time | What the room sees |
| --- | --- | --- | --- |
| 1 | Does it work? | ~3 min | idle at zero → one agent → 100 from one POST → back to zero |
| 2 | How would you know? | ~90 s | every result chained back to the signed wake that caused it |
| 3 | What if someone lies? | ~2 min | an unsigned wake is refused; the attempt stays on record |

## The two helper scripts

Part 3 needs two things `kubectl` cannot do on its own.

| Script | Why it exists |
| --- | --- |
| [`../../scripts/watch-refusals.sh`](../../scripts/watch-refusals.sh) | `kubectl logs deploy/eventrunner -f` **fails at zero replicas** — it exits immediately with `error: timed out waiting for the condition` rather than waiting for a pod. The refusing pod then lives ~5 s, so by the time you notice the watch died the evidence is gone with it. This polls for the pod, tails it, and keeps going when it dies. |
| [`../../scripts/inject-unsigned.sh`](../../scripts/inject-unsigned.sh) | Publishes one **correctly-encoded but unsigned** request. It runs in a throwaway pod on the eventrunner's own image and imports the real `shared.ce`, so the event is byte-identical in shape to a genuine wake and the only difference is the absent signature. The demo must refuse a *correct* forgery, or it proves nothing about the signature check. |

Both narrate while they work, which is not decoration. A script that prints one line and
then stalls for 20 s inside `kubectl run` looks broken, and the operator cannot tell a slow
image pull from a hung cluster. `inject-unsigned.sh` prints a five-step arc with the
in-pod progress; `watch-refusals.sh` prints a heartbeat with the replica count, because a
watcher waiting correctly and a watcher that has died otherwise look identical.

```sh
export KC=/path/to/kubeconfig-ykt1 NS=kev1

# pane A — start this FIRST, or the refusal is missed
./scripts/watch-refusals.sh                      # QUIET=1 to drop the heartbeat
                                                  # stop: kill -- -"$(cat "$OUT".pid)"
# pane B
CORR_FORGED="forged-$(date +%s)"
./scripts/inject-unsigned.sh "$CORR_FORGED"
```

`watch-refusals.sh` writes its PID to a file and must be killed as a process **group** —
it spawns `kubectl logs -f | grep | tee`, and killing only the parent leaves those children
tailing a pod and appending to the log.

### What each one asserts before it runs

`inject-unsigned.sh` **exits 1 if `ER_REQUIRE_SIGNATURE` is not `true`**. Without
enforcement the beat is vacuous: the forgery would simply be accepted and run, and the
narration would describe a control that is switched off. It also asserts the event it
built carries no signature, because a bug that accidentally signed it would make the beat
narrate a refusal that never happened.

The image is read from `deploy/eventrunner` rather than hardcoded, so the forgery is built
from the same codec as the real runner. A hardcoded ref was wrong twice over — it pinned
one cluster's internal registry, and it named `rossoctl-eventrunner` while ykt1 runs
`rossoctl-eventrunner-claude`.

## Part 3's evidence, verbatim

```text
[consume] rejecting unsigned/badly-signed request corr=forged-1794078678:
no ce_signature attribute (ER_REQUIRE_SIGNATURE=true) — offset committed, not retried
```

Every clause is a separate claim worth naming: **why** it was refused, under **which**
policy, and **what became of the event** — committed, so it is not retried forever, which
is what a naive refusal would do.

Say *"it woke up, declined to work, and went away."* Not "nothing happened": KEDA does
scale `0 → 1` on the lag, and anyone watching `get deploy` will catch the overclaim.

## What the audit does and does not catch

Stated plainly because an earlier version of this script overclaimed it.

- The forged chain reads **`OPEN`** — "no terminal yet, still running, or read too early" —
  **not `ORPHANED`**. `ORPHANED` means results naming an absent wake; this is the reverse,
  a wake with no results. On a slide, `OPEN` unfortunately reads as *it's working*.
- The forged request audits **`unsigned-ok`**, not `unsigned`. The verdict flags missing
  signatures only on terminal and group-lifecycle events, and a request is neither.
- `--verify` still **exits 0** with the forgery on the topic, and that is the stronger
  beat rather than a gap: an unsigned request never became a response, so no terminal is
  missing, nothing is contested and no chain is orphaned.

So the detector is the **runner**. The audit's narrower job is to show the attempt is on
record and the chain unanswered — lead with the refusal line and use the audit as
corroboration. Claiming the audit detected the forgery would be exactly the unchecked
control claim the talk is about.

**Do not reach for `--corr` here.** It hides every event it was not given, so filtering to
the honest run would make the forgery vanish — a clean-looking audit seconds after
demonstrating an attack is worse than showing no audit at all. `--highlight` narrows
attention; `--corr` narrows evidence.

## Known gap on ykt1

`audit_topic.py --label` cannot work against ykt1 until EventBridge is redeployed: the
running image emits no `ce_label`, so no event on the topic carries one
([ADR-0003](../adrs/0003-agent-labels-are-signed-event-attributes.md)). The audit aborts
with exit 1 and says so rather than silently printing the whole topic. Use
`--highlight "$CORR"`, where `$CORR` is still resolved *from the label* by the CLI against
EventBridge's store — so no correlation id is typed by hand either way.

Because `label` is in `SIGNED_ATTRS`, that redeploy must upgrade **EventBridge and
EventRunner together**; ykt1 enforces signing in both directions, so a staggered rollout
means total refusal of genuine traffic until both are current.

---

*Assisted-By: Claude Code*
