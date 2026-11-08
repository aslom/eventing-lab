#!/usr/bin/env bash
# The ykt1 three-part demo, as a copy-paste command sheet.
#
# NOT a script to execute. It is the card you hold while performing: paste one block at
# a time and talk over it. `set -e` is deliberately absent for that reason — a beat that
# returns non-zero (an expected 400, an audit that aborts) must not kill the shell you
# are demoing in. `bash -n` passes, so a typo still fails the syntax gate in CI.
#
# The narrated version with the WHY, the limits to say out loud, and what each beat does
# NOT prove is ../docs/demos/ykt1-three-part-demo.md. Change a command here, check there.
#
#   REPO=/path/to/eventing-lab  . scripts/demo-commands.sh   # or just paste blocks
#
# Paths are relative to the repo root and resolved from $REPO (default: the parent of
# this file's directory), because the original carried one operator's absolute home
# path in seven places — unusable by anyone else and silently wrong after a move.
setopt interactive_comments 2>/dev/null || true   # zsh: allow # comments when pasting

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)}"
cd "$REPO" || return 1

export KC="${KC:-$REPO/../.kube/config-ykt1}"
export EB="${EB:-http://127.0.0.1:18080}"
export NS="${NS:-kev1}"
export REG="${REG:-image-registry.openshift-image-registry.svc:5000/kev1/}"
export TAG="${TAG:-local1}"
. ./.demo-env                     # DEMO_GH_TOKEN, DEMO_GH_LOGIN, NTFY_TOPIC


kubectl --kubeconfig "$KC" -n $NS get deploy --request-timeout=15s

curl -sS -H "Authorization: Bearer $DEMO_GH_TOKEN" https://api.github.com/user \
  | python3 -c 'import json,sys; print("github login:", json.load(sys.stdin).get("login","REFUSED"))'


# Login into github if the tokeni s not wokring
cd "$REPO"
eval "$(python3 scripts/login.py --client-id Ov23liA2Z4jfbFRoJKZn --open --export)"

echo "signed in as: ${DEMO_GH_LOGIN:?login did not come back - pass --gh-login by hand}"

python3 - <<'PY'
import os, pathlib, re
p = pathlib.Path(".demo-env")
keep = [l for l in p.read_text().splitlines()
        if not re.match(r"\s*export\s+(DEMO_GH_TOKEN|DEMO_GH_LOGIN)=", l)]
keep += [f"export DEMO_GH_TOKEN={os.environ['DEMO_GH_TOKEN']}",
         f"export DEMO_GH_LOGIN={os.environ['DEMO_GH_LOGIN']}"]
p.write_text("\n".join(keep) + "\n")
p.chmod(0o600)
print("wrote DEMO_GH_TOKEN + DEMO_GH_LOGIN to .demo-env (mode 600)")
PY

# Check config

kubectl --kubeconfig "$KC" -n $NS get cm eventing-config \
  -o jsonpath='{.data.ER_REQUIRE_SIGNATURE}{" "}{.data.EB_REQUIRE_RESPONSE_SIGNATURE}{"\n"}'

# skill config

export EVENTBRIDGE_TOKEN="$DEMO_GH_TOKEN"
export EVENTBRIDGE_URL="$EB" 
python3 skills/eventbridge/eventbridge-cli.py whoami

cd "$REPO"
claude -p "Do you have an eventbridge skill available? Answer YES or NO on the first line." \
  --allowedTools 'Bash' 2>&1 | head -3

python3 scripts/audit_topic.py --kubeconfig "$KC" --tag $TAG --registry $REG | tail -8

####### 
# PANES TO WATCH DEMO SETUP

# (1) Kubernetes state pane
kubectl --kubeconfig "$KC" -n $NS get deploy eventrunner -w

# (2) Strimi/Kafka state
python3 scripts/watch_topics.py --kubeconfig "$KC" --group latest --url "$EB" --interval 2


# (3)

