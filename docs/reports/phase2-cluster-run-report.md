# IMPLEMENTATION REPORT — Phase 2, the signed path on a cluster

Status: Partial <!-- acts 1-7 verified on Kind; act 8 cut, SPIRE absent, no in-cluster signing cost -->
Date: 2026-10-14
Spec: [`../specs/0003-phase2-identity-on-the-event-path.md`](../specs/0003-phase2-identity-on-the-event-path.md)
Covers: §4.4 request and response signing, end to end against a live broker — the execution [eventing-lab#2](https://github.com/rossoctl/eventing-lab/issues/2) was opened for

> **What this report says in one sentence.** The signed event path ran against a Kind cluster
> with enforcement on in both directions — **54 of 55 assertions passed** — and getting there
> surfaced **seven bugs that no unit test could reach, every one of them sitting behind a green
> local report**.

**Append-only.** This document is not rewritten when a later phase changes the answer — write
a new report or append a dated section. See
[the lifecycle note](../README.md#specs-revise-reports-do-not).

---

## 1. What landed

The deliverable was an **execution**, not a build: the code under test was already merged and
unit-tested. What did not exist was any record of it running against a real broker, and
therefore any on-cluster number at all.

| Spec ref | What it was | Verified on cluster | Where |
| --- | --- | --- | --- |
| §4.4 step 1 | signed request wakes the runner | Yes | act 2 |
| §4.4 step 2 | terminal response signed, verified, stored as the answer | Yes | act 2 |
| §4.4 | forged *response* refused and inert | Yes | act 3 |
| §4.4 | forged *request* refused — the wake itself | Yes | act 6 |
| §4.4 | completion bound to the wake by causation | Yes | act 4 |
| §4.4 | audit trail distinguishes bound from orphaned | Yes | act 5 |
| §3 | submitter allowlist default-OFF on a cluster | Yes | banner, §10.1 |
| — | `/continue` after full scale-to-zero resumes sequence | Yes | stage 2c |
| — | replay guard drops stale requests | Yes | T5.6 |
| D3 | blocked egress on a wrong-scope call | **No** | cut — see §5 |
| R1 | no SVID, no consumption | **No** | no SPIRE — see §5 |

Two beats in that list were **not planned** and ran anyway, because the rig was up:

- **Stage 2c.** The deployment scaled fully to zero, a `/continue` woke a *new* pod, and it
  resumed the conversation's sequence numbering rather than restarting it. This is the
  sharpest available evidence that conversation state lives in the event log and not in the
  process.
- **T5.6.** Re-submitting captured request bytes dropped **64 stale requests** rather than
  re-running them.

Environment: Kind cluster `kev1`, namespace `kev1`, podman on arm64 (M-series), single-node
ephemeral Kafka via Strimzi, KEDA, ingress-nginx. Mock agent mode throughout. Overlay
`kind-signed`, so `ER_REQUIRE_SIGNATURE` and `EB_REQUIRE_RESPONSE_SIGNATURE` were both
`true`.

## 2. Measurements

The spec predicted none of these — §4.4 argues for the design without costing it, and
[eventing-lab#2](https://github.com/rossoctl/eventing-lab/issues/2) was explicit that "no
on-cluster cost figure" existed anywhere in the repository. **That absence is itself the
finding about the spec**: the two-flag rollout in §4.4 depends on an observable reject rate
and a tolerable signing cost, and neither had a number until this run.

| What was measured | Spec predicted | Measured | Method |
| --- | --- | --- | --- |
| KEDA wake, lag to pod scheduled | — | **2.1–4.2 s** | 1-replica Kind, includes the broker's 3000 ms `group.initial.rebalance.delay.ms` |
| Pod Ready after scale-up | — | **+0–3 s** | `kubectl get po -w` |
| Turn 1, submit to terminal | — | **2–6 s** | cold pod, mock mode |
| Turn 2, warm | — | **4–10 s** | same pod still up |
| Signed / total events | — | **2 / 6** | terminal-only by policy, §4.4 |
| Scale back to zero | ~31 s (phase 1) | consistent | `cooldownPeriod: 30` |
| `sign_into` per call | 190–230 ms (phase 1 §9) | **123 ms median**, n=9 | **laptop, not cluster** — M-series host |

Two notes on reading these:

**The wake number is dominated by a broker setting, not by signing.** 3000 ms of a 2.1–4.2 s
wake is `group.initial.rebalance.delay.ms`. Quoting the wake time without naming the rig is
therefore meaningless, and the demo doc now says so rather than picking a favourable figure.

**The 123 ms signing cost is a laptop measurement and does not belong on a cluster slide.**
It is faster than Phase 1's 190–230 ms band for the same operation, which is consistent with
a faster host rather than with any change to the code. No per-signature figure was isolated
in-cluster — see §5.

## 3. Deltas from the design

1. **Signing is terminal-only, and the demo now states it as a ratio.** 2 signed of 6 events
   is the policy in §4.4 working as designed, not a gap — but the bare phrase "signed events"
   in the talk invited the assumption that all six were. The ratio is the honest version.

2. **Act 8 (blocked egress) is cut rather than deferred.** kindnetd does not enforce
   `NetworkPolicy`, so the beat cannot be built on this rig at all — a policy applies cleanly
   and changes nothing, which is the single worst outcome for a talk about unverified control
   claims. Recorded as a cut in the demo doc §9, not as pending work.

3. **A local Kind run now requires two extra flags**, `--public-url` and `--tag`. Both are
   workarounds for defects filed in §6, not design changes, but the demo doc is the place
   that has to carry them and now does.

## 4. Findings

Seven bugs. The common shape is what makes them worth recording: **each verified an intent
rather than a state, and each reported success while the system did something else.**

| Reported | Actually true |
| --- | --- |
| `ALL 40 CHECKS PASSED` | the image contained no signing code |
| `applied with 2 kid(s)` | the overlay overwrote the keyset with `{}` |
| `deploy not needed — digest matches` | the keyset was never applied |
| `response verification ON (ENFORCING)` | zero approved keys, refusing everything |

### 4.1 The eventbridge image crash-looped on a missing module

- **Symptom:** `CrashLoopBackOff` with an `ImportError` for `eventrunner.agentspec`.
- **Root cause:** `handlers.py` imports it; `Dockerfile-eventbridge` never `COPY`ed
  `eventrunner/` into the image. Local runs pass because the source tree is on `sys.path`.
- **Fix:** `74f7657`, merged in [#8](https://github.com/rossoctl/eventing-lab/pull/8) as `0331e57`.

### 4.2 The overlay erased the keyset on every deploy

- **Symptom:** `k8s_deploy.py` reported `applied with 2 kid(s)`, and the ConfigMap in the
  cluster contained `{}`.
- **Root cause:** the `kind-signed` overlay carries a committed placeholder keyset of `{}`,
  which was applied *after* the real one and silently replaced it. The report was true of
  what the script sent and false of what the cluster held.
- **Fix:** `d5fd9f4`.

### 4.3 ENFORCING with zero approved kids starts silently

- **Symptom:** every response refused, including genuine traffic, with the banner reading
  `response verification ON (ENFORCING)`. The refusals were indistinguishable from the
  forgery beat succeeding.
- **Root cause:** `eventrunner/__main__.py:74-77` and `eventbridge/__main__.py:67-71` print
  the kid count and continue. Downstream of 4.2 this meant `0 kid(s)`, which can verify
  nothing. Both files argue in comments that a service which believes it is enforcing but is
  not should refuse to start, and `eventbridge` already does exactly that for a *missing kid*
  at `__main__.py:75` — the policy simply is not applied to the empty-set case.
- **Fix:** not fixed; filed as
  [eventing-lab#12](https://github.com/rossoctl/eventing-lab/issues/12).

### 4.4 Fixing the ConfigMap did not fix the running process

- **Symptom:** after 4.2's fix the keyset in the cluster was correct, the deploy reported
  `rollout_status` rolled out, and EventBridge still refused every response.
- **Root cause:** the keyset is a volume mount and `keyset.load_if_set` runs once at startup
  (`eventbridge/__main__.py:65`) — live reload is deliberately absent. A pod scheduled while
  the overlay's `{}` was still in place holds the empty set for its whole life, and an
  unchanged overlay triggers no rollout, so re-deploying fixes the ConfigMap and leaves the
  process enforcing against zero kids.
- **Fix:** `2b632af` — `rollout restart deploy/eventbridge` after the keyset is restored,
  plus `_verify_keyset_enforcing`, which reads EventBridge's **own startup line** back from
  the pod and fails when the kid count is 0 or the line is absent.
- **Why it belongs in this report rather than as a footnote to 4.2:** it is the same lesson
  one layer deeper, and the fix states it exactly — *the ConfigMap check proves what was
  written; this one proves what the process loaded.* Two checks can both be green about the
  same object and still not establish that any running code read it.

### 4.5 Act 3 asked for a state the API correctly refuses

- **Symptom:** act 3 failed against a working system.
- **Root cause:** the stage constructed its forgery target with `expected=2` *and* one prompt,
  which `handlers.py:325` rightly rejects as inconsistent. The assertion was wrong, not the
  code.
- **Fix:** `7ad11d4` — build the group through the API rather than around it.

### 4.6 The audit pod had no keyset, so it called valid signatures unsigned

- **Symptom:** the audit stage reported signed terminals as unsigned.
- **Root cause:** the audit pod mounted no keyset volume, so it had nothing to verify
  against. **Two of this session's own assertions were wrong in the same way.**
- **Fix:** `f988fd9`.

### 4.7 Act 6 matched a generic phrase across a 400-line window

- **Symptom:** intermittent pass/fail on a correctly-refusing runner.
- **Root cause:** the log assertion matched a phrase that appears in ordinary operation, over
  a wide `--tail` window, so it could match an unrelated line.
- **Fix:** `9d5f5fd` — match the forgery's own `corr=forged-<ts>` with `--tail=-1`. **This
  commit postdates the last flow run and has not itself been verified on a cluster**; see §5.

### 4.8 The lesson that outranks the individual bugs

Three of this run's own assertions accused a correctly-working signature check of being
bypassed (4.5, 4.6, and the misdiagnosis in
[eventing-lab#11](https://github.com/rossoctl/eventing-lab/issues/11)). Throughout, the
control held: the runner refused unsigned requests, including the system's own production
traffic. **It failed closed, silently, while every indicator was green.**

A false alarm about a control is its own category of failure. It consumes the attention a
real breach would need, and it is produced by the verification tooling — which is therefore
not exempt from needing verification. For a talk titled "The Seams Are the Trap" this is the
strongest material the week produced.

## 5. What is NOT verified

- **55/55 has never been observed.** The run that passed 54/55 predates the last four
  commits merged in [#8](https://github.com/rossoctl/eventing-lab/pull/8) — `9d5f5fd`
  (act 6's log assertion), `2b632af` (the keyset rollout restart and
  `_verify_keyset_enforcing`), `0065382` and `1a128bb`. None of the four has run against a
  cluster. Verifying them means one flow run on a rebuilt rig, and `2b632af` in particular
  changes deploy behaviour rather than an assertion, so it is the one to watch.
- **Per-signature cost in-cluster.** Only a laptop figure exists (123 ms median, n=9). The
  wake-latency number on the slide therefore does not have a signing component attributed
  inside it, which is what [#2](https://github.com/rossoctl/eventing-lab/issues/2) asked for.
- **Any real agent run.** Mock mode throughout; the LiteLLM team budget is exhausted. Nothing
  here exercises `claude` actually running, only the event path around it.
- **Act 8, blocked egress.** Untested and unbuildable on kindnetd, which does not enforce
  `NetworkPolicy`. Cut, not deferred.
- **The SPIRE beat (R1), "no SVID, no consumption".** No SPIRE anywhere in the rig. The beat
  does not exist in any form.
- **A demo stage for allowlist drift (R2).** The control is verified OFF-by-default on a
  cluster — the banner reads `submitter allowlist OFF` — and the enforcement path has nine
  unit tests, but **no stage shows the drift live**. The control exists; the beat does not.
- **Multi-node anything.** Single-node ephemeral Kafka, one broker, one node. Its
  `group.initial.rebalance.delay.ms` also differs from ykt1's, so no timing here transfers to
  that cluster.
- **The ingress path.** It drops ~40% of POSTs (§6, [#9](https://github.com/rossoctl/eventing-lab/issues/9)),
  so every number above was taken through a port-forward. The ingress path is not the one
  that was measured, and is known-bad rather than unverified.

## 6. Follow-ups

| What | Why it was deferred | Issue |
| --- | --- | --- |
| `kind-local` overlay; stop `imagePullPolicy: Always` defeating `kind load` | workaround existed (manual patches); the fix is a design choice about overlays | [#9](https://github.com/rossoctl/eventing-lab/issues/9) |
| preflight should assert the image *contains* the enforced code, not that it pulls | needs a marker or a build SHA on `/healthz` | [#10](https://github.com/rossoctl/eventing-lab/issues/10) |
| the in-pod selftest reports the host's ingress as `not-listening` | wrong network namespace; cost an hour of misdiagnosis | [#11](https://github.com/rossoctl/eventing-lab/issues/11) |
| refuse to start when ENFORCING with a zero-kid keyset | §4.3; the policy already exists for a missing kid | [#12](https://github.com/rossoctl/eventing-lab/issues/12) |
| ingress drops ~40% of POSTs through podman's `hostPort` | not worth fixing for the demo; port-forward is sufficient | noted in [#9](https://github.com/rossoctl/eventing-lab/issues/9) |
| a demo stage for allowlist drift (R2) | control verified; the beat is unbuilt | §5 |
