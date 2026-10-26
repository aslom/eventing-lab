# KubeCon NA 2026 — the demo script

The run-of-show for **"The Seams Are the Trap: Agent Fleets from CNCF Parts You Already
Have"** (30-minute session). Companion to [`../notes/kubecon-na-2026.md`](../notes/kubecon-na-2026.md),
which holds the abstract, the status assessment and the reasoning.

**If the camera is rolling, you want
[`kubecon-na-2026-screenplay.md`](kubecon-na-2026-screenplay.md) instead** — six beats,
~4 minutes, commands only. This document is the reference behind it: every act, what each
one does *not* prove, and the limits to say out loud. Read it to decide what to record;
hold the screenplay while recording.

**Read this first:** every act below is marked **🟢 DOABLE TODAY**, **🟡 PARTIAL**, or
**🔴 NEEDS WORK**. The marks are the point of the document. **Seven of the eight acts are
recordable**, all seven now verified against a Kind cluster rather than scripted-and-hoped;
one needs code that does not exist and is cut. Five further beats sit below the acts: three
that ran on the cluster and are recordable (§8.1, §8.2, §8.3 — the identity gate, with a real
GitHub sign-in), one built but without a stage, one needing SPIRE.

Nothing here is narrated over a system that does not do it. Where a beat is cut, the script
says what to say instead — per the talk's own thesis, an unchecked control claim is the
failure mode with no recovery.

---

## 0. The honest summary

| Act | Beat | State | What is missing |
| --- | --- | --- | --- |
| **1** | Idle at zero | 🟢 **DOABLE TODAY** | — |
| **2** | Signed task event wakes the agent | 🟢 **DOABLE TODAY** | — |
| **3** | Forged *response* refused | 🟢 **DOABLE TODAY** | — |
| **4** | Completion bound to the wake | 🟢 **DOABLE TODAY** | — |
| **5** | Audit trail over the topic | 🟢 **DOABLE TODAY** | — |
| **6** | Forged *request* refused — the wake itself | 🟢 **DOABLE TODAY** | — |
| **7** | Back to zero when idle | 🟢 **DOABLE TODAY** | — |
| **8** | Blocked egress on a wrong-scope call | 🔴 **NEEDS WORK** | **cut it** — see §9 |
| **—** | Scale-to-zero then `/continue` (stage 2c) | 🟢 **DOABLE TODAY** | — |
| **—** | Replay guard drops stale requests (T5.6) | 🟢 **DOABLE TODAY** | — |
| **—** | Identity gate — unauthenticated refused, approved user accepted (§8.3) | 🟢 **DOABLE TODAY** | — |
| **—** | Allowlist drift between submit and wake | 🟡 **PARTIAL** | enforcement verified; needs a demo stage |
| **—** | No SVID, no consumption | 🔴 **NEEDS WORK** | no SPIRE anywhere |

