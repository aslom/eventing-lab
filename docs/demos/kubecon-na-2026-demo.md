# KubeCon NA 2026 — the demo script

The run-of-show for **"The Seams Are the Trap: Agent Fleets from CNCF Parts You Already
Have"** (30-minute session). Companion to [`../notes/kubecon-na-2026.md`](../notes/kubecon-na-2026.md),
which holds the abstract, the status assessment and the reasoning; this document is the
thing you hold while recording.

**Read this first:** every act below is marked **🟢 DOABLE TODAY**, **🟡 PARTIAL**, or
**🔴 NEEDS WORK**. The marks are the point of the document. Four of the seven acts are
recordable this afternoon against a Kind cluster; one needs a cluster run that is scripted
but has never been executed; two need code that does not exist.

Nothing here is narrated over a system that does not do it. Where a beat is cut, the script
says what to say instead — per the talk's own thesis, an unchecked control claim is the
failure mode with no recovery.

---

## 0. The honest summary

| Act | Beat | State | What is missing |
| --- | --- | --- | --- |
| **1** | Idle at zero | 🟢 **DOABLE TODAY** | — |
| **2** | Signed task event wakes the agent | 🟡 **PARTIAL** | scripted, never run on a cluster |
| **3** | Forged *response* refused | 🟡 **PARTIAL** | scripted, never run on a cluster |
| **4** | Completion bound to the wake | 🟢 **DOABLE TODAY** | — |
| **5** | Audit trail over the topic | 🟡 **PARTIAL** | built, never run on a cluster |
| **6** | Back to zero when idle | 🟢 **DOABLE TODAY** | — |
| **7** | Blocked egress on a wrong-scope call | 🔴 **NEEDS WORK** | **cut it** — see §7 |
| **—** | Forged *request* refused | 🔴 **NEEDS WORK** | control exists, no stage drives it |
| **—** | No SVID, no consumption | 🔴 **NEEDS WORK** | no SPIRE anywhere |
| **—** | Allowlist drift between submit and wake | 🔴 **NEEDS WORK** | runner never reads `ce_submitter` |

