#!/usr/bin/env bash
# Follow the eventrunner's refusal lines ACROSS scale-from-zero.
#
# Part 3a of the demo. `kubectl logs deploy/eventrunner -f` cannot do this: at zero
# replicas it fails immediately with "error: timed out waiting for the condition"
# rather than waiting for a pod. The refusing pod then lives ~5s, so by the time you
# notice the watch died, the evidence is gone with it.
#
# This polls for a new eventrunner pod, tails it, and keeps going when it dies.
#
# It NARRATES while it waits. The old version printed two lines and then sat silent,
# which is the worst possible behaviour for a demo pane: a watcher waiting correctly and
# a watcher that has died look identical, and the operator finds out only when the beat
# produces no evidence. So it shows a heartbeat with the replica count, announces each
# pod as it appears and goes, and says plainly when a refusal lands.
#
# Usage:  OUT=... ./watch-refusals.sh      # log path (default $TMPDIR/refusals.log)
#         QUIET=1 ./watch-refusals.sh      # no heartbeat, only real events
#         NO_COLOR=1 ./watch-refusals.sh   # plain output for a recording
set -uo pipefail

KC="${KC:?export KC=/path/to/kubeconfig-ykt1 first}"
NS="${NS:-kev1}"
OUT="${OUT:-${TMPDIR:-/tmp}/refusals.log}"
PIDFILE="${PIDFILE:-${OUT}.pid}"
SEL=app.kubernetes.io/name=eventrunner
HEARTBEAT="${HEARTBEAT:-5}"          # seconds between idle status lines

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[0m'
else
  B=""; D=""; G=""; Y=""; R=""
fi

# Write our own PID so the watcher can be killed DIRECTLY:
#
#     kill -- -"$(cat "$PIDFILE")"
#
# rather than hunted for with pgrep/pkill. That matters here: `pkill -f watch-refusals`
# needs permission to enumerate every process on the box, which is not always granted,
# and a pattern match can hit the wrong thing — including the demo operator's own
# editor holding the file open. A PID file is exact and needs no process listing.
#
# `kill` the whole PROCESS GROUP, not just this shell: the loop below spawns
# `kubectl logs -f | grep | tee`, and killing only the parent leaves those children
# tailing a pod and appending to $OUT. The group id equals this PID when the script is
# started as a background job, so `kill -- -<pid>` reaches all of it.
echo $$ > "$PIDFILE"
# Clean up on any exit, so a stale PID file never points at a process that is gone (or,
# worse, at a PID the OS has since reused for something else).
#
# The count is recomputed from $OUT rather than read from $HITS: the tail loop runs in
# this shell, but `kill -- -$PID` takes the whole group down at once and the trap may
# not observe the latest increment. The file is the durable record, so counting it is
# both simpler and more honest than trusting a variable the signal raced.
_bye() {
  n=$(grep -c 'corr=' "$OUT" 2>/dev/null || echo 0)
  printf '\n%s[watch] stopped after %ds - %s refusal(s) captured%s\n' \
    "$B" "$(( $(date +%s) - T0 ))" "$n" "$R"
  printf '        %sfull log: %s%s\n' "$D" "$OUT" "$R"
  rm -f "$PIDFILE"
}
trap _bye EXIT INT TERM

T0=$(date +%s)
HITS=0

printf '\n%s=== Part 3a: watching for the runner to refuse something ===%s\n' "$B" "$R"
printf '      %scluster:   %s%s\n' "$D" "$(kubectl --kubeconfig "$KC" config current-context 2>/dev/null || echo '?')" "$R"
printf '      %snamespace: %s   selector: %s%s\n' "$D" "$NS" "$SEL" "$R"
printf '      %slog:       %s%s\n' "$D" "$OUT" "$R"
printf '      %spid %s -> %s%s\n' "$D" "$$" "$PIDFILE" "$R"
printf '      %sstop with:  kill -- -$(cat "%s")%s\n' "$D" "$PIDFILE" "$R"

# Say up front whether the thing being watched is even enforcing. A watcher that can
# never see a refusal should say so now, not by staying quiet for the whole beat.
ENF=$(kubectl --kubeconfig "$KC" -n "$NS" get cm eventing-config \
        -o jsonpath='{.data.ER_REQUIRE_SIGNATURE}' 2>/dev/null || echo '?')
if [ "$ENF" = "true" ]; then
  printf '      %sER_REQUIRE_SIGNATURE=true - unsigned requests WILL be refused%s\n' "$G" "$R"
else
  printf '      %sER_REQUIRE_SIGNATURE=%s - NOT enforcing. An unsigned request would be%s\n' "$Y" "${ENF:-unset}" "$R"
  printf '      %sACCEPTED and run, so no refusal will ever appear here.%s\n' "$Y" "$R"
fi

printf '\n%s[watch] armed. Now run ./inject-unsigned.sh in the other pane.%s\n' "$B" "$R"
printf '%s        Watching for: reject | unsigned | refus%s\n\n' "$D" "$R"

: > "$OUT"
seen=""
last_beat=0
while true; do
  now=$(date +%s)
  pod=$(kubectl --kubeconfig "$KC" -n "$NS" get pods -l "$SEL" \
          -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

  if [ -n "$pod" ] && [ "$pod" != "$seen" ]; then
    seen="$pod"
    printf '%s[watch +%ds] POD UP: %s%s\n' "$G" "$((now - T0))" "$pod" "$R"
    printf '        %sKEDA scaled 0 -> 1 on topic lag. Tailing its whole log...%s\n' "$D" "$R"
    echo "--- pod $pod (t+$((now - T0))s) ---" >> "$OUT"

    # --tail=-1 so nothing already logged is missed; ignore the error when the pod
    # vanishes mid-tail, which is the normal case here. Each matching line is announced
    # as it arrives AND appended to $OUT, so the pane shows it even though the pod it
    # came from is about to disappear.
    while IFS= read -r line; do
      # Two different kinds of line match the grep, and calling both "REFUSAL" would be
      # a lie told loudly. The runner prints a STARTUP BANNER describing its policy
      # ("submitter allowlist ON - ... is refused, offset committed, agent not run"),
      # which contains every keyword but is not an event being refused. Only a line
      # naming a correlation id is an actual refusal of an actual request.
      case "$line" in
        *corr=*)
          HITS=$((HITS + 1))
          printf '\n%s>>> REFUSAL #%d (t+%ds) - an event was REFUSED%s\n' \
            "$B" "$HITS" "$(( $(date +%s) - T0 ))" "$R"
          printf '%s%s%s\n\n' "$G" "$line" "$R"
          printf '%s\n' "$line" >> "$OUT"
          ;;
        *)
          printf '        %s[policy] %s%s\n' "$D" "$line" "$R"
          printf '[policy] %s\n' "$line" >> "$OUT"
          ;;
      esac
    done < <(kubectl --kubeconfig "$KC" -n "$NS" logs "$pod" --tail=-1 -f 2>/dev/null \
               | grep --line-buffered -iE 'reject|unsigned|refus')

    printf '%s[watch +%ds] pod %s is gone%s\n' "$D" "$(( $(date +%s) - T0 ))" "$pod" "$R"
    printf '        %sdrained and scaled back to zero. Still watching.%s\n' "$D" "$R"
    last_beat=$(date +%s)
    continue
  fi

  # Heartbeat. Without it a correct wait is indistinguishable from a dead script, and
  # the replica count is the one number that says which of the two it is.
  if [ -z "${QUIET:-}" ] && [ $((now - last_beat)) -ge "$HEARTBEAT" ]; then
    rep=$(kubectl --kubeconfig "$KC" -n "$NS" get deploy eventrunner \
            -o jsonpath='{.status.replicas}' 2>/dev/null || echo '?')
    printf '%s[watch +%ds] idle - eventrunner replicas=%s, %d refusal(s) so far%s\n' \
      "$D" "$((now - T0))" "${rep:-0}" "$HITS" "$R"
    last_beat=$now
  fi
  sleep 1
done
