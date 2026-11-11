# Agent groups — batch fan-out, and the notification behaviour people get wrong

Status: Implemented

Two runs, back to back, that demonstrate group fan-out and tracked fan-in — plus the
ntfy setup they rely on.

Moved here from the root `README.md`, which now summarises and links rather than
inlining runbooks. **The commands are unchanged**; only the heading levels shifted.

**Prerequisite:** a deployment you can reach. Any of the three environments in
[`phase1-demo.md`](phase1-demo.md) works — local, Kind or OpenShift. Point the CLI at it
once with `EVENTBRIDGE_URL`, as described in [`eventbridge-cli.md`](eventbridge-cli.md).

---

## Demo scenario: three agents, then a batch of 100

Two runs, back to back, that show the one behaviour people get wrong when they first
see this: **whether a run is part of a batch changes how many notifications your phone
gets.** Three loose agents send three notifications. A hundred agents in a batch send
two. Run them in this order — the contrast is the demo.

Everything below was executed on ykt1 on 2026-09-23 against the `test` overlay (mock
agents, so it costs no tokens). The numbers in the output blocks are measured, not
illustrative.

```bash
cd examples-aslom/eventing
CLI="python3 skills/eventbridge/eventbridge-cli.py"

# Point the CLI at the cluster once. Everything below then needs no --base-url, and
# the CLI echoes `(from $EVENTBRIDGE_URL)` on every run so the target is never in doubt.
export EVENTBRIDGE_URL=eventbridge-kev1.apps.ykt1.hcp.res.ibm.com

# The baseline to compare against afterwards. An ntfy topic is a capability, so this
# reads your own topic — substitute yours.
curl -s "https://ntfy.sh/$NTFY_TOPIC/json?poll=1&since=all" | wc -l
```

Only the pod-watching step later on needs `kubectl` (and a
`KUBECONFIG=…/config-ykt1`); the CLI itself never touches a cluster API.

## Part 1 — three separate agents, three notifications

Ask the skill for it in words:

> **on ykt1, run agent1 to say hello, agent2 to say world and agent3 to say again,
> one word each**

which it turns into exactly one command — `runmany`, never three `run` calls:

```bash
$CLI runmany "Say hello. One word." "Say world. One word." "Say again. One word." \
  --label agent1 --label agent2 --label agent3
```

```text
$ python3 skills/eventbridge/eventbridge-cli.py runmany 'Say hello. One word.' 'Say world. One word.' 'Say again. One word.' --label agent1 --label agent2 --label agent3
· endpoint https://eventbridge-kev1.apps.ykt1.hcp.res.ibm.com (from $EVENTBRIDGE_URL)
▸ agent1 patient-whale-1324  · https://…/v0/agents/patient-whale-1324
▸ agent2 bitter-leopard-8584 · https://…/v0/agents/bitter-leopard-8584
▸ agent3 cold-puma-7650      · https://…/v0/agents/cold-puma-7650
· 3 agent(s) submitted together — KEDA should scale to 3 pod(s)
agent3 ASSISTANT: MOCK-REPLY[Say again. One word.]   agent3 ✔ done · 1.4s · 20→32 tokens
agent1 ASSISTANT: MOCK-REPLY[Say hello. One word.]   agent1 ✔ done · 1.4s · 20→32 tokens
agent2 ASSISTANT: MOCK-REPLY[Say world. One word.]   agent2 ✔ done · 3.9s · 20→32 tokens
✔ 3/3 agent(s) finished
```

**What to verify — exactly 3 notifications, each pointing at its own page:**

```text
title : agent cold-puma-7650 · 1.4s
click : https://…/v0/agents/cold-puma-7650
body  : ❓ Say again. One word.  💬 MOCK-REPLY[Say again. One word.]
title : agent patient-whale-1324 · 1.4s      click: …/v0/agents/patient-whale-1324
title : agent bitter-leopard-8584 · 3.9s     click: …/v0/agents/bitter-leopard-8584
```

And **no batch exists** — that is the half of this part that is easy to skip:

```bash
for c in patient-whale-1324 bitter-leopard-8584 cold-puma-7650; do
  curl -s -o /dev/null -w "$c HTTP %{http_code}\n" "https://$EVENTBRIDGE_URL/v0/agents/$c"
  curl -s "https://$EVENTBRIDGE_URL/v0/agents/$c" | grep -c '<div class="groupback">'   # 0
  curl -s "https://$EVENTBRIDGE_URL/v0/agents/$c/turns" | grep -o '"groupid": *[^,]*'   # null
done
curl -s -H 'Accept: application/json' "https://$EVENTBRIDGE_URL/v0/groups"   # count unchanged
```