**The single blocking item is a cluster run, not a build.** Acts 2, 3 and 5 are implemented
and unit-tested; what does not exist is any record of them executing against a real broker,
and therefore any on-cluster signing figure. Tracked as
[eventing-lab#2](https://github.com/rossoctl/eventing-lab/issues/2).

---

## 1. Setup — before you record

🟢 **DOABLE TODAY.**

```sh
# 1. Signing key material. Never committed; the overlay mounts it as a Secret.
python3 scripts/gen_signing_keys.py --out ~/.eventing-keys

# 2. A Kind cluster with Strimzi, KEDA and ingress-nginx.
python3 scripts/kind_setup.py --check     # report the gaps, change nothing
python3 scripts/kind_setup.py             # install into the cluster

# 3. Deploy with signing ENFORCED in both directions.
python3 scripts/k8s_deploy.py --yes --overlay kind-signed --signing-keys ~/.eventing-keys
```

`--overlay kind-signed` flips `ER_REQUIRE_SIGNATURE` and `EB_REQUIRE_RESPONSE_SIGNATURE` to
`true` and points both services at one approved keyset. `k8s_deploy.py` refuses the overlay
without `--signing-keys`, so a half-configured enforcing deployment is not reachable by
accident — which is itself worth one sentence on stage, because
[examples#888](https://github.com/rossoctl/examples/issues/888) was exactly that state.

**Record in mock mode.** It is free, deterministic, and auto-selected when no credential is
present. Say so on the slide. The real-agent path has been blocked on an exhausted LiteLLM
team budget, so a live run risks a 429 you cannot recover from in front of a room.

**Rehearse and record on the same cluster.** Kind sets
`group.initial.rebalance.delay.ms: 0` where ykt1 leaves it at 3000 ms, so the timings are
not comparable. Pick one and say which on the slide.

### The one command

```sh
python3 scripts/k8s_demo_flow.py --overlay kind-signed
```

That drives acts 1–6 in order with assertions and timings. The acts below break out what is
on screen and what to say over it.

---

## 2. Act 1 — idle at zero  (D1)

🟢 **DOABLE TODAY.** Measured on two clusters.

**On screen:** `kubectl get deploy eventrunner` showing `0/0`, beside `watch_topics.py` with
zero consumer lag.

```sh
kubectl -n kev1 get deploy eventrunner
python3 scripts/watch_topics.py --once
```

**Say:** no agent is running. Not idling — *absent*. KEDA holds the deployment at
`minReplicaCount: 0` and the only thing that will create a replica is depth on a Kafka topic.

**The detail that makes it land:** `watch_topics.py` shows the claimed/unclaimed split, not
just a total. Point at it — it is what makes the KEDA story concrete rather than a slogan.

---

## 3. Act 2 — a signed task event wakes the agent  (D2 / R-signed)

🟡 **PARTIAL — this is the one that must be run before the recording.**

The stage is implemented (`assert_signed_wake`, `k8s_demo_flow.py:568`) and unit-tested
(`tests/test_demo_flow_signed.py`). **It has never run against a cluster.**

**On screen:** the POST, then replicas going 0 → 1, then the transcript streaming, then the
terminal response with a `kid` and a verified signature.

**Say:** the wake is a CloudEvent carrying a detached JWS over the envelope. EventRunner
will not act on it unless the signature verifies against an approved key.

**Two things to say that are true and unusual:**

1. **Lag means "work not finished," not "not read."** The offset commit is deferred to the
   terminal event, which makes consumer lag a *correct* queue gauge and makes it
   structurally impossible for KEDA to scale away a pod mid-stream. Proven by accident: a
   run sat in 429 retry backoff for 300 s and replicas stayed ≥ 1 for all 300 s.
2. **Only terminal events are signed, deliberately.** Ed25519 here is pure Python at
   190–230 ms per operation, and `emit()` runs per `stdout` frame — signing every frame
   would add minutes to a chatty run. So the signature proves **who finished a run, not
   what it said along the way.** Say that out loud; it is the honest limit and someone will
   ask.

**⚠️ Before recording:** the wake-latency number on the slide must *include* the signing
cost, and there is currently no on-cluster figure for it. The 190–230 ms band is laptop-only.
Capture it during the run and put the cluster number on the slide.

---

## 4. Act 3 — a forged response is refused *and inert*  (the money shot)

🟡 **PARTIAL — scripted, never run on a cluster.**

Implemented as `assert_forged_event_refused` (`k8s_demo_flow.py:655`). A throwaway pod
built from the eventrunner image publishes a **correctly encoded but unsigned** terminal
event directly to the responses topic — a correct forgery, not a malformed one.

**On screen:** the forged event published, then the batch *not* completing, then the genuine
answer still present in the transcript, then `rejected=2`.

**Say:** this is the talk's thesis with a measurement attached. Before the fix, the verifier
computed the **right verdict and did not act on it**:

```text
before: completed_utc set with 1 of 2 agents terminal
        counts {finished: 0, failed: 1}        <- the agent had SUCCEEDED
        [groups] duplicate terminal event ... ignored   <- the real answer, dropped
after:  batch open, transcript holds the genuine answer, rejected=2
```

**The sentence to get right:** the verdict was never wrong. The path to the outcome was
never followed. A forged event that the verifier *correctly rejected* still ended the batch
and fired a "finished" notification for work that never ran — and the genuine answer
arriving afterwards was then discarded as a duplicate. So the forgery did not merely add a
lie, **it destroyed the truth.**

That is [examples#885](https://github.com/rossoctl/examples/issues/885), fixed by
examples#955, with the group-mirror replay half fixed by examples#956. Two rules came out of
it, and they generalize past this repo:

- **Call the real function.** A reimplementation of the guard proves nothing about the guard.
- **Assert the outcome, not the verdict.** The test that was supposed to catch this asserted
  the verdict on a callback dict, so it could not have.

Use `--forge-collide` for the sequence-collision variant if you want the audit evidence to
be the surviving genuine row rather than a high-sequence orphan.

---

## 5. Act 4 — completion bound to the wake  (D4)

🟢 **DOABLE TODAY.**

**On screen:** the terminal response's `ce_causationid` matching the wake event's id.

**Say:** the abstract lists "unbound audit" as one of the four seams — as a *challenge*. This
one is already closed: every response carries `ce_causationid` binding it to the request that
triggered it. Keep it short; the interesting ones are the open seams.

---

## 6. Act 5 — the audit trail  (R4)

🟡 **PARTIAL — built, never run on a cluster.**

`scripts/audit_topic.py` + `shared/audit.py`, driven as `assert_audit_trail`
(`k8s_demo_flow.py:612`). **Run it after the forgery on purpose** — an audit that showed a
clean topic would prove nothing.

```sh
python3 scripts/audit_topic.py            # both topics, in-cluster, the deployment's keyset
python3 scripts/audit_topic.py --chains   # the binding, not the list
```

**On screen:** the event list with which are signed, and which result answers which signed
wake.

**Say:** five verdicts, so a frame that is unsigned *by policy* is not reported as a finding.
Seven chain states, so `bound=False` cannot quietly mean four different things. It runs
in-cluster against the deployment's own keyset.

**And then the limit, which matters more than the feature:** a flat keyset is
**attribution, not restriction.** It tells you which approved key signed — not that this
particular agent was permitted to answer this particular request. EventBridge's own key is
in the verify keyset by requirement, so its signature validates on any run's answer, as does
any approved runner's. Group events are pinned to `EB_SIGNING_KID`; member answers are not.
If anyone in the room works on authorization, this is the question they will ask.

From `keyset.py`'s own docstring, which is better than a paraphrase:

> *"What it does not do is attest anything. The set records which keys an operator approved,
> not which workloads a platform vouched for."*

---

## 7. Act 6 — back to zero  (D5)

🟢 **DOABLE TODAY.** Measured: 30–31 s with `cooldownPeriod: 30`.

**On screen:** replicas 1 → 0, lag at zero.

**Say:** the work finished, the offsets committed, the lag went to zero, KEDA removed the
replica. No agent is running again.

**Then the turn into the security story** — this is the best transition in the deck:
scale-to-zero is usually a cost argument. It is also a **trust** argument, because the
runner loads its configuration at *startup*. Change something while replicas are zero and
the next wake enforces the new value. There is no long-lived process holding a stale
decision.

That is the setup for the seam you are *not* demoing — see §8.

---

## 8. Act 7 — blocked egress  (D3): **cut it**

🔴 **NEEDS WORK — and the recommendation is to cut the beat, not build it.**

The abstract promises "blocked egress on a wrong-scope call." There is **no implementation
at all**: no NetworkPolicy, no Istio, no AuthorizationPolicy, no sidecar.

**The obvious MVP does not work, and the way it fails is on-theme.** The plan was to add a
NetworkPolicy with a DNS + broker + model-endpoint allowlist. But `k8s/kind/kind-cluster.yaml`
creates a default Kind cluster, and Kind's default CNI is **kindnetd, which does not
implement NetworkPolicy**: the API server accepts the object, `kubectl get networkpolicy`
shows it, and **nothing is enforced.** The demo would have shown a policy that appeared to
work and blocked nothing — precisely the class of failure this talk is about, reached by
skipping "which code path, by name?" on infrastructure rather than on code.

| Option | Cost | Verdict |
| --- | --- | --- |
| Swap Kind's CNI for Calico/Cilium | re-verify Strimzi, KEDA, ingress-nginx and every scale-to-zero timing on the new CNI | ❌ too much risk for one beat |
| Record on OpenShift (ykt1), which enforces natively | a second cluster, on VPN, with non-comparable timings | 🟡 viable as a separately-labelled clip |
| **Cut D3 and name the seam honestly** | a few hours of slide work | ✅ **recommended** |

**What to say instead.** Do not narrate a blocked call. Name the seam and state the limit:
a deploy-time egress allowlist *is* the "trust-scope drift" seam the abstract calls a
**problem**. Demoing the static version straight would always have required an apology.
Say that the composition's answer today is a deploy-time allowlist, that it drifts by
construction, and that the honest fix is scope read at wake time — which needs the SPIRE
work in §9.

**If you do build it**, two things apply: make the failure legible (a NetworkPolicy denial is
a connection timeout, indistinguishable on stage from slow wifi — surface the agent's own
`phase=error` frame or set a ~2 s client timeout), and verify a deliberately-denied call
actually fails before relying on it.

---

## 9. The three beats that need code

🔴 **NEEDS WORK.** None of these can be narrated over the system as it stands today. They
are listed in the order I would build them.

### 9.1 Forged *request* refused — cheapest, highest ratio

The control **already exists and is real**: with `ER_REQUIRE_SIGNATURE=true`, an unsigned
request hits `consume.py` → `verify_request` → `rejected_unsigned`, the offset is committed,
and the agent is never run. **Nothing drives it.**

This is the beat the abstract's "the event broker hands off events before signature check"
actually names, and it is a different topic *and* direction from Act 3, which forges a
*response*. Both are worth showing; neither substitutes for the other.

**Work:** one demo stage that publishes an unsigned frame to the **requests** topic and
asserts the agent never ran. Hours, not days — the control and the metric are both already
there. Scope item 6 on [eventing-lab#2](https://github.com/rossoctl/eventing-lab/issues/2).

### 9.2 Allowlist drift between submit and wake  (R2)

**EventRunner never reads `ce_submitter`** — zero matches in `eventrunner/`. It verifies that
EventBridge *signed*, not *who asked*. So "the same event is accepted, then refused after the
allowlist changes" cannot be shown.

**Work:** a runner-side submitter allowlist, loaded at startup. New code, but small. This is
the sharper of the two remaining stories, because the drift window is real rather than
contrived: edit the list while replicas are 0 and the next wake enforces the new one. It is
what turns §7's transition into a demonstrated claim.

### 9.3 No SVID, no consumption  (R1) — the SPIRE decision

Two of the four seams name SPIRE. **There is no SPIRE** — the only `spiffe` mention in the
entire non-markdown tree is a comment in `shared/keyset.py`.

The note's §3 recommends **Option A′: real SPIRE, identity gate only.** Deploy SPIRE server
and agent on Kind; an init container blocks on the Workload API socket until an SVID is
issued; EventRunner refuses to start its consumer without one. **The SVID is not the signing
key** — Ed25519 and the approved keyset keep doing signing exactly as they do now.

That split is the whole point. It buys R1's visible claim — no SVID, no consumption, lag
stays put; SVID issued, the agent wakes — while leaving the expensive half alone. What
§8.3 costs is making the SVID the *verification* key: the verifier accepts **EdDSA only**
and SPIRE issues EC or RSA, and `keyset.load` **rejects a JWKS document outright**. Adding
EC/RSA to hand-rolled pure-Python crypto (whose `_scalar_mult` already carries a documented
"not side-channel resistant" warning) or relaxing the no-C-extensions rule for `cryptography`
is the dominant engineering cost of the entire remaining plan. **The gate needs none of it.**

**Say the boundary out loud on the slide:** *SPIRE gates whether the agent runs; it does not
yet sign what the agent says.*

**Work:** multi-day, and the honest note is that the "~2 days" MVP estimate in the note's §5
never included it. If R1 is cut instead, the SPIRE question dissolves into "named upgrade
path with its obstacles" — which is a defensible talk, and §3's original Option A.

---

## 10. The §8 section — 3–4 minutes, and the differentiator

🟢 **DOABLE TODAY. Needs no engineering at all.**

[`../specs/0003-phase2-identity-on-the-event-path.md`](../specs/0003-phase2-identity-on-the-event-path.md)
§8 — thirteen documented claims that did not hold in code, each with a filed issue, plus the
five-question checklist that came out of them.

Say **"thirteen found"**, not "thirteen outstanding". As the fixes land the count that did
not hold drops, but the count that *were found* stays thirteen — so the number never needs
re-deriving before the talk.

The abstract promises a named taxonomy of where compositions leak. §8.4 names *why*:

> **"each claim was written from the change that introduced it, and stayed true only for the
> configuration that change was exercised in."**

That is a seam taxonomy for security *documentation*, and nobody else at the conference will
be presenting one. The five questions compress to one slide:

1. Which code path, by name?
2. What is the default?
3. What happens when it is half-configured?
4. Can an attacker choose the input the check reads?
5. What does the claim become one release from now?

**Question 3 is the highest-yield.** A control with two variables has four states, and the
three that are not "both set" are the ones a reader hits. The worst finding
([examples#888](https://github.com/rossoctl/examples/issues/888)) was exactly that:
enforcement on, no keyset, refusing nothing while the banner printed `OFF` — against a
request side that refused *everything* in the same configuration. Both were documented as
"refusal is on." One was true.

**This section also inoculates you.** Saying the thirteen out loud turns the open bugs from
something a sharp attendee catches into evidence of rigor. **Go first.**

---

## 11. Running order and timing

30 minutes is ~22–24 min of content, 4 min of demo, 3–4 min of questions. **The demo is the
constraint** — D1–D5 cannot be driven live in four minutes: the wake alone is 1.0–5.0 s on
ykt1 and the cycle back to zero is 30–31 s, before the agent does any work.

| Min | Section |
| --- | --- |
| 0–2 | "Every vendor is shipping an agent platform. You probably don't need one." |
| 2–5 | The composition: KEDA + Strimzi/Kafka + CloudEvents + JWS. Say **Strimzi, not Knative** |
| 5–9 | The four seams, all named — the take-home artifact |
| 9–13 | Seams 1 + 4: the wake contract and bound audit. Lead with the lag insight |
| 13–17 | Seams 2 + 3: scope drift and delivery. Name what they do **not** prove |
| 17–21 | **Demo** (recorded, narrated) — acts 1–6. Hard stop at 4 min |
| 21–25 | **§8: thirteen claims that did not hold** — the differentiator |
| 25–27 | What's next: the SPIRE upgrade path with its obstacles; Phase 3 tenancy as design |
| 27–30 | Questions |

**Recording technique:** record at 1× with a visible clock, cut dead air with explicit
on-screen jumps ("+12 s"), narrate live over it. Keep `watch_topics.py` on screen for the lag
numbers. Have a terminal ready to run one *short* thing live if the room is engaged.

**If the talk runs long, the §8 section is the one thing not to cut.** Compress the
architecture slides instead.

---

## 12. Likely questions, with answers that exist

| Question | Where the answer is |
| --- | --- |
| Why not SPIRE already? | §9.3; §8.3's EdDSA/JWKS obstacles |
| Why not a JWT library / Keycloak? | Phase 2 §2.1, §4.3 — Keycloak proves holding a secret, which a compromised pod also holds |
| Why hand-rolled Ed25519? | Phase 1 §1.1; Phase 2 §4.3. Mention the `_scalar_mult` timing caveat before someone else does |
| Why not HMAC — it's 170,000× faster? | Phase 2 §4.3: symmetric, so EventBridge could forge any agent's response |
| Kafka ACLs? | Phase 2 §3.3: the authorizer is global, not per-listener, so the demo is either broken or vacuous |
| Multi-tenancy? | [`../specs/0004-phase3-tenancy-agents-triggers.md`](../specs/0004-phase3-tenancy-agents-triggers.md) §2–§3, **design only — do not promise it** |
| Signing overhead? | 190–230 ms sign/verify, pure Python, laptop-measured. `cryptography` does it in microseconds. **Get the cluster number during the eventing-lab#2 run** |
| Doesn't compromising EventBridge mean you can claim any user? | **Yes.** Standard PEP design, and the alternative is worse — spreading a live GitHub credential across every runner. Phase 2 §2.7 |

---

## 13. Things to say verbatim

Lifted from the code's own docstrings, which beat any paraphrase:

- *"What it does not do is attest anything. The set records which keys an operator approved,
  not which workloads a platform vouched for."* — `keyset.py`
- *"This proves who finished a run, not what it said along the way."* — Phase 2 §4.4 step 2
- *"A security demo that oversells its guarantee is worse than one that does not exist."* —
  Phase 2 §7

---

## 14. Pre-flight checklist

Before you consider the demo recordable:

- [ ] **Run `kind-signed` end to end against a cluster** ([eventing-lab#2](https://github.com/rossoctl/eventing-lab/issues/2)) — the one blocking item
- [ ] Capture the **on-cluster signing cost** and put it inside the wake-latency slide number
- [ ] Record the run in `docs/reports/` (append a new section — reports are never retro-edited)
- [ ] Confirm **which cluster** every number on every slide came from
- [ ] Pre-pull images on the node (a cold pull is 5.1 s of the wake at 245 MB)
- [ ] Decide **R1/SPIRE: build the gate, or cut the beat and name the upgrade path**
- [ ] Decide whether §9.1 (forged request) makes the cut — it is the cheapest remaining beat
- [ ] Re-run the suite and quote the **collected** count with its environment, not a bare pass figure
- [ ] Do not expose the public Route during the conference, or set `EB_AUTH_TOKENS` — [eventing-lab#1](https://github.com/rossoctl/eventing-lab/issues/1) is an unauthenticated `PUT /transcript` that rewrites what an agent resumes from
