# eventing-lab — signed-event agent wake path

**Do you want to run AI agent fleets on the Kubernetes cluster you already have?**

This is a reference pattern built from well-tested components you probably already run:
**Strimzi** with Apache Kafka to carry agent requests as **CloudEvents** with a
tamper-evident audit log, and **KEDA** to autoscale agents from zero and back. No new
platform — two small services and a wire contract.

See it working, and find out where a composition like this actually leaks:

- **▶ [Demo video recording](https://youtu.be/s6494nZZ-hM)** — zero replicas → signed task
  event wakes the agent → forged event refused → completion chained back to the wake →
  back to zero
- **🎤 [The Seams Are the Trap: Agent Fleets from CNCF Parts You Already Have](https://events.linuxfoundation.org/kubecon-cloudnativecon-north-america/program/schedule/?id=1248035)** —
  KubeCon + CloudNativeCon NA, **Thu 12 November 2026, 1:50–2:20 PM MST** ·
  Salt Palace, Level 2, Room 255 BC
- **📋 [The four seams, with honest status](docs/notes/kubecon-na-2026.md)** — two closed,
  two still open, and we say which

```text
  HTTP  →  EventBridge  →  Kafka:requests  →  EventRunner  →  claude -p
                                                                  │
                                                                  ▼
   HTML view / SSE  ←  EventBridge  ←  Kafka:responses  ←  stream-json → CloudEvents
```

> Extracted from [`rossoctl/examples`](https://github.com/rossoctl/examples) (`eventing/`),
> with git history preserved.

---

## Where to go next

| If you want to… | Go to |
|---|---|
| **See it work** | the [demo recording](https://youtu.be/s6494nZZ-hM), then [`docs/demos/`](docs/demos/) |
| **Follow the KubeCon talk** | [`docs/notes/kubecon-na-2026.md`](docs/notes/kubecon-na-2026.md) (the four seams) and the [run-of-show](docs/demos/kubecon-na-2026-demo.md) |
| **Understand it without reading code** | [`docs/eventing-lab-overview.md`](docs/eventing-lab-overview.md) |
| **Run it on your laptop** | [Quickstart](#quickstart) below, then [`docs/demos/phase1-demo.md`](docs/demos/phase1-demo.md) |
| **Run it on Kubernetes** | [`docs/demos/phase1-demo.md`](docs/demos/phase1-demo.md) §3 (Kind) or §4 (OpenShift) |
| **Fan out a batch of agents** | [`docs/demos/agent-groups-demo.md`](docs/demos/agent-groups-demo.md) — three agents, then 100 |
| **Drive it from the CLI** | [`docs/demos/eventbridge-cli.md`](docs/demos/eventbridge-cli.md) — the `/eventbridge` skill |
| **Change the code** | [`docs/specs/0001-phase0-local-wire-contract.md`](docs/specs/0001-phase0-local-wire-contract.md) — the wire contract nothing may break |
| **Know what is real vs designed** | [Status](#status-at-a-glance) below, and [`docs/specs/README.md`](docs/specs/README.md) |
| **Find anything else** | [`docs/README.md`](docs/README.md) — the documentation router |

Full documentation index: **[`docs/README.md`](docs/README.md)**. It routes by intent and
is the authoritative map; this file is the summary.

---

## What it is

Two services, and one constraint that shaped every phase.

**EventBridge** — the HTTP front door (`:8080`): Kafka producer and consumer, SQLite
event store, HTML/SSE transcript views, group fan-out, ntfy notifications.

**EventRunner** — the Kafka consumer: spawns `claude -p --output-format stream-json` per
request and converts each frame into a response CloudEvent.

**The constraint: pure Python, no C extensions**
([spec 0001 §1.1](docs/specs/0001-phase0-local-wire-contract.md)). It is why Ed25519 is
implemented from RFC 8032 by hand — and why the SPIRE upgrade path is expensive.

Two decisions worth knowing before you read the code:

- **Correlation identity is human-readable; session identity is not.** A run gets a slug
  like `brave-otter-4718`; `claude` gets `uuid5(NAMESPACE, correlationid)` because it
  requires a real UUID. Continuing a conversation reuses both.
- **Turns are FIFO per conversation, parallel across conversations.** Each correlationid
  gets a slot and its turns serialize in arrival order; distinct correlationids run
  concurrently up to `ER_MAX_CONCURRENT` (default 4). The contract is
  [spec 0001 §4.5](docs/specs/0001-phase0-local-wire-contract.md); `tests/test_router.py`
  proves it.

---

## Status at a glance

Four phases, built in order, each a delta over the last.
[`docs/specs/README.md`](docs/specs/README.md) is the registry and is blunt about which
is which.

| Phase | What it added | State |
|---|---|---|
| **0** — [wire contract](docs/specs/0001-phase0-local-wire-contract.md) | CloudEvent envelope, correlation/session identity, per-correlation FIFO | **Built** |
| **1** — [KEDA on Kubernetes](docs/specs/0002-phase1-keda-kubernetes.md) | scale-to-zero on consumer lag, graceful drain, arbitrary-UID portability | **Built**, measured on OpenShift and Kind |
| **2** — [identity on the event path](docs/specs/0003-phase2-identity-on-the-event-path.md) | event signing + verification, `kid` keyset, GitHub device-flow sign-in, `ce_submitter` | **Built**, run end to end on a cluster — **enforcement defaults off** |
| **3** — [tenancy and agents](docs/specs/0004-phase3-tenancy-agents-triggers.md) | per-user stores, ownership index, declarative `AgentSpec` | **Partial** — `EB_TENANCY_MODE` defaults to `single` |
| **4** — [enrollment and approval](docs/specs/0005-phase4-enrollment-and-permission.md) | enrollment, human-in-the-loop approval | **Design only** |

### Two things to read before you deploy this

**An unconfigured deployment is open.** `EB_AUTH_TOKENS` is unset in every manifest in
`k8s/`, so an unauthenticated `POST /v0/agents` returns 202 and spawns a run — and an
OpenShift Route makes that reachable from outside the cluster. Signing enforcement
(`ER_REQUIRE_SIGNATURE`, `EB_REQUIRE_RESPONSE_SIGNATURE`) also defaults to `false`. Set
them whenever the Route is reachable by anyone you would not hand a shell to. Recipe:
[`docs/demos/phase1-demo.md`](docs/demos/phase1-demo.md) § "Submit-path authentication".

**Phase 3's `multi` mode is for developing against, not running.** Each tenant gets its
own store and topics, but neither consumer subscribes to the per-user topics yet, so **no
response is ever consumed** — transcript pages stay empty and group counters never
advance. Reads are not owner-scoped either
([`docs/reports/phase3-report.md`](docs/reports/phase3-report.md) §5).

---

## Quickstart

Requires Python 3.14 **free-threaded** (`python3.14t`), `uv`, a local Kafka 4.x (KRaft) on
`:9092` with topics `requests` and `responses`, and the `claude` CLI.

```bash
uv run rossoctl-eventbridge    # terminal 1 — HTTP + Kafka bridge
uv run rossoctl-eventrunner    # terminal 2 — claude executor
```

<details>
<summary><strong>Standing up the broker</strong> — there is no script for it</summary>

Without containers, from the Apache tarball and a JDK 17+:

```bash
tar xzf kafka_2.13-4.1.0.tgz && cd kafka_2.13-4.1.0
sed -i '' "s#^log.dirs=.*#log.dirs=$PWD/data#" config/server.properties   # /tmp may be unwritable
bin/kafka-storage.sh format -t "$(bin/kafka-storage.sh random-uuid)" \
  -c config/server.properties --standalone
bin/kafka-server-start.sh config/server.properties &

for t in requests responses; do
  bin/kafka-topics.sh --bootstrap-server 127.0.0.1:9092 \
    --create --topic $t --partitions 12 --replication-factor 1
done
```

**12 partitions matches the Phase 1 overlays**, so local and cluster runs stay comparable.
In `kafka-consumer-groups.sh --describe`, **lag means unfinished work, not lateness** — the
offset commit is deferred to the terminal event, which is what makes lag a usable queue
gauge during a batch.

Prefer containers? [`docs/demos/phase1-demo.md`](docs/demos/phase1-demo.md) §2 stands up
Kafka and both services under Docker in one command.

</details>

```bash
CORR=$(curl -sS -X POST http://127.0.0.1:8080/v0/agents \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Name one animal starting with O.","max_turns":1}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["correlationid"])')

open "http://127.0.0.1:8080/v0/agents/$CORR"     # live HTML transcript
```

Stop everything with `scripts/stop-demo.sh`.

**Mock mode is the default when no credential is in the environment** — EventRunner
returns a deterministic `system → assistant → result` sequence with the same wire shape
as real Claude. No subprocess, no tokens, and it tells wire problems apart from `claude`
problems. The startup line always states the mode and why:

```text
[eventrunner] bootstrap=… mock_claude=True (auto: none of ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY set)
```

`ER_MOCK_CLAUDE` overrides the auto-detection. Set `ER_MOCK_CLAUDE=false` if you
authenticated via `claude login` — there is no env var to detect in that case.

Full runbooks for Docker, Kind and OpenShift:
**[`docs/demos/phase1-demo.md`](docs/demos/phase1-demo.md)**.

---

## What is in the box

| Path | Role |
|---|---|
| `eventbridge/` | HTTP front door, Kafka producer + consumer, SQLite store, HTML/SSE views, ntfy fan-out |
| `eventrunner/` | Kafka consumer; spawns `claude` per request, converts stream-json to CloudEvents |
| `shared/` | CloudEvent envelope helpers, binary-mode Kafka binding, Ed25519 signing, keyset |
| `k8s/` | Kustomize base + overlays (`test`, `kind`, `kind-signed`, `kind-signed-auth`, `kind-signed-device`, `demo`) |
| `scripts/` | Build, deploy, preflight, e2e and demo-flow drivers |
| `skills/` | The `/eventbridge` CLI skill |
| `tests/` | Unit tests — no Kafka or `claude` needed |
| `docs/` | Specs, reports, demos, ADRs, notes — see [`docs/README.md`](docs/README.md) |

```bash
uv run pytest tests -q          # the whole suite; no broker, no claude
```

### HTTP surface

The six endpoints you need to start; the rest are in the live Swagger UI at **`GET /docs`**.

| Endpoint | Returns |
|---|---|
| `POST /v0/agents` | Start a run; mints the correlationid |
| `POST /v0/agents/{corr}/continue` | Another turn on the same `claude` session |
| `GET /v0/agents/{corr}` | Live HTML transcript, turn-grouped, SSE-backed |
| `GET /v0/agents/{corr}/events.jsonl` | Raw CloudEvents as ndjson |
| `POST /v0/groups` | Create a batch and fan out its members |
| `GET /v0/groups/{gid}` | Live batch progress: percent, ETA, per-member rows |

Every response event carries `data.text` at the top level, so extracting a reply never
means walking into `data.raw.message.content[0].text`.

### Configuration

The six that change behaviour on a first run. The full set — Phase 1 scaling, Phase 2
signing, Phase 3 tenancy — is in each phase's spec, linked from
[Status](#status-at-a-glance).

| Env | Default | Effect |
|---|---|---|
| `ER_MOCK_CLAUDE` | auto | Deterministic fake responses, zero cost. Unset = mock unless a credential is present |
| `ER_MAX_CONCURRENT` | `4` | Distinct correlationids running in parallel |
| `KAFKA_BOOTSTRAP` | `localhost:9092` | Broker address |
| `EB_AUTH_TOKENS` | — **(open)** | `name:token` pairs guarding the two routes that create work. Empty means no authentication |
| `EVENT_BRIDGE_PUBLIC_BASE_URL` | — | Used verbatim in HTML and ntfy links |
| `NTFY_TOPIC` / `NTFY_ENABLED` | off | Push `result`/`error` phases to a phone |

Two are load-bearing in ways that are easy to miss. `ER_EVENTBRIDGE_URL` is what makes
`/continue` survive a scale-to-zero *and* what keeps event `sequence` numbers unique
across pods. `ER_CONSUMER_GROUP` must equal the KEDA trigger's `consumerGroup` exactly —
a mismatch is a silent no-scale failure.

---

## Measured

Phase 1 timings, from [`docs/reports/phase1-report.md`](docs/reports/phase1-report.md) §3:

| | OpenShift (ykt1) | Kind |
|---|---|---|
| e2e assertions | **49/49** | **37/37** |
| POST → KEDA scales up | 1.7–3.0 s | 2.2–4.3 s |
| POST → pod Ready | ~14 s | — |
| POST → back to zero | **22 s** (cooldown 5) | — |

**KEDA's detection was never the slow part.** The remaining ~12 s is pod startup, which
no KEDA setting affects — pre-pulling the 245 MB image is the lever. Dropping
`cooldownPeriod` from 30 to 5 took the whole loop from 46.5 s to 22 s; the 30 s had been
an idle pod lingering, not a wake delay.

The signed path has since run end to end on a cluster with enforcement on in both
directions. Seven bugs surfaced, **every one of them reachable only on a cluster and
every one sitting behind a green test report** —
[`docs/reports/phase2-cluster-run-report.md`](docs/reports/phase2-cluster-run-report.md)
§4 is the record, and it is the best argument in the repo for why a venv harness proves a
decision but only a deployment proves a control.

> Kind sets `group.initial.rebalance.delay.ms: 0`; ykt1 leaves it at 3000 ms. **Kind
> timings are not comparable to cluster timings.**

---

## Documentation

[`docs/README.md`](docs/README.md) is the router — it routes by intent and explains the
lifecycle that decides where a new document goes. The short version:

| | |
|---|---|
| [`docs/eventing-lab-overview.md`](docs/eventing-lab-overview.md) | **start here if you will not open the code** — what each phase decided, built vs designed |
| [`docs/specs/`](docs/specs/) | the five phase designs — what *will* be true, revised in place |
| [`docs/reports/`](docs/reports/) | what each phase **actually built and measured** — append-only, never rewritten |
| [`docs/demos/`](docs/demos/) | performed runbooks — the [KubeCon run-of-show](docs/demos/kubecon-na-2026-demo.md) and the [screenplay](docs/demos/kubecon-na-2026-screenplay.md) the recording was cut from |
| [`docs/adrs/`](docs/adrs/) | one decision per file, permanent |
| [`docs/glossary.md`](docs/glossary.md) | terms |

**Writing a claim about a security control?** Read
[spec 0003 §8.5](docs/specs/0003-phase2-identity-on-the-event-path.md) first — five
questions to ask before it ships, derived from thirteen claims in this repository that a
docs review disproved.

**Something behaving surprisingly?**
[`docs/reports/phase1-report.md`](docs/reports/phase1-report.md) §4 — most surprises are
already recorded there with their root cause.

---

## Contributing

The contribution process is documented once for the whole project in
[`rossoctl/rossoctl`](https://github.com/rossoctl/rossoctl/blob/main/CONTRIBUTING.md);
this repo's specifics are in [`CONTRIBUTING.md`](CONTRIBUTING.md). Governance:
[`GOVERNANCE.md`](GOVERNANCE.md).

Licensed under [Apache 2.0](LICENSE).