```text
patient-whale-1324     HTTP 200 · groupback divs: 0 · groupid: None
bitter-leopard-8584    HTTP 200 · groupback divs: 0 · groupid: None
cold-puma-7650         HTTP 200 · groupback divs: 0 · groupid: None
groups: 15  (15 before, so no group was created)
```

Three notifications for three agents is the right behaviour at this scale, and the
reason batches exist: it does not survive being multiplied by 100.

The same watcher as Part 2 (below) shows KEDA following the lag exactly — and catches a
trap worth seeing once, at `t=16s`:

```text
t=6s   replicas=1 running=0     # KEDA activates
t=8s   replicas=3 running=0     # scales to the lag: 3 requests, 3 pods
t=12s  replicas=3 running=1
t=16s  replicas=0 running=2     # ← spec.replicas is 0 while two pods are still running
t=20s  replicas=0 running=0
```

`spec.replicas == 0` does **not** mean no consumer is running: a pod lives until it
exits or `terminationGracePeriodSeconds` elapses, and until then it is still a group
member consuming from the topic. Any "is it idle yet?" assertion has to require zero
replicas *and* no surviving pod.

## Part 2 — one batch of 100 agents, two notifications

The simplest phrasing that gets there — no mention of groups, templates or counters:

> **on ykt1, run a batch of 100 agents, each saying Hello N**

"batch" (or "as a group", or "100 agents") is what selects `group run`; "each saying
Hello N" is what becomes the template. One command, and it monitors itself:

```bash
$CLI group run --template "Say Hello {n}" --count 100 --label hello-100-demo
```

`{n}` is 1-based (`{i}` is 0-based, `--start` shifts it). Do not pass 100 positional
prompts — the CLI echoes the first and last generated prompt so the pattern is
reviewable before anything runs, which 100 shell-quoted arguments are not. Watching is
the default, so progress lands in the transcript with no second command:

```text
$ python3 skills/eventbridge/eventbridge-cli.py group run --template 'Say Hello {n}' --count 100 --label hello-100-demo
· endpoint https://eventbridge-kev1.apps.ykt1.hcp.res.ibm.com (from $EVENTBRIDGE_URL)
▸ group hello-100-demo bold-hornet-2962 · https://…/v0/groups/bold-hornet-2962
  all groups · https://…/v0/groups
· 100 agents submitted together (from template 'Say Hello {n}')
  first: 'Say Hello 1'   last: 'Say Hello 100'
  0/100 done · 100 queued · 8s elapsed
  4/100 done · 4 running · 92 queued · 24s elapsed · about 2 minutes left
  41/100 done · 16 running · 43 queued · 45s elapsed · less than a minute left
  88/100 done · 5 running · 7 queued · 1m00s elapsed · less than a minute left
  100/100 done · 1m20s elapsed
✔ group complete · 100 finished · 0 failed · 1m20s
```

**What to verify — exactly 2 notifications, both pointing at the group page:**

```text
title : group hello-100-demo started
click : https://…/v0/groups/bold-hornet-2962
body  : 🚀 100 agent(s) submitted · 📎 bold-hornet-2962

title : group hello-100-demo · 100 done
click : https://…/v0/groups/bold-hornet-2962
body  : ✅ 100 finished · 🎯 of 100 expected · ⏱ 1m20s · 🐢 slowest agent 79.6s
```

Not 100, and not 102: while a group is open its members' own notifications are
suppressed, keyed on **membership** rather than on the group still being open — so a
redelivered request whose duplicate terminal event arrives *after* completion stays
quiet too. Each member page gains a link back the other way:

```bash
curl -s "https://$EVENTBRIDGE_URL/v0/groups/bold-hornet-2962/status" | head -c 300
curl -s "https://$EVENTBRIDGE_URL/v0/agents/fresh-mole-0029" | grep -o '<div class="groupback">.*</div>'
```

```text
label hello-100-demo · finished/expected 100/100 · reason: all · members 100
<div class="groupback">← back to group <a href="/v0/groups/bold-hornet-2962">hello-100-demo</a> <code>bold-hornet-2962</code></div>
```

**And the fan-out, in a second terminal while Part 2 runs:**

```bash
while true; do
  printf '%s replicas=%s running=%s\n' "$(date +%T)" \
    "$(kubectl -n kev1 get deploy eventrunner -o jsonpath='{.spec.replicas}')" \
    "$(kubectl -n kev1 get pods -l app.kubernetes.io/name=eventrunner \
        --no-headers 2>/dev/null | grep -c Running)"
  sleep 3
done
```

