# The `/eventbridge` CLI skill

Status: Implemented

How to drive any of the three environments from the command line, including the
`EVENTBRIDGE_URL` convention that saves passing `--base-url` on every call.

Moved here from the root `README.md`, which now summarises and links rather than
inlining runbooks. **The commands are unchanged**; only the heading levels shifted.

See also: [`agent-groups-demo.md`](agent-groups-demo.md) for the batch fan-out scenario
this CLI drives, and [`phase1-demo.md`](phase1-demo.md) for standing an environment up.

---

## Driving it with the `/eventbridge` skill

The skill CLI works against any of the three environments. It defaults to
`http://127.0.0.1:8080`, so **pass `--base-url` when you mean a cluster** —
silently hitting localhost is the confusing failure mode.

```bash
# The normal case: set it once, then no flag on any command. A bare hostname is
# assumed https, so a Route host can be pasted straight in.
export EVENTBRIDGE_URL=eventbridge-kev1.apps.ykt1.hcp.res.ibm.com
python3 skills/eventbridge/eventbridge-cli.py run "Say hi"

# Or per command, when you mean a different target just this once
python3 skills/eventbridge/eventbridge-cli.py run "Say hi" \
  --base-url http://eventbridge.127.0.0.1.nip.io:30080      # kind
```

Precedence is `--base-url` > `$EVENTBRIDGE_URL` > `http://127.0.0.1:8080`, and
**the CLI reports which of the three it used on every run**, including the default:

```text
· endpoint https://eventbridge-kev1.apps.ykt1.hcp.res.ibm.com (from $EVENTBRIDGE_URL)
· endpoint http://127.0.0.1:8080 (from default)
```

That line used to be printed only when the endpoint was *not* the default, which is
backwards: the silent case was the one that needed announcing. A run that quietly went
to localhost reads exactly like "the agent never replied".

**There is no discovery step, by design.** The CLI is pure HTTP — it never invokes
`kubectl`, and neither should an agent driving it (`SKILL.md` says so explicitly, and a
test asserts the CLI cannot start a subprocess at all). If the endpoint is wrong or
down it says so and exits 2 with the fix, so nothing needs to pre-check `/healthz`:

```text
✖ cannot reach EventBridge at http://127.0.0.1:8080 ([Errno 61] Connection refused)
  endpoint came from: default
  No endpoint was given, so this used the laptop default. Point it at the
  right EventBridge — nothing here discovers one for you:
      export EVENTBRIDGE_URL=https://<eventbridge-host>   # whole session
      … --base-url https://<eventbridge-host>             # one command
```

**Every run echoes its own command first**, shell-quoted and pasteable, so a
transcript can be replayed by hand and a wrong flag is visible where it was used
rather than in the results:

```text
$ python3 skills/eventbridge/eventbridge-cli.py group run --template 'Say Hello {n}' --count 100 --label hello-100 --base-url https://eventbridge-kev1.apps.ykt1.hcp.res.ibm.com
```

`EVENTBRIDGE_ECHO=0` silences it; a `user:pass@` in any URL argument becomes `***@`
before printing.

**Submit, then poll — don't block.** A tool call (or a CI step) shows its output when
the command *exits*, so one long watching command reports nothing for minutes and then
everything at once, or gets killed at a timeout and reports nothing at all. Submit with
`--no-watch`, which returns in ~2 s with every correlationid, then check each one in its
own short bounded call:

```bash
$CLI runmany "Say hello." "Say world." --label agent1 --label agent2 --no-watch
$CLI watch sweet-crab-0956 --timeout 30      # seconds, not minutes
$CLI group status <gid>                      # one-shot counts, no waiting
```

Stdout is line-buffered explicitly, so partial progress survives a kill rather than
dying in an 8 KiB pipe buffer. And don't append `2>&1`: it does nothing for latency and
throws away whether a line was progress or `✖ cannot reach EventBridge`.

The skill lives in the repo at **`skills/eventbridge/`** and is exposed to Claude
Code through a symlink from the user's skills directory, so there is exactly one copy
to edit and it is versioned with the code it drives.

**Two agents at once — and why the order matters:**

```bash
python3 skills/eventbridge/eventbridge-cli.py runmany \
  "Say hello. One word." "Say world. One word." \
  --label agent1 --label agent2
```

`runmany` POSTs every prompt *before* it watches any of them, so N requests land on
the topic together, KEDA sees lag N and scales to N pods. Two sequential `run` calls
would each finish before the next was submitted — lag would never exceed 1, one pod
would serve both, and nothing would scale. Observed on ykt1:

```text
· endpoint https://eventbridge-kev1.apps.ykt1.hcp.res.ibm.com (from $EVENTBRIDGE_URL)
▸ agent1 grumpy-beetle-8286 · https://…/v0/agents/grumpy-beetle-8286
▸ agent2 gentle-manatee-0993 · https://…/v0/agents/gentle-manatee-0993
· 2 agent(s) submitted together — KEDA should scale to 2 pod(s)
agent1 ASSISTANT: MOCK-REPLY[Say hello. One word.]
agent2 ASSISTANT: MOCK-REPLY[Say world. One word.]
✔ 2/2 agent(s) finished

spec_replicas=1 pods=0     # KEDA activates
spec_replicas=2 pods=2     # then scales to the lag
```

Both turns produced their own ntfy notification.

**Setup needed before running the skill: just the endpoint.** The CLI is
stdlib-only (no venv), `NTFY_TOPIC` is a property of the *deployment* rather than of
this client, and mock-vs-real is decided by the EventRunner deployment. `kubectl` is
only needed if you want to watch pods.

**Two URLs, easily confused.** `--base-url` / `EVENTBRIDGE_URL` is *client side* —
where the CLI sends requests. `EVENT_BRIDGE_PUBLIC_BASE_URL` is set *on the
deployment* and is what EventBridge advertises in HTML links and ntfy actions.
Changing one does not change the other.

Against the `test` and `kind` overlays every reply is `MOCK-REPLY[...]` — those
overlays mount no credential, so mock mode is auto-selected. That is expected.


---

*Assisted-By: Claude Code*
