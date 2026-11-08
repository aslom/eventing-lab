#!/usr/bin/env bash
# Inject ONE correctly-encoded but UNSIGNED request onto the requests topic.
#
# Part 3b of the demo. Lifted from the FORGE_REQUEST_SRC beat in
# eventing-lab/scripts/k8s_demo_flow.py so the event is byte-identical in shape to one
# EventBridge would publish -- it imports the real `shared.ce` inside a throwaway pod on
# the eventrunner image, and the ONLY difference from a genuine wake is the missing
# signature. That is the point: the demo must refuse a CORRECT forgery, not a malformed
# one, or it proves nothing about the signature check.
#
# Narrates every step, because this is run in front of an audience. A script that
# prints one line and then stalls for 20 seconds inside `kubectl run` looks broken, and
# the operator cannot tell a slow image pull from a hung cluster. Each step says what it
# is about to do and what it found.
#
# Usage:  ./inject-unsigned.sh            # mints its own correlation id
#         ./inject-unsigned.sh my-corr-1
#         NO_COLOR=1 ./inject-unsigned.sh # plain output for a recording
set -euo pipefail

KC="${KC:?export KC=/path/to/kubeconfig-ykt1 first}"
NS="${NS:-kev1}"
# Default: whatever the eventrunner Deployment is ACTUALLY running, read from the
# cluster. A hardcoded ref was wrong in two ways at once — it pinned one cluster's
# internal registry, and it named `rossoctl-eventrunner` while ykt1 runs
# `rossoctl-eventrunner-claude`, so the forgery pod and the thing it is meant to
# impersonate were built from different images. The whole claim of this script is
# "byte-identical in shape to a genuine wake", which requires the same `shared.ce`.
# Overridable for a cluster where the Deployment is named differently.
IMAGE="${IMAGE:-}"
CORR="${1:-forged-$(date +%s)}"

# Bold for the step headers only. Dim/colour is skipped when stdout is not a terminal
# (a pipe, a recording harness) or NO_COLOR is set, so the transcript stays readable.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[0m'
else
  B=""; D=""; R=""
fi

step=0
say() { step=$((step + 1)); printf '%s[%d/5] %s%s\n' "$B" "$step" "$1" "$R"; }
note() { printf '      %s%s%s\n' "$D" "$1" "$R"; }
t0=$(date +%s)

printf '\n%s=== Part 3b: forge a wake event ===%s\n' "$B" "$R"
note "target cluster: $(kubectl --kubeconfig "$KC" config current-context 2>/dev/null || echo '?')"
note "namespace:      $NS"
note "correlation id: $CORR   (EventBridge never minted this)"
echo

say "Reading the broker address and topic from the deployment's own ConfigMap"
note "so the forgery goes exactly where genuine traffic goes - nothing is hardcoded"
BOOTSTRAP=$(kubectl --kubeconfig "$KC" -n "$NS" get cm eventing-config \
  -o jsonpath='{.data.KAFKA_BOOTSTRAP}')
TOPIC=$(kubectl --kubeconfig "$KC" -n "$NS" get cm eventing-config \
  -o jsonpath='{.data.REQUEST_TOPIC}')
note "topic:     $TOPIC"
note "bootstrap: $BOOTSTRAP"

say "Confirming the runner ENFORCES signatures"
note "without this the beat is vacuous: an unsigned request would simply be accepted"
ENFORCING=$(kubectl --kubeconfig "$KC" -n "$NS" get cm eventing-config \
  -o jsonpath='{.data.ER_REQUIRE_SIGNATURE}')
if [ "$ENFORCING" != "true" ]; then
  printf '      %sER_REQUIRE_SIGNATURE=%s - NOT enforcing.%s\n' "$B" "${ENFORCING:-unset}" "$R"
  note "This forgery would be ACCEPTED and the agent would run it. Stopping."
  note "Set ER_REQUIRE_SIGNATURE=true on the eventing-config ConfigMap first."
  exit 1
fi
note "ER_REQUIRE_SIGNATURE=true - an unsigned request will be refused"

say "Checking the agent is at zero replicas before we start"
note "so the wake we are about to forge is visibly what brings a pod up"
REPL=$(kubectl --kubeconfig "$KC" -n "$NS" get deploy eventrunner \
  -o jsonpath='{.status.replicas}' 2>/dev/null || echo 0)
note "eventrunner replicas: ${REPL:-0}"

say "Publishing the unsigned request from a throwaway in-cluster pod"
if [ -z "$IMAGE" ]; then
  IMAGE=$(kubectl --kubeconfig "$KC" -n "$NS" get deploy eventrunner \
    -o jsonpath='{.spec.template.spec.containers[0].image}')
  [ -n "$IMAGE" ] || { note "could not read the eventrunner image; set IMAGE="; exit 1; }
  note "image taken from deploy/eventrunner, so the codec matches the real runner"
