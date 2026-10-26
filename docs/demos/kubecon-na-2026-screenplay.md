# KubeCon NA 2026 — the screenplay

**The commands, in order, nothing else.** This is the card you hold while recording.
[`kubecon-na-2026-demo.md`](kubecon-na-2026-demo.md) is the reference — why each beat
exists, what it does *not* prove, and the full assertion list. This file is the shot list.

**Six beats, ~4 minutes of screen time, five terminals.** The slides carry the explanation;
the demo only has to make the value *visible*. Every beat answers one question in one screen,
and the `Pane` column says which terminal it happens in — see "The pane layout" below.

**Every beat below has run on a cluster.** Dates in the State column are the runs that
proved them, not the dates they were written.

| # | Beat | Question it answers | Pane | Screen time | State |
| --- | --- | --- | --- | --- | --- |
| **0** | No pods at all | "What does idle cost?" | A·B·C | 15 s | ✅ verified |
| **1** | Approved user in, everyone else out | "Who may ask?" | A | 45 s | ✅ verified 2026-10-19 |
| **2** | 100 agents, one POST | "Does it scale?" | A·B·C·D | 90 s | ✅ verified 2026-10-26 |
| **3** | Back to zero | "What does it cost when nobody asks?" | B | 30 s | ✅ verified |
| **4** | A forged wake is refused | "Who may answer?" | A·E | 45 s | ✅ verified 2026-10-14 |
| **5** | The audit trail | "How would you *know*?" | A | 30 s | ✅ verified 2026-10-14 |