Note the selector is `app.kubernetes.io/name=eventrunner`; a plain `app=eventrunner`
matches nothing and reports a silent `0` throughout. Measured:

```text
t=6s   replicas=10   # the explicit HPA scaleUp policy jumps straight to the cap
t=12s  replicas=10 running=1
t=30s  replicas=10 running=8
t=33s  replicas=0    # the batch drained
```

Reaching the cap in one step rather than climbing `1 → 5 → 10` over ~30 s is the
`behavior.scaleUp` policy in `k8s/base/eventrunner-scaledobject.yaml` doing its job:
KEDA handles 0 → 1 but delegates 1 → N to an HPA, whose default policy adds at most
`max(100%, 4 pods)` per 15 s.

`maxReplicaCount` is **10**, under the topic's **12** partitions. Partitions are the
real ceiling — Kafka gives each partition to exactly one consumer in a group, and KEDA
enforces the same bound by default (`allowIdleConsumers: false`) — so the cap can be
lowered freely but raising it past 12 would mean raising partitions first, which is a
one-way door. With 10 consumers over 12 partitions, two of them simply own two
partitions each.

## The summary to read back

| | Part 1 — three loose agents | Part 2 — a batch of 100 |
|---|---|---|
| Command | `runmany "…" "…" "…"` | `group run --template … --count 100` |
| ntfy notifications | **3** — one per agent | **2** — group started, group finished |
| Each notification links to | that agent's own page | the group page |
| Group created | none (`groupid: null`) | one, with 100 member rows |
| Wall clock | ~4 s | ~1m20s |
| Reproduced | twice, delta 3 both times | delta 2 |
| Peak `spec.replicas` | 3 | 10 (the cap) |

If Part 2 sends more than two notifications, the suppression regressed. If a group
page 404s after a pod restart, check the log for `[group-mirror] rebuilt N group(s)` —
`/data` is `emptyDir` in this overlay, so group state is rebuilt from the `responses`
topic on every start.

## Phone notifications (ntfy)

Off by default. An **ntfy topic name is a capability** — anyone who knows it can read
*and* publish your notifications — so it is never committed. Export it and deploy:

```bash
export NTFY_TOPIC=<your-ntfy-topic>
export NTFY_TOKEN=<token>                 # only for a protected topic
python3 scripts/k8s_deploy.py --yes
```

**Running locally, `NTFY_TOPIC` alone is not enough — set `NTFY_ENABLED=true` too.**
`eventbridge/config.toml` ships `enabled = false`, and the env override derives its own
default from whatever the file loaded (`e("NTFY_ENABLED", "true" if cfg.ntfy.enabled
else "false")`), so an unset `NTFY_ENABLED` leaves ntfy off no matter what the topic is.
The startup banner is the check — it must say `ntfy=on`:

```bash
NTFY_ENABLED=true NTFY_TOPIC=$NTFY_TOPIC \
  EVENT_BRIDGE_PUBLIC_BASE_URL=http://<lan-ip>:8080 \
  .venv/bin/python -m eventbridge | grep 'ntfy='
# [eventbridge] bootstrap=… http=… ntfy=on
```

With `ntfy=off` nothing is sent and nothing is logged, which looks exactly like a
delivery failure. Also set `EVENT_BRIDGE_PUBLIC_BASE_URL` to a LAN address: the default
`127.0.0.1` still delivers a fully readable notification, but the click-through and the
`Continue…` action button resolve to loopback and do nothing from a phone.

`k8s_deploy.py` stores it as `Secret/eventbridge-ntfy`, which the base Deployment
references with `optional: true` — listed after the ConfigMap so its
`NTFY_ENABLED=true` overrides the ConfigMap's `"false"`. Confirm with:

```bash
kubectl -n kev1 logs deploy/eventbridge | grep ntfy      # -> ntfy=on
curl -s "https://ntfy.sh/$NTFY_TOPIC/json?poll=1&since=10m"
```

Each notification carries the prompt, the reply, a stats line and a click-through to
the correlation's HTML view at `EVENT_BRIDGE_PUBLIC_BASE_URL`.

Both images install `ca-certificates` **and** symlink `/etc/ssl/cert.pem` to it,
because the bundled standalone CPython looks for the former path while Debian writes
the latter. Without both, every outbound HTTPS call fails with
`CERTIFICATE_VERIFY_FAILED` and ntfy silently delivers nothing.


---

*Assisted-By: Claude Code*