fi
note "image: ${IMAGE##*/}"
note "it imports the REAL shared.ce codec, so the bytes are production-shaped;"
note "the only difference from a genuine wake is the absent ce_signature"
note "(first run may pause ~15s pulling the image - that is not a hang)"

PYSRC=$(cat <<PY
import sys, time
sys.path.insert(0, "/app")
print("  [pod] importing the production CloudEvent codec from /app/shared", flush=True)
from kafka import KafkaProducer
from shared import ce

CORR = "$CORR"
print("  [pod] connecting to $BOOTSTRAP", flush=True)
t = time.time()
p = KafkaProducer(bootstrap_servers="$BOOTSTRAP", acks="all", retries=3)
print("  [pod] producer ready in %.1fs" % (time.time() - t), flush=True)

# mode="start" and the data shape match what handlers.py sends, so if this were accepted
# the agent would really run. The prompt is self-identifying and inert: the signature
# covers datadigest, so the prompt text is immaterial to the verdict. If a regression
# ever accepts it, the transcript says so in its own words.
req = ce.new_event(
    type=ce.TYPE_REQUEST, source="rossoctl://attacker/forge",
    subject="start", datacontenttype="application/json",
    correlationid=CORR, sessionuuid=ce.session_uuid(CORR), mode="start",
    data={"prompt": "FORGERY-EXECUTED: an unsigned request reached the agent. "
                    "Reply with exactly this sentence and stop.",
          "model": None, "max_turns": 1})

# Prove what we are sending, and prove what it LACKS. The absence is the whole point,
# so it is asserted rather than assumed: a bug that accidentally signed this would make
# the beat narrate a refusal that never happened.
print("  [pod] built event id=%s" % req["id"], flush=True)
print("  [pod]   type         = %s" % req["type"], flush=True)
print("  [pod]   source       = %s" % req["source"], flush=True)
print("  [pod]   ce_signature = %s" % req.attrs.get("signature", "<ABSENT - this is the forgery>"), flush=True)
assert "signature" not in req.attrs, "this event is signed; it is not a forgery"

headers, value = ce.to_kafka_binary(req)
print("  [pod] encoded binary CloudEvent: %d header(s), %d byte payload"
      % (len(headers), len(value)), flush=True)
# Keyed by correlation id, as publish_request does: per-correlation FIFO ordering is the
# Phase 0 wire contract, and an attacker wanting to be processed would honour it.
md = p.send("$TOPIC", key=CORR.encode(), value=value,
            headers=[(k, v) for k, v in headers]).get(timeout=30)
p.flush(timeout=30)
print("  [pod] ACCEPTED BY THE BROKER: partition %d, offset %d" % (md.partition, md.offset), flush=True)
print("FORGED REQUEST", CORR, "onto", "$TOPIC", flush=True)
PY
)

kubectl --kubeconfig "$KC" -n "$NS" run "forger-req-$(date +%s)" \
  --rm -i --restart=Never --quiet \
  --image "$IMAGE" --command -- python -c "$PYSRC"

say "Done in $(( $(date +%s) - t0 ))s"
cat <<EOF

${B}The broker took it. That is expected and it is half the point:${R}
  anything with network access to Kafka can write to a topic. It is plaintext
  and this demo does not pretend otherwise. What it cannot do is make an agent ACT.

${B}What should happen next, in this order:${R}
  1. KEDA sees lag on $TOPIC and scales eventrunner 0 -> 1   (~5-15s)
  2. the pod reads the event, finds no ce_signature, and REFUSES it
  3. it commits the offset so the event is not retried forever
  4. no agent runs, so no transcript is ever created
  5. the queue is empty again, so the pod scales back to 0   (~5s)

  "It woke up, declined to work, and went away" - say that, not "nothing happened".
  Claiming replicas stay at zero would be wrong, and anyone watching will catch it.

${B}The evidence, in the watch-refusals.sh pane:${R}
  [consume] rejecting unsigned/badly-signed request corr=$CORR:
  no ce_signature attribute (ER_REQUIRE_SIGNATURE=true) - offset committed, not retried

${B}And confirm no agent ran (expect 400 'bad correlationid' - the id shape is rejected${R}
${B}before any lookup; a well-formed-but-unknown id would give 404):${R}
  curl -sS -w '\\n%{http_code}\\n' "\$EB/v0/agents/$CORR"

${B}Then re-read the audit with both runs marked:${R}
  cd eventing-lab && python3 scripts/audit_topic.py --kubeconfig "\$KC" \\
    --tag \$TAG --registry \$REG --chains --verify \\
    --highlight "\$CORR,$CORR"
EOF