**Record all of it.** Beat 2 drains in about a minute, and beat 3's wait is short but real. And the
ingress drops ~40% of POSTs ([#9](https://github.com/rossoctl/eventing-lab/issues/9)), so
every verified number came through a **port-forward** — a live POST over the Route is a
coin flip in front of a room. Narrate over the recording; keep a terminal open for
questions.

> **Beat 2 ran on a cluster 2026-10-26**: 100 agents from one POST, **1m04s, 100 finished,
> 0 failed, peak 10 replicas**. It was the last beat never to have been executed. The figures
> in §"What it looked like when it ran" are from that run.

---

## Setup — before the camera rolls

Not on screen. Done and warm before the first take.

```sh
# 1. Key material. Never committed; the overlay mounts it as a Secret.
python3 scripts/gen_signing_keys.py --out ~/.eventing-keys

# 2. The cluster: Strimzi, KEDA, ingress-nginx.
python3 scripts/kind_setup.py --check     # report gaps, change nothing
python3 scripts/kind_setup.py

# 3. Sign in, so beat 1 has a real GitHub identity rather than a pasted token.
eval "$(python3 scripts/login.py --client-id Ov23liA2Z4jfbFRoJKZn --open --export)"
echo "signed in as: ${DEMO_GH_LOGIN:?login did not come back — pass --gh-login by hand}"

# 4. Put YOUR login in the overlay. Both ship as CHANGEME on purpose.
#    EB_ALLOWED_USERS      — who may ask (EventBridge, at the edge)
#    ER_ALLOWED_SUBMITTERS — who the runner will act for
sed -i '' "s/CHANGEME-your-github-login/$DEMO_GH_LOGIN/g" \
  k8s/overlays/kind-signed-device/kustomization.yaml
# (GNU sed: drop the '' argument)

# 5. Deploy with signing ENFORCED in both directions.
python3 scripts/k8s_deploy.py --yes --kubeconfig .kube/config-kind \
  --overlay kind-signed-device \
  --signing-keys ~/.eventing-keys

# 6. Port-forward, and keep it up for the whole recording. NOT the ingress (#9).
kubectl -n kev1 port-forward svc/eventbridge 18080:8080 &
export EB=http://127.0.0.1:18080

# 7. Prove the whole thing green once, before recording a single frame.
python3 scripts/k8s_demo_flow.py --kubeconfig .kube/config-kind \
  --overlay kind-signed-device \
  --gh-login "$DEMO_GH_LOGIN" --public-url "$EB" --tag local1
```

Step 7 is the rehearsal. **If it does not report all checks passed, do not record** — every
beat below is a slice of that run, and the flow is what proves the slices are honest. One
known exception: the forged-request check fails on a reporting bug while the control itself
holds ([eventing-lab#23](https://github.com/rossoctl/eventing-lab/issues/23)).

### Three things that will bite, all of them found by running this

- **Step 4 makes the working tree dirty, and the suite will go red.**
  `test_the_device_overlay_ships_a_placeholder_login_not_a_real_one` asserts the overlay
  still says `CHANGEME` — correctly, so a committed overlay never approves a specific
  person on every cluster it reaches. Expect one failing test while your edit is in place,
  and **do not commit the overlay**.

- **The registry's `:dev` eventbridge image has no signing code.** Preflight check 14
  refuses the deploy, which is the check working
  ([eventing-lab#24](https://github.com/rossoctl/eventing-lab/issues/24)). Build locally
  and load, then deploy at that tag — this is why every command here carries `--tag`:

  ```sh
  python3 scripts/build_images.py --local --tag local1
  kind load docker-image --name kev1 \
    quay.io/aslomnet/rossoctl-eventbridge:local1 \
    quay.io/aslomnet/rossoctl-eventrunner:local1
  ```

- **Every pane needs the credential.** `eval "$(python3 scripts/login.py …)"` sets
  `DEMO_GH_TOKEN` in *one* shell. In another pane the POST goes out with an empty bearer
  and you get a `KeyError` traceback instead of a readable refusal. Write the env once and
  source it everywhere:

  ```sh
  python3 scripts/login.py --client-id Ov23liA2Z4jfbFRoJKZn --open --export > .demo-env
  . ./.demo-env          # in every pane, including the ones that only watch
  ```

## The pane layout

Five terminals, set up **before** the camera rolls. Every one of them needs the environment,
including the ones that only watch — `kubectl` needs `--kubeconfig` and beat 1 needs the
token:

```sh
# in EVERY pane
cd <your eventing-lab clone>
export KC=.kube/config-kind
export EB=http://127.0.0.1:18080
. ./.demo-env                      # the token, written once in Setup step 3
```

| Pane | Size on screen | What it shows | Started |
|---|---|---|---|
| **A** commands | large, where the eye goes | what you type | per beat |
| **B** pods | medium | KEDA creating and destroying replicas | once, left running |
| **C** queue | medium | lag, split claimed vs unclaimed | once, left running |
| **D** browser | full width when used | the group page — progress in *agent* units | beat 2 only |
| **E** refusals | small, can be off-screen | the runner's refusal line | **before** beat 4 |

**Pane B — the fan-out.** This is the pane the audience watches during beat 2.

```sh
kubectl --kubeconfig "$KC" -n kev1 get pods \
  -l app.kubernetes.io/name=eventrunner -w
```

**Pane C — the queue.** `--group latest` re-resolves the most recent group every frame, so
**start it before launching the batch** and it picks it up with no id to copy mid-demo.

```sh
python3 scripts/watch_topics.py --kubeconfig "$KC" \
  --group latest --url "$EB" --interval 2
```

**Pane D — the group page.** Open `$EB/v0/groups/$GID` *after* the POST in beat 2. It
auto-refreshes, so open it once and leave it. Use the `127.0.0.1:18080` form, **not** the
`html_url` the API returns — that is the ingress path, which drops ~40% of POSTs
([#9](https://github.com/rossoctl/eventing-lab/issues/9)).

**Pane E — the refusals.** Beat 4's only evidence is a log line, and the pod is gone ~5 s
after the work drains. Start this first:

```sh
kubectl --kubeconfig "$KC" -n kev1 logs deploy/eventrunner --tail=-1 -f 2>/dev/null \
  | grep --line-buffered -iE 'reject|unsigned' | tee /tmp/refusals.log
```

**Pane A — the port-forward goes here**, and it is the first thing to check. It dies when
the bridge pod restarts, and a dead forward looks exactly like a broken demo:

```sh
pkill -f 'port-forward.*18080'; sleep 1
kubectl --kubeconfig "$KC" -n kev1 port-forward svc/eventbridge 18080:8080 \
  >/dev/null 2>&1 &
sleep 3; curl -sS -m 5 -o /dev/null -w 'bridge %{http_code}\n' "$EB/healthz"   # expect 200
```

**Why C and B are both up:** C is infrastructure units — partitions, lag, offsets. D is
human units — "73 of 100 done, 14 running, about a minute left". The same reality in two
vocabularies is the demo; neither alone lands it.

---

## Beat 0 — there are no pods  (15 s) · pane A, point at B and C

```sh
kubectl -n kev1 get deploy eventrunner
```

```text
NAME          READY   UP-TO-DATE   AVAILABLE   AGE
eventrunner   0/0     0            0           12m
```

**Say:** "No agent is running. Not idle — *absent*. Nothing is consuming, nothing is
costing anything. The only thing that will create a replica is depth on a Kafka topic."

**Why it opens the demo:** it is the baseline beats 2 and 3 are measured against. Skip it
and the fan-out has nothing to fan out *from*.

---

## Beat 1 — who may ask  (45 s) · pane A

Three POSTs. The first two fail, and they fail *differently*.

```sh
# (a) no credential at all
curl -i -sS -X POST "$EB/v0/agents" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Summarise the incident.","max_turns":1}' | head -6

# (b) a token that is not a GitHub token
curl -i -sS -X POST "$EB/v0/agents" \
  -H 'Authorization: Bearer ghp_definitely_not_a_real_token' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Summarise the incident.","max_turns":1}' | head -4

# (c) the approved user. The guard and the echoed body are deliberate: an unset token
# otherwise produces a Python KeyError traceback on stage instead of a readable refusal.
: "${DEMO_GH_TOKEN:?not signed in in THIS pane — source ./.demo-env}"
RESP=$(curl -sS -X POST "$EB/v0/agents" \
  -H "Authorization: Bearer $DEMO_GH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Summarise the incident.","max_turns":1}')
echo "$RESP" | python3 -m json.tool
CORR=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("correlationid",""))')
echo "accepted as ${CORR:?request was refused — see the body above}"

# and who the event says asked — read back off the turn tape, not typed by the caller.
# No credential here on purpose: `get_turns` is NOT a guarded route, and sending one in
# the beat about which requests need a credential teaches the wrong thing. Six routes are
# guarded — start_agent, create_group, list_groups and the group mutations — and reads
# are not among them (eventing-lab#1).
curl -sS "$EB/v0/agents/$CORR/turns" | python3 -m json.tool | grep -i submitter
```

```text
(a) HTTP/1.1 401 Unauthorized
    WWW-Authenticate: Bearer realm="eventbridge"
(b) HTTP/1.1 401 Unauthorized
(c) accepted as brave-otter-4718
    "submitter": "<your-login>",
    "submitteriss": "github"
```

**Say:** "The first request was not refused by a signature check — it never became an event
at all. EventBridge refuses before the first write: no transcript, no correlation id,
nothing on the topic. The third request is the same request, from a user on the approved
list — and the event now records *who asked*, verified by GitHub rather than typed by the
caller."

**The line that makes it more than HTTP status codes:** `submitter` is in
`signing.SIGNED_ATTRS`, so the identity is covered by the signature and **cannot be
relabelled in flight**. That is why the runner checks its allowlist *after* the signature
check, not before.

**If a 403 would land better than a 401:** add a login that is *not* on the approved list
and show it — `403`, with the body naming the refused login, and deliberately **no**
`WWW-Authenticate`, because retrying with another credential is not the remedy. Costs
another 15 s; the 401/403 distinction is a good slide either way.

---

## Beat 2 — 100 agents, one POST  (90 s) · pane A, then B + C + D

The centrepiece. One call submits a hundred agents; KEDA does the rest.

```sh
GID=$(curl -sS -X POST "$EB/v0/groups" \
  -H "Authorization: Bearer $DEMO_GH_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c '
import json
print(json.dumps({"label": "kubecon-100",
                  "prompts": [f"Triage finding {i+1}. Reply OK." for i in range(100)],
                  "max_turns": 1}))')" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["groupid"])')
echo "batch $GID"
```

Then let the two panes tell the story — do not type anything else for ~60 s.

```sh
# left pane: the batch, in user units
python3 scripts/watch_topics.py --group "$GID" --url "$EB" --interval 2

# a third pane, or cut to it: the pods appearing
kubectl -n kev1 get pods -l app.kubernetes.io/name=eventrunner -w
```

**On screen, in this order:** lag jumps to 100 → `0/0` becomes `1/1`, then more → pods
appear until **10** → the group page's percentage climbs → lag drains to 0.

**Say:** "One POST, a hundred agents. Nobody provisioned anything. KEDA is reading queue
depth and creating pods — it stops at ten because `maxReplicaCount` is ten, and because
the topic has twelve partitions, which is the real ceiling: a partition is the unit of
assignment, so a pod can hold several but a partition is never split — twelve is the point
past which an extra pod gets nothing at all."

**Get this right if you say it.** With 12 partitions, 11 pods means one pod holds **two**
partitions; nothing is idle. Idle pods begin at 13. The cap of 10 is a deliberate setting,
not a limit the system bumped into.

**The detail worth pointing at:** the watcher splits lag into *claimed* and *unclaimed*.
That split exists because this system defers the Kafka offset commit until the agent's
terminal event — so **lag means "work not finished", not "work not read"**. It is a correct
queue gauge, and it is also what makes it structurally impossible for KEDA to scale away a
pod mid-run.

**Limits to state, since the talk is about unchecked claims:** mock mode, so this measures
the event path and not a hundred real model calls; one Kind node; and the batch figure from
Phase 1 (100 agents in 1m02s) was a different repo, pre-signing — use the number from your
own rehearsal, not that one.

---

## Beat 3 — back to zero  (30 s) · pane B

```sh
kubectl -n kev1 get deploy eventrunner -w
```

```text
eventrunner   10/10
eventrunner   0/0        # ~30s after the last terminal event
```

**Say:** "The queue is empty, so the capacity goes away. Scale-to-zero is not a cost
optimisation here — it is the thing that makes the trust-scope question interesting, because
an allowlist read at pod start is re-read on the *next* wake. Change it while there are zero
pods and the next request is judged by the new rules."

**The cooldown on the recording rig is 5 s**, not 30. `k8s/overlays/kind/kustomization.yaml`
patches `/spec/cooldownPeriod` to `5`, and `kind-signed-device` inherits it through
`../kind-signed` → `../kind`; the `30` in the comment above that patch records an old ykt1
measurement, not this setting. Base is 300 s. So this beat is quick enough to leave uncut —
and if a number reaches a slide, name the rig, because the three values are 5 / 30 / 300.

---

## Beat 4 — who may answer  (45 s) · pane A, evidence in E

A forged **wake** — an event injected straight onto the requests topic, correctly encoded
and unsigned, exactly as an attacker with broker access would write it.

There is no flag that runs this stage alone — `--skip` is declared but unused, so passing
it does nothing and you get the whole flow. The shortest honest path is the fast flow, then
cut to the log in the edit:

```sh
# ~2 min: skips the two slow gates, keeps every signed beat
python3 scripts/k8s_demo_flow.py --kubeconfig .kube/config-kind \
  --overlay kind-signed-device \
  --gh-login "$DEMO_GH_LOGIN" --public-url "$EB" --tag local1 \
  --skip-cold-continue --skip-idle-replay
```

**Start the log capture BEFORE the flow.** This beat's only evidence is a log line, and the
flow ends on `stage3()` — scale-back-to-zero — with a 5 s cooldown. Run the grep afterwards
and `logs deploy/eventrunner` resolves through a Deployment with no pods and returns nothing
(or `error: timed out waiting for the condition`). The run-of-show's checklist says this; it
is easy to drop when transcribing commands.

```sh
# pane E, started FIRST and left running for the whole beat
kubectl -n kev1 logs deploy/eventrunner --tail=-1 -f 2>/dev/null \
  | grep --line-buffered -iE 'reject|unsigned' | tee /tmp/refusals.log
```

The frame that matters, captured while the pod is briefly up:

```text
[eventrunner] rejecting unsigned/badly-signed request corr=forged-1761…:
  no ce_signature attribute (ER_REQUIRE_SIGNATURE=true) — offset committed, not retried
```

**Say:** "That event reached the broker. Anything with network access to Kafka can write to
a topic — the broker is plaintext and this demo does not pretend otherwise. What it cannot
do is make an agent *act*. The runner verified the signature against an approved key set,
refused it, committed the offset so it is not retried, and **never started the agent**."

**The sentence that lands the seam:** "The broker handed off the event. The *runner* is what
refused it. That is the difference between a queue and a trust boundary — and the abstract
calls it unverified delivery."

**One honest limit:** signing covers terminal events only, because Ed25519 in pure Python
costs ~190–230 ms per operation and signing every streamed frame would add minutes to a
chatty run. So this proves **who finished a run, not what it said along the way**. Say it;
someone will ask.

---

## Beat 5 — how would you know?  (30 s) · pane A

Beat 4 showed the gate holding. This is the question auditors actually ask.

```sh
python3 scripts/audit_topic.py --kubeconfig .kube/config-kind --tag local1 \
  --chains --verify
```

`--verify` is what makes this a beat rather than a wall of counts: without it the tool
asserts nothing and prints no verdict, deliberately — `audit_locally` returns 0 early
because "a harness that asserted nothing has proven nothing". With it the beat ends on
`PASS`/`FAIL`.

**One caveat before you add `--verify` to a recording:** it asserts a *clean* topic, so it
fails if a forgery from beat 4 is still on it. Either run beat 5 on a purged topic, or keep
`--chains` alone and read the `ORPHANED` line aloud — which is the stronger demo anyway,
because the forgery is visible rather than merely counted.

```text
  wake 27504bea  brave-otter-4718   2 result(s)  BOUND
         wake and result both verified
  wake (none)    -                  1 result(s)  ORPHANED
         results naming a wake event that is not on this topic

summary
  signed-ok     29
  signed-BAD     2
  unsigned       4
  unsigned-ok   76
  terminals     15 (13 verified)
```

**Say:** "Every result is chained back to the signed wake event that caused it —
`causationid`, which is itself inside the signature, so the link cannot be rewritten. The
forgery shows up as `ORPHANED`: an answer to a question nobody asked. And `unsigned-ok` is
seventy-six streamed frames that are unsigned *by policy* — a tool that flagged those would
bury the two findings that matter in noise."

**Why it closes the demo:** beats 1 and 4 are controls — they say no. This says *what
happened*, after the fact, from the log alone. That is the fourth seam, and the one most
teams discover they cannot answer.

---

## What it looked like when it ran

Measured on a Kind rig 2026-10-26 — `kind-signed-device`, signing enforced both
directions, mock mode, images at `--tag local1`. **Your numbers will differ; these are
what "working" looks like, not targets.**

| | Measured | Note |
|---|---|---|
| Full flow | **70 of 71 checks** | the 1 failure is [#23](https://github.com/rossoctl/eventing-lab/issues/23), a reporting bug |
| Cold wake, lag → `Active=True` | **6.3 s** | ~3000 ms of it the broker's `group.initial.rebalance.delay.ms` |
| Pod Ready after POST | 6.9 s | |
| Turn 1, submit → terminal | 2–6 s | mock |
| **Beat 2 — group of 100** | **1m04s, 100 finished, 0 failed, peak 10 replicas** | within 2 s of Phase 1's 1m02s on other hardware, pre-signing |
| Signed / total events | 2 / 6 | terminal-only by policy |
| Audit | 10 chains `BOUND`, forgeries `ORPHANED` | `signed-ok 24, terminals 14 (12 verified)` |
| Replay guard | 35 stale requests dropped | no agent re-run |

Rig: single-node Kind, podman, arm64, 6 CPU / 11.6 GiB allocatable, ephemeral Kafka.
**Every figure came through a port-forward, not the ingress** —
[#9](https://github.com/rossoctl/eventing-lab/issues/9) is unchanged.

**Do not quote a wake under a second.** A second run reported `0.1s` because a pod was
still spinning down from a prior stage, so KEDA flipped `Active=True` immediately. The
honest cold-wake figure is the 6.3 s above.

## Cleaning up between runs

The demo is self-cleaning — every beat ends at zero replicas, and a plain re-run mints
fresh ids. The only nuisance is accumulated history: old batches in `/v0/groups`, and a
growing audit summary.

```sh
# rehearsing: nothing to do, just re-run

# clean audit screen for a recording — deletes the app objects AND both
# KafkaTopics with their history, keeps the namespace
python3 scripts/k8s_teardown.py --kubeconfig .kube/config-kind --purge --yes
python3 scripts/k8s_deploy.py --yes --kubeconfig .kube/config-kind \
  --overlay kind-signed-device --signing-keys ~/.eventing-keys --tag local1

# done for the day, want it back tomorrow: pauses, keeps topics AND data
python3 scripts/k8s_teardown.py --kubeconfig .kube/config-kind
python3 scripts/k8s_teardown.py --kubeconfig .kube/config-kind --resume
```

After a purge, re-establish the port-forward and re-source `.demo-env` — the pod is new.
Two things that look alarming after a pause and are correct: a paused ScaledObject still
reports `Active=True` while messages sit in the topic, and the Route stays resolvable
while pointing at zero endpoints, so it serves a 503 rather than a DNS error.

## If you are over time

Cut in this order. Each cut is stated on a slide, never narrated as if it ran.

1. **Beat 3** (back to zero) — fold it into beat 2's tail as a single `+30 s` jump.
2. **Beat 5's chain view** — keep the summary line only.
3. **Beat 1's 403 variant**, if you added it.
4. **Beat 2** — the most expensive 90 s in the demo. Cutting it costs the most, so cut it
   last; say the figure from a slide if you must.

**Never cut beats 1 and 4.** Those two are the talk: who may ask, and who may answer.

---

## The one-command fallback

If the edit falls apart the night before, this drives acts 1–7 with assertions and timings
and can be screen-captured as-is. Less cinematic, entirely honest, and it prints its own
verdict:

```sh
python3 scripts/k8s_demo_flow.py --kubeconfig .kube/config-kind \
  --overlay kind-signed-device \
  --gh-login "$DEMO_GH_LOGIN" --public-url "$EB" --tag local1 --group 100
```

---

## Pre-flight, the night before

- [ ] `k8s_demo_flow.py` reports **all checks passed** on the rig you will record on
- [ ] Beat 2 rehearsed on THIS rig — the fan-out reached 10 pods and drained
- [ ] `DEMO_GH_LOGIN` is non-empty (`login.py` can emit a token without it; §8.3)
- [ ] Port-forward up; **the public Route is NOT exposed** ([#1](https://github.com/rossoctl/eventing-lab/issues/1): `PUT /transcript` is unauthenticated)
- [ ] Every number that reaches a slide is labelled with the cluster it came from
- [ ] Terminal font large enough to read at the back of the room
- [ ] `watch_topics.py` pane is wide enough that the claimed/unclaimed split does not wrap