**The blocking item is cleared.** Acts 1–7 ran against a Kind cluster on 2026-10-14 with
signing enforced in both directions: **54 of 55 assertions passed**, including every signed
beat. [eventing-lab#2](https://github.com/rossoctl/eventing-lab/issues/2) was "run the signed
demo against a cluster, which has never been done"; that is now done, and the on-cluster
numbers it was blocking are in §12. The full write-up, including seven bugs the run exposed,
is [`../reports/phase2-cluster-run-report.md`](../reports/phase2-cluster-run-report.md).

**Changed 2026-10-14.** The cluster run moved acts 2, 3, 5 and 6 to 🟢 and added two beats
that were never in this document:

- **Stage 2c — `/continue` after a full scale-to-zero.** The deployment went to zero, a
  `/continue` woke a *new* pod, and it resumed the conversation's sequence numbering rather
  than restarting it. This is the strongest single demonstration that the state lives in the
  event log and not in the process; it is now §8.1.
- **T5.6 — the replay guard.** Re-submitting captured request bytes dropped **64 stale
  requests** instead of re-running them. Now §8.2.

Seven bugs surfaced, every one of them reachable only on a cluster and every one sitting behind
a green local report. They are fixed in [#8](https://github.com/rossoctl/eventing-lab/pull/8), merged
and are the material for §11 — see the report's §4.

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

**Rehearse and record on the same cluster.** The `group.initial.rebalance.delay.ms` differs
between rigs, and it dominates the wake number — the 2026-10-14 run measured a 3000 ms delay
inside its 2.1–4.2 s wake. Pick one cluster and say which on the slide; do not quote a wake
time measured on the other.

### The one command

```sh
python3 scripts/k8s_demo_flow.py --overlay kind-signed \
  --public-url http://127.0.0.1:18080 --tag local1
```

That drives acts 1–7 in order with assertions and timings. The acts below break out what is
on screen and what to say over it.

**Both extra flags are required for a local Kind run**, and neither was needed before
2026-10-14:

- **`--public-url` with a port-forward**, because the ingress drops roughly 40% of POSTs.
  Measured: port-forward 10/10 at 202 in ~10 ms; ingress 5 of 9, with four 10-second
  timeouts. nginx logged **nothing** for the lost requests, which places the loss in podman's
  `hostPort` mapping rather than in Kubernetes. Not worth fixing for the demo — but do not
  discover it while recording. Start the forward first:

  ```sh
  kubectl -n kev1 port-forward deploy/eventbridge 18080:8080 &
  sleep 3 && curl -sS -m 5 -o /dev/null -w 'pf ready %{http_code}\n' http://127.0.0.1:18080/healthz
  ```

- **`--tag`**, because locally-built images are `kind load`ed under a unique tag and are in no
  registry. The base manifests set `imagePullPolicy: Always` and tag `:dev`, so a deploy
  without `--tag` returns to the registry and silently runs stale code — which is how the run
  spent an evening narrating a signing demo against an image with no signing code in it.

  **`--tag` now actually applies.** Until 2026-10-19 it was parsed and ignored — declared in
  `build_args` and referenced nowhere else — so passing `--tag local1` did *not* prevent the
  `:dev` reset, and what produced a working local-image deployment was the manual `kubectl
  patch` pair, not the flag. `k8s_deploy.py` now patches both Deployments to the tag and
  relaxes `imagePullPolicy` to `IfNotPresent` for any non-default tag, so the one command is
  enough:

  ```sh
  python3 scripts/build_images.py --local --tag local1   # --local: a podman host has no
                                                         # buildx --builder, and we want a
                                                         # single-arch image loaded locally
  kind load docker-image \
    quay.io/aslomnet/rossoctl-eventbridge:local1 \
    quay.io/aslomnet/rossoctl-eventrunner:local1 --name kev1
  python3 scripts/k8s_deploy.py --yes --overlay kind-signed --tag local1 \
    --signing-keys ~/.eventing-keys
  ```

  **A `--tag` deploy then diffs forever, and that is expected.** The overlay renders `:dev`,
  so once both Deployments are patched to `:local1` the live state disagrees with the render
  and §14.1's `kubectl diff` reports cluster-side drift on every later run — while the digest,
  stamped from the *unpatched* render, keeps matching. The cheap gate says "unchanged" and the
  authoritative one says "drifted". So on a second `--tag` deploy the `deploy not needed` fast
  path is unreachable: pass `--force-deploy`, or deploy without `--tag` to return to the
  rendered state. `k8s_deploy.py` prints this as a note rather than leaving it to be found as
  an unexplained "drift detected" on a cluster nobody touched.

  [eventing-lab#9](https://github.com/rossoctl/eventing-lab/issues/9) proposed a `kind-local`
  overlay and was closed without one being built. The `--tag` fix makes that unnecessary **for
  the stale-image bug, and not for change detection** — a two-line `images:` transformer would
  make the tag part of the render, so the digest and the diff both stay meaningful and no patch
  is needed. Worth reopening #9 with that narrower scope.

  **Restart the port-forward after any deploy that changes an image.** A forward binds to one
  pod and does not follow its replacement, so a rollout silently kills it — and the failure
  surfaces three stages later as `Remote end closed connection without response`, not as
  "your tunnel is gone".

  Then **verify the running images actually contain the signing code**. This is the step
  whose absence caused the evening above. **Preflight check 14 now does this automatically**
  for any overlay that enforces signatures
  ([eventing-lab#10](https://github.com/rossoctl/eventing-lab/issues/10), merged) — it probes
  each image and fails the deploy if `<pkg>.__main__` will not import. The commands below
  remain useful as a spot check against a cluster you did not just deploy to. The two
  services need different checks: the stale image that evening was EventBridge's — it is
  up throughout, so check it in place — while EventRunner sits at 0 replicas until there
  is lag, so before the flow there is no pod to exec into and its image has to be
  checked directly, under the same `--tag` the deploy used:

  ```sh
  # EventBridge is up throughout, and its image is the one that carries the signing banner.
  kubectl -n kev1 exec deploy/eventbridge -- grep -rl 'request signing' /app/eventbridge
  # EventRunner sits at 0 replicas until there is lag, so there is no pod to exec into:
  # check the image itself, under the same --tag the deploy used.
  kubectl -n kev1 run imgcheck --rm -i --restart=Never --quiet \
    --image quay.io/aslomnet/rossoctl-eventrunner:local1 \
    --command -- grep -rl 'request verification uses' /app/eventrunner
  ```

  Both must print a path. Once the runner has woken, its startup banner
  (`[eventrunner] request verification uses …`) is the stronger check anyway — it
  reports what the process *loaded* rather than what the image contains, the same
  written-vs-read distinction the report's §4.4 turns on.

**Grab the runner logs while the pod is alive.** It scales to zero about 30 s after the work
drains, and `kubectl logs -l` then returns `No resources found`. Run this in a second terminal
*before* the flow:

```sh
while true; do
  kubectl -n kev1 logs -l app.kubernetes.io/name=eventrunner --prefix --timestamps --tail=-1 2>/dev/null
  sleep 2
done | tee /tmp/runner.log
```

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

🟢 **DOABLE TODAY.** Verified on a cluster 2026-10-14.

`assert_signed_wake` (`k8s_demo_flow.py:568`), unit-tested in
`tests/test_demo_flow_signed.py` and now run under enforcement on Kind: the terminal
`ce_signature` verified and was stored as the `phase=result` row. **Wake measured 2.1–4.2 s**,
of which 3000 ms is the broker's `group.initial.rebalance.delay.ms` — say the delay out loud
if you quote the wake, or the number sounds like signing overhead.

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
   **123 ms median** per `sign_into` (n=9, M-series laptop; Phase 1 §9 measured 222/227 ms — the
   pure-Python band across rigs is ~190–230 ms), and `emit()` runs per `stdout` frame — signing every frame would add
   tens of seconds to a chatty run. The ratio in this demo is **2 signed of 6 events**. So
   the signature proves **who finished a run, not what it said along the way.** Say both the
   ratio and the limit out loud; someone will ask, and "signed events" without the ratio
   invites the assumption that all six were.

**⚠️ Before recording:** the signing figure above is a **laptop** measurement. No
per-signature cost was isolated in-cluster during the 2026-10-14 run, so do not present it
as a cluster number — either capture one during the recording run, or quote the wake latency
without breaking out a signing component. See the report's §5.

---

## 4. Act 3 — a forged response is refused *and inert*  (the money shot)

🟢 **DOABLE TODAY.** Verified on a cluster 2026-10-14.

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

🟢 **DOABLE TODAY.** Verified on a cluster 2026-10-14: **12 BOUND chains**, with the forgery
showing as ORPHANED/unsigned. The run's summary line was
`signed-ok 29, signed-BAD 2, unsigned 4, unsigned-ok 76, terminals 15 (13 verified)`.

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

## 7. Act 6 — a forged *request* is refused  (R3)

🟢 **DOABLE TODAY.** The refusal was verified on a cluster 2026-10-14; the stage's own log
assertion was then fixed in `9d5f5fd`, and **that fix has not been re-run** — if this act
fails, read the flow log for whether the refusal line is present at all before suspecting the
runner. The check now matches the forgery's own `corr=forged-<ts>` with `--tail=-1`, so an
absence means something real.

Implemented as `assert_forged_request_refused`, with the attacker's program in
`FORGE_REQUEST_SRC`. **This is the beat the abstract literally names** — "the event broker
hands off events before signature check" — and it is a different topic *and* direction from
Act 3. That one forges an **answer** to real work; this one forges the **work itself**.
Neither substitutes for the other.

**On screen:** a throwaway pod publishes one unsigned request onto the **requests** topic.
Then replicas go 0 → 1, the runner logs its refusal, and replicas go back to 0 — with no
transcript anywhere for that correlation id.

```sh
# driven automatically by --overlay kind-signed; the refusal looks like
# [consume] rejecting unsigned/badly-signed request corr=forged-...: no ce_signature
#           attribute (ER_REQUIRE_SIGNATURE=true) — offset committed, not retried
```

**Say:** the pod woke up, declined to work, and went away.

**Why that sentence and not "nothing happened".** KEDA scales on lag over the requests
topic with `lagThreshold: 1`, so the forged request is what *causes* the runner to exist.
Claiming replicas stay at zero would be wrong, and anyone watching `kubectl get deploy`
would catch it. The honest claim is stronger anyway: the system spent a pod to refuse,
committed the offset so the message is not retried forever, and ran no agent.

**The prompt in the forgery is deliberately ugly** — it asks the agent to exfiltrate
secrets. If this control fails, the demo should make the consequence obvious rather than
subtle.

**What the evidence is, and why.** `rejected_unsigned` is an in-process counter with no
HTTP endpoint, so the beat asserts the runner's own log line plus **the absence of a run** —
a `GET /v0/agents/<forged-corr>` that finds no transcript. The correlation id is one
EventBridge never minted, so a transcript existing for it could only mean the forged
request executed.

---

## 8. Act 7 — back to zero  (D5)

🟢 **DOABLE TODAY.** Measured: 30–31 s with `cooldownPeriod: 30`.

**On screen:** replicas 1 → 0, lag at zero.

**Say:** the work finished, the offsets committed, the lag went to zero, KEDA removed the
replica. No agent is running again.

**Then the turn into the security story** — this is the best transition in the deck:
scale-to-zero is usually a cost argument. It is also a **trust** argument, because the
runner loads its configuration at *startup*. Change something while replicas are zero and
the next wake enforces the new value. There is no long-lived process holding a stale
decision.

**This is now a demonstrable claim, not just a nice line.** `ER_ALLOWED_SUBMITTERS` is read
in `config.load` at startup and never per record, so editing it while replicas are 0
means the next wake enforces the new list. What is missing is a *stage* that performs the
edit on camera — see §10.1.

That is also the setup for the seam you are *not* demoing — see §9.

### 8.1 Stage 2c — `/continue` after a full scale-to-zero

🟢 **DOABLE TODAY.** Ran on a cluster 2026-10-14; not previously in this document.

**On screen:** replicas at 0, then a `/continue` on an existing correlation id, then a **new**
pod coming up and the transcript resuming — with sequence numbers continuing the old
conversation rather than restarting at 1.

**Say:** the pod that started this conversation no longer exists. The one answering now has
never seen it. It picks up at the right sequence number because the conversation's state is
in the event log, not in the process — the agent is a **consumer of the log**, not the owner
of the session.

**Why this earns its 90 seconds:** it is the claim the whole architecture rests on, and it is
cheap to show. Put it immediately after act 7, while the deployment is already at zero.

### 8.2 T5.6 — the replay guard

🟢 **DOABLE TODAY.** Ran on a cluster 2026-10-14: **64 stale requests dropped**, none re-run.

**On screen:** captured request bytes re-submitted to the requests topic, and the runner
dropping them as stale rather than executing them.

**Say:** these are *validly signed* requests — the signature is genuine, because they are the
real bytes from earlier. A signature proves origin, not freshness. The age guard is what
stops a replay, and it is a separate control from the signature check.

**The reason to include it:** it pre-empts the sharpest question act 6 invites — "what stops
me from recording a valid signed request and sending it again?" Answering that before it is
asked is worth more than the 30 seconds it costs.

---

### 8.3 The identity gate — who *asked*  (R-identity)

🟢 **DOABLE TODAY.** Verified on a cluster 2026-10-19 with a real GitHub sign-in:
`identity: mrsabath via github; submitter covered by the signature`, inside a run that
reported **ALL 64 CHECKS PASSED**. The stage is `assert_identity_gate`
(`k8s_demo_flow.py`), with tests including three that drive it against a
deliberately-broken bridge and require it to fail.

Needs its own overlay and a credential. **Two ways to get the credential, and the
difference is worth one sentence on the slide.**

**The real one — GitHub sign-in in a browser** (`kind-signed-device`). Use this for the
recording: it is what a user would actually do, and it shows a human signing in rather
than pasting a token from a settings page.

```sh
# Prints a short code, opens github.com/login/device, polls until you authorise.
eval "$(python3 scripts/login.py --client-id Ov23liA2Z4jfbFRoJKZn --open --export)"
python3 scripts/k8s_deploy.py --yes --overlay kind-signed-device \
  --signing-keys ~/.eventing-keys
python3 scripts/k8s_demo_flow.py --overlay kind-signed-device \
  --gh-login "$DEMO_GH_LOGIN" --public-url http://127.0.0.1:18080 --tag local1
```

`--open` is opt-in: the default prints the URL instead, because auto-opening fails
silently over SSH and inside a container, which is where this is most often run (§2.2).
The code appears **before** any browser launch, so a failed launch is not a dead end.

**Check `DEMO_GH_LOGIN` is actually set.** `login.py` reads the login back from
`GET /user` after the token arrives, and that call can fail on its own (rate limit, a
network blip) while the token is perfectly valid — so the command exits 0, `--export`
emits `DEMO_GH_TOKEN` with no `DEMO_GH_LOGIN`, and the invocation above then passes
`--gh-login ""`. Every ordinary stage still authenticates; only `assert_identity_gate`
skips, and the run reports success having never exercised the beat this overlay exists
for. Both scripts now warn loudly on stderr when this happens — if you see it, pass
`--gh-login <your-login>` by hand.

**The deterministic one — a Personal Access Token** (`kind-signed-auth`). Use this in CI
or when you do not want a browser in the loop:

```sh
export DEMO_GH_TOKEN=ghp_...        # env only — a flag would reach `ps`
python3 scripts/k8s_deploy.py --yes --overlay kind-signed-auth \
  --signing-keys ~/.eventing-keys
python3 scripts/k8s_demo_flow.py --overlay kind-signed-auth \
  --gh-login <your-login> --public-url http://127.0.0.1:18080 --tag local1
```

Edit `EB_ALLOWED_USERS` and `ER_ALLOWED_SUBMITTERS` in whichever overlay you use first —
both ship as `CHANGEME` placeholders on purpose, so a committed overlay never approves a
specific person on every cluster it reaches.

**Two things about the device flow that are worth saying out loud:**

- **It needs no client secret**, which is why the client id is committed in the overlay
  rather than mounted as a Secret. A redirect-based flow would need EventBridge to host
  a callback at a URL GitHub can reach — and the demo runs behind a VPN, so that is
  exactly what it cannot do. The device flow inverts it: nothing has to reach us.
- **It requests no scopes.** `GET /user` returns the login for an unscoped token, so the
  credential cannot read a repository even if it leaks.

**On screen:** three `POST /v0/agents` calls in a row. No credential → **401** with
`WWW-Authenticate: Bearer realm="eventbridge"`. A junk token → **401 again**. The approved
user's token → **202**, and then `GET …/turns` showing `submitter=<login>` — the login
GitHub vouched for, read back from the turn tape rather than typed by the caller.

**Say:** the first request was not rejected by a signature check — it never became an event
at all. EventBridge refuses before the first write, so there is no transcript, no correlation
id, and nothing on the topic. Then the same request, signed in as an approved user, works —
and the event now records *who asked*, verified by GitHub rather than typed by the caller.

**The line that makes this more than HTTP status codes:** `submitter` and `submitteriss` are
in `signing.SIGNED_ATTRS`. So the identity is covered by the signature and **cannot be
relabelled in flight** — and that is precisely why EventRunner checks
`ER_ALLOWED_SUBMITTERS` *after* the signature check rather than before. Acts 3 and 6 prove
which approved *key* signed; this proves which *person* asked, and that the answer is
tamper-evident. Together they close the loop.

**Two limits to state out loud, because both are real:**

- **Approved-list mode accepts any GitHub token from an approved login.** There is no OAuth
  App scoping it to this application. That is right for a demo driven by a PAT and wrong for
  a deployment — `eventbridge/auth.py` says so in its own docstring, and a talk about
  unchecked control claims does not get to skip the sentence.
- **Only four routes are guarded.** `start_agent`, `create_group`, `list_groups` and group
  mutations. **`PUT /transcript` is not** — and it is the checkpoint EventRunner resumes from
  on a cold pod, so an unauthenticated write changes what the agent continues with. Request
  signing does not cover it either. That is
  [eventing-lab#1](https://github.com/rossoctl/eventing-lab/issues/1), and it is the reason
  §15 says not to expose the public Route during the conference.

**Why it is worth the 90 seconds:** it is the only beat that answers "who" rather than
"what", and it sets up §10.1's drift beat directly — the allowlist it configures is read at
*startup*, so editing it while replicas are zero is the whole trust-scope-drift story.

---

## 9. Act 8 — blocked egress  (D3): **cut it**

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
work in §10.2.

**If you do build it**, two things apply: make the failure legible (a NetworkPolicy denial is
a connection timeout, indistinguishable on stage from slow wifi — surface the agent's own
`phase=error` frame or set a ~2 s client timeout), and verify a deliberately-denied call
actually fails before relying on it.

---

## 10. What still needs work

Listed in the order I would do them. The first is half-built and cheap; the second is the
one real decision left.

### 10.1 Allowlist drift between submit and wake  (R2) — half done

**The enforcement now exists.** `ER_ALLOWED_SUBMITTERS` on EventRunner refuses a request
whose `ce_submitter` is absent or unlisted, checked **after** the signature so the attribute
is one a signature covers (`submitter` is in `signing.SIGNED_ATTRS`, so it cannot be
rewritten in flight). Nine tests pin the decisions, including that the signature check fires
first and that a stale event is still dropped by the age guard rather than reported as an
allowlist miss.

**The control is now verified on a cluster** as well as unit-tested — the 2026-10-14 run
confirmed it is **off by default**, with the startup banner reading `submitter allowlist OFF`.
That is the correct default and worth one sentence on the slide, because an allowlist that
defaults to on would refuse the demo's own traffic.

**What is still missing is the stage**, not the control: something that submits as an approved
user, scales to zero, edits the ConfigMap, and shows the *same* request refused on the next
wake. Perhaps an hour's work now the enforcement is in. **Do not narrate the drift beat
without building it** — this is the one remaining place in the deck where a control claim
would outrun what has been run.

**One caveat to state on the slide.** With `ER_REQUIRE_SIGNATURE=false` the list is still
enforced but means much less — `ce_submitter` is then whatever the publisher typed, so it
filters honest clients and restricts nobody. The runner prints a `WARNING` saying exactly
that at startup, and `test_the_allowlist_is_enforced_even_without_signature_enforcement`
pins the behaviour so the banner cannot quietly become a lie.

### 10.2 No SVID, no consumption  (R1) — the SPIRE decision

Two of the four seams name SPIRE. **There is no SPIRE** — and no `spiffe` mention anywhere in
the non-markdown tree. The closest the code comes is `shared/keyset.py`'s docstring, which
names workload attestation only to disclaim it: *"What it does not do is attest anything."*

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

## 11. The §8 section — 3–4 minutes, and the differentiator

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

### 11.1 The seven the cluster run added — "every check verified an intent, not a state"

The thirteen above were found by **reading** the code against its documentation. The
2026-10-14 cluster run found seven more by **running** it, and they are a different and better
category of evidence: all seven were unreachable locally, and **every one of them sat behind a
green report.** One slide, four rows, and it lands without explanation:

| Reported | Actually true |
| --- | --- |
| `ALL 40 CHECKS PASSED` | the image contained no signing code |
| `applied with 2 kid(s)` | the overlay overwrote the keyset with `{}` |
| `deploy not needed — digest matches` | the keyset was never applied |
| `response verification ON (ENFORCING)` | zero approved keys, refusing everything |

**The line to say over it:** each of those checks verified an **intent** — what the tooling
*sent* — rather than a **state**: what the cluster actually *held*. That is the same seam as
the thirteen, one layer out. The documentation claimed controls the code did not implement;
the tooling claimed states the cluster was not in.

**Then the part that is harder to say and worth more.** Throughout all of it the control
held: EventRunner refused unsigned requests, correctly, including the system's own genuine
traffic. **It failed closed, silently, while every indicator was green.** And three of my own
assertions then accused that correctly-working control of being bypassed.

So the taxonomy gets a fifth entry the abstract does not promise: **a false alarm about a
control is its own category of failure.** It consumes exactly the attention a real breach
would need, and it is produced by the verification tooling — which is therefore not exempt
from needing verification. For a talk called "The Seams Are the Trap", admitting that the
trap caught the person who named it is the strongest ending available.

Details and root causes: [`../reports/phase2-cluster-run-report.md`](../reports/phase2-cluster-run-report.md)
§4. Fixes in [#8](https://github.com/rossoctl/eventing-lab/pull/8); the two not fixed are
[#10](https://github.com/rossoctl/eventing-lab/issues/10) and
[#12](https://github.com/rossoctl/eventing-lab/issues/12).

---

## 12. Running order and timing

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
| 17–21 | **Demo** (recorded, narrated) — acts 1–7, plus §8.1 if time. Hard stop at 4 min |
| 21–25 | **The claims that did not hold** (spec §8's thirteen, plus this run's seven) — the differentiator |
| 25–27 | What's next: the SPIRE upgrade path with its obstacles; Phase 3 tenancy as design |
| 27–30 | Questions |

**What to cut from the demo reel, in order.** All seven acts now run, so the four-minute
budget is the binding constraint rather than what works: drop §8.2 (replay guard) first,
then §8.1 (`/continue`), then act 4 (causation — it is one screenshot and can be a slide),
then compress act 5's audit output to the summary line. Acts 2, 3 and 6 are the talk; keep
all three, since act 3 forges an **answer** and act 6 forges the **work**, and neither
substitutes for the other.

**Recording technique:** record at 1× with a visible clock, cut dead air with explicit
on-screen jumps ("+12 s"), narrate live over it. Keep `watch_topics.py` on screen for the lag
numbers. Have a terminal ready to run one *short* thing live if the room is engaged.

**If the talk runs long, the §8 section is the one thing not to cut.** Compress the
architecture slides instead.

---

## 13. Likely questions, with answers that exist

| Question | Where the answer is |
| --- | --- |
| Why not SPIRE already? | §10.2; §8.3's EdDSA/JWKS obstacles |
| Why not a JWT library / Keycloak? | Phase 2 §2.1, §4.3 — Keycloak proves holding a secret, which a compromised pod also holds |
| Why hand-rolled Ed25519? | Phase 1 §1.1; Phase 2 §4.3. Mention the `_scalar_mult` timing caveat before someone else does |
| Why not HMAC — it's 170,000× faster? | Phase 2 §4.3: symmetric, so EventBridge could forge any agent's response |
| Kafka ACLs? | Phase 2 §3.3: the authorizer is global, not per-listener, so the demo is either broken or vacuous |
| Multi-tenancy? | [`../specs/0004-phase3-tenancy-agents-triggers.md`](../specs/0004-phase3-tenancy-agents-triggers.md) §2–§3, **design only — do not promise it** |
| Signing overhead? | 190–230 ms sign/verify, pure Python, laptop-measured. `cryptography` does it in microseconds. **Get the cluster number during the eventing-lab#2 run** |
| Doesn't compromising EventBridge mean you can claim any user? | **Yes.** Standard PEP design, and the alternative is worse — spreading a live GitHub credential across every runner. Phase 2 §2.7 |

---

## 14. Things to say verbatim

Lifted from the code's own docstrings, which beat any paraphrase:

- *"What it does not do is attest anything. The set records which keys an operator approved,
  not which workloads a platform vouched for."* — `keyset.py`
- *"This proves who finished a run, not what it said along the way."* — Phase 2 §4.4 step 2
- *"A security demo that oversells its guarantee is worse than one that does not exist."* —
  Phase 2 §7

---

## 15. Pre-flight checklist

Before you consider the demo recordable:

- [x] **Run `kind-signed` end to end against a cluster** ([eventing-lab#2](https://github.com/rossoctl/eventing-lab/issues/2)) — done 2026-10-14, 54/55
- [x] Record the run in `docs/reports/` — [`../reports/phase2-cluster-run-report.md`](../reports/phase2-cluster-run-report.md)
- [x] **Re-run the flow for a clean sweep** — done 2026-10-19: **ALL 64 CHECKS PASSED** on Kind, including the four commits from #8 that had never run, `2b632af`'s in-process keyset check, and the identity gate
- [ ] Capture the **on-cluster signing cost** — still laptop-only at 123 ms; either measure it or stop breaking it out of the wake number
- [ ] Confirm **which cluster** every number on every slide came from, and name the `group.initial.rebalance.delay.ms` wherever a wake time appears
- [ ] Start the **port-forward** before recording, and use `--public-url` — the ingress drops ~40% of POSTs ([#9](https://github.com/rossoctl/eventing-lab/issues/9))
- [x] Verify the running image **contains the signing code**, not just that it pulled — now **automated as preflight check 14** ([#10](https://github.com/rossoctl/eventing-lab/issues/10), merged): it probes each enforcing service's image and fails the deploy if `<pkg>.__main__` will not import. The manual §1 commands remain useful for a spot check
- [x] Check the keyset has **2 kids, not `{}`**, after the final deploy — now **self-announcing**: an enforcing service with a zero-kid keyset refuses to start ([#12](https://github.com/rossoctl/eventing-lab/issues/12), merged), and `k8s_deploy.py` reads the startup line back from the pod (`2b632af`)
- [ ] Start the runner **log capture loop** before the flow — the pod is gone ~30 s after the work drains
- [ ] Pre-pull images on the node (a cold pull is 5.1 s of the wake at 245 MB)
- [ ] Decide **R1/SPIRE: build the gate, or cut the beat and name the upgrade path**
- [x] **Run §8.3's identity gate on a cluster** — done 2026-10-19 via the device flow (`kind-signed-device`), signed in as a real GitHub user
- [ ] Decide whether the §10.1 allowlist-drift stage makes the cut — ~1 hour now that enforcement is in
- [ ] Re-run the suite and quote the **collected** count with its environment, not a bare pass figure
- [ ] Do not expose the public Route during the conference, or set `EB_AUTH_TOKENS` — [eventing-lab#1](https://github.com/rossoctl/eventing-lab/issues/1) is an unauthenticated `PUT /transcript` that rewrites what an agent resumes from