# make sure port forwarding is enabled. Its PID goes to a file too, same reason as the
# refusal watcher: killable directly, no pkill pattern that could match something else.
PF_PID_FILE="${TMPDIR:-/tmp}/eb-port-forward.pid"
[ -f "$PF_PID_FILE" ] && kill "$(cat "$PF_PID_FILE")" 2>/dev/null; sleep 1
kubectl --kubeconfig "$KC" -n $NS port-forward svc/eventbridge 18080:8080 >/dev/null 2>&1 &
echo $! > "$PF_PID_FILE"
sleep 3; curl -sS -m 5 -o /dev/null -w 'bridge %{http_code}\n' "$EB/healthz"   
# expect 200
# stop it later with:  kill "$(cat "$PF_PID_FILE")"

# (3) live notification feed:
ntfy subscribe "$NTFY_TOPIC"








################
## DEMO PART ONE


# check that nothing is running

kubectl --kubeconfig "$KC" -n $NS get po

kubectl --kubeconfig "$KC" -n $NS get deploy eventrunner


# generate one event to run one agent one agent

claude -p "/eventbridge run 'Summarise the incident in one sentence.' --label incident"  --allowedTools 'Bash' --verbose

# check its output

CORR=$(python3 skills/eventbridge/eventbridge-cli.py latest incident)
echo "single-agent corr: $CORR"      

curl -sS "$EB/v0/agents/$CORR/turns" | python3 -m json.tool | head -20


### check back to zero

kubectl --kubeconfig "$KC" -n $NS get deploy eventrunner

kubectl --kubeconfig "$KC" -n $NS get po


################
## PART ONE with 100 agents


claude -p "/eventbridge run a batch of 100 agents, each saying 'Triage finding N. Reply OK.' label kubecon-100" --allowedTools 'Bash' --verbose


BATCH=$(curl -sS "$EB/v0/groups" | python3 -c 'import json,sys; g=json.load(sys.stdin)["groups"]; print(g[0]["groupid"] if g else "")')
echo "batch $BATCH → $EB/v0/groups/$BATCH"

# wathc esults of 100 agents

kubectl --kubeconfig "$KC" -n $NS get deploy eventrunner -w

watch kubectl --kubeconfig "$KC" -n $NS get po










#############
## PART TWO

# The audit. --highlight marks the labelled run from Part 1 and DROPS NOTHING, so the
# rest of the topic stays on screen. Highlighted chains now sort first, and a `focus`
# block after the summary restates the id with its verdicts and chain state.
python3 scripts/audit_topic.py --kubeconfig "$KC" --tag $TAG --registry $REG \
  --chains --verify --highlight "$CORR"
echo "audit exit: $?"            # 0 = the four assertions held

# The NARROW view, for a busy topic: --corr shows ONLY this run and prints a SCOPE line
# saying how many events it hid. Do NOT use it in Part 3 — it would hide the forgery.
# (--highlight alongside --corr is redundant: everything left is the id you asked for.)
python3 scripts/audit_topic.py --kubeconfig "$KC" --tag $TAG --registry $REG \
  --chains --corr "$CORR"



###############
# PART THREE

# scipt to wathch refusals

cd "$REPO"
export OUT="${TMPDIR:-/tmp}/refusals.log"
./scripts/watch-refusals.sh


# scritp to publish event that is unsigne dby running pod that injects it

CORR_FORGED="forged-$(date +%s)"
cd "$REPO"
./scripts/inject-unsigned.sh "$CORR_FORGED"

curl -sS -w '\ntranscript for the forged id: %{http_code}\n'  "$EB/v0/agents/$CORR_FORGED"

grep "$CORR_FORGED" "${TMPDIR:-/tmp}/refusals.log"

cd "$REPO"
python3 scripts/audit_topic.py --kubeconfig "$KC" --tag $TAG --registry $REG  --chains --verify --highlight "$CORR_FORGED"
