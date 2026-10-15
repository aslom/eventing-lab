"""Every package a service imports must be COPYed into its image. eventing-lab#2.

This file exists because of a crash loop found on the first real cluster run. The
eventbridge image could not start at all:

    File "/app/eventbridge/__main__.py", line 14, in <module>
      from eventbridge.handlers import Handlers
    File "/app/eventbridge/handlers.py", line 18, in <module>
      from eventrunner import agentspec
    ModuleNotFoundError: No module named 'eventrunner'

`eventbridge/handlers.py` grew a module-level `from eventrunner import agentspec` with
the declarative-agents work (examples#883, 2026-10-05) and `Dockerfile-eventbridge` was
never updated to COPY `eventrunner/`. Nothing caught it for over a week:

  * the test suite imports from the repo root, where every package is on sys.path;
  * a laptop run does the same;
  * preflight check 4 verifies the image is *pullable* and matches the node's
    architecture, not that it can execute;
  * the deployed `:dev` tag predated the import, so the OLD image kept working and
    the failure only appeared once the image was rebuilt from current source.

That last point is the trap: a green deploy against a stale image proves nothing about
the code in the repository.

These tests read the Dockerfiles and the sources statically. No build, no daemon, no
registry — a test that needed a container runtime would not run in CI here, and the
check is a pure dependency question anyway.

What this does NOT do: prove the image starts. Only running it does that. It proves
the narrower, cheaper property that every first-party package imported by a service's
own modules is present in that service's image.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent

# The first-party packages that could be imported. `tests/` and `scripts/` are
# deliberately absent: neither belongs in a runtime image.
FIRST_PARTY = ("shared", "eventbridge", "eventrunner")

# Which Dockerfile ships which service, and the package whose sources it runs.
SERVICE_IMAGES = (
    ("Dockerfile-eventbridge", "eventbridge"),
    ("Dockerfile-eventrunner", "eventrunner"),
)

_IMPORT = re.compile(
    r"^\s*(?:from\s+(?P<from>[a-zA-Z_][\w.]*)\s+import|import\s+(?P<plain>[a-zA-Z_][\w.]*))",
    re.MULTILINE)


def _copied_packages(dockerfile: pathlib.Path) -> set[str]:
    """First-party package names COPYed into the image."""
    out = set()
    for line in dockerfile.read_text().splitlines():
        s = line.strip()
        if not s.upper().startswith("COPY"):
            continue
        for pkg in FIRST_PARTY:
            # `COPY eventrunner/ ./eventrunner/` — match the source operand.
            if re.search(rf"\bCOPY\s+(?:--\S+\s+)*{pkg}/", s):
                out.add(pkg)
    return out


def _imported_packages(pkg: str) -> dict[str, list[str]]:
    """First-party packages imported by `pkg`, mapped to where. Excludes itself."""
    found: dict[str, list[str]] = {}
    for py in sorted((REPO / pkg).rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text()
        for m in _IMPORT.finditer(text):
            mod = (m.group("from") or m.group("plain") or "").split(".")[0]
            if mod in FIRST_PARTY and mod != pkg:
                line_no = text[:m.start()].count("\n") + 1
                found.setdefault(mod, []).append(
                    f"{pkg}/{py.relative_to(REPO / pkg)}:{line_no}")
    return found


def test_each_service_image_copies_every_package_it_imports():
    """The invariant the crash loop broke."""
    problems = []
    for dockerfile_name, pkg in SERVICE_IMAGES:
        dockerfile = REPO / dockerfile_name
        assert dockerfile.exists(), f"{dockerfile_name} is missing"
        copied = _copied_packages(dockerfile)
        for needed, sites in sorted(_imported_packages(pkg).items()):
            if needed not in copied:
                problems.append(
                    f"{dockerfile_name} runs {pkg}/ but does not COPY {needed}/, "
                    f"which it imports at {', '.join(sites[:4])}")
    assert not problems, (
        "these images cannot import their own code — add the COPY line:\n  "
        + "\n  ".join(problems))


def test_the_eventbridge_image_copies_eventrunner():
    """The specific regression, named so a failure points straight at the cause.
    `handlers.py` imports `eventrunner.agentspec` at module level, so a missing
    COPY is a crash loop and not a degraded feature."""
    copied = _copied_packages(REPO / "Dockerfile-eventbridge")
    assert "eventrunner" in copied, (
        "Dockerfile-eventbridge must COPY eventrunner/ — eventbridge/handlers.py "
        "imports eventrunner.agentspec at import time, so without it the image "
        "crash-loops with ModuleNotFoundError before serving anything")


def test_every_service_image_copies_shared():
    """`shared/` holds the wire contract; both services depend on it."""
    for dockerfile_name, _pkg in SERVICE_IMAGES:
        copied = _copied_packages(REPO / dockerfile_name)
        assert "shared" in copied, f"{dockerfile_name} must COPY shared/"


def test_the_import_scanner_actually_finds_the_known_cross_package_import():
    """Guards the test above from silently passing on a broken scanner: if this
    stops finding handlers.py's import, the checks become vacuous."""
    found = _imported_packages("eventbridge")
    assert "eventrunner" in found, (
        "the scanner no longer sees eventbridge -> eventrunner; either the import "
        "was removed (then drop the COPY and these tests) or the regex broke")
    assert any("handlers.py" in s for s in found["eventrunner"]), found["eventrunner"]


# ---- the committed-empty keyset vs the deploy that populates it -------------
#
# `k8s/base/keyset-configmap.yaml` is committed holding `{}` on purpose (a key in
# the repo is what that file exists to prevent) and it IS in base/kustomization.yaml.
# So `kubectl apply -k <overlay>` always resets the keyset — including the copy that
# `ensure_signing_material` wrote moments earlier.
#
# On the first real cluster run (eventing-lab#2) the deploy reported:
#
#     ConfigMap/eventing-keyset: 2 approved kid(s): eb-01, runner-01
#     PASS ConfigMap/eventing-keyset applied with 2 kid(s)
#     configmap/eventing-keyset configured         <- the overlay, overwriting it
#     ALL 40 CHECKS PASSED
#
# and left `{}` in the cluster. EventBridge then started "response verification ON
# (ENFORCING) — 0 kid(s)" and refused every response, so no run could ever complete.
# Re-running the deploy could not fix it; every apply repeated the overwrite.

def _deploy_src() -> str:
    return (REPO / "scripts" / "k8s_deploy.py").read_text()


def _call_lines(src: str, name: str) -> list[int]:
    """Line numbers where `name` is CALLED — not defined, not named in a comment.

    An earlier version of these tests searched the raw text and passed against
    code with the re-apply deleted, because a comment mentioning the function and
    its own `def` both matched. Checking call sites is the whole point here.
    """
    out = []
    for n, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(rf"(?<!def )\b{re.escape(name)}\s*\(", code):
            out.append(n)
    return out


def test_the_committed_keyset_is_empty():
    """The premise. If someone populates this file, the ordering problem below
    disappears but a public key has been committed — which is the thing the file's
    own header forbids."""
    import json as _json
    text = (REPO / "k8s" / "base" / "keyset-configmap.yaml").read_text()
    # Drop the `|` block indicator that follows the key, then join the block body.
    body = text.split("agents.json:", 1)[1].split("\n", 1)[1]
    payload = "".join(line.strip() for line in body.splitlines() if line.strip())
    assert _json.loads(payload) == {}, (
        "k8s/base/keyset-configmap.yaml must stay empty — see its header")


def test_the_keyset_is_applied_after_the_overlay_too():
    """The fix: `ensure_signing_material` must run AFTER `apply_kustomize`, not
    only before it. Before-only is what left the cluster with `{}`."""
    src = _deploy_src()
    applies = _call_lines(src, "apply_kustomize")
    ensures = _call_lines(src, "ensure_signing_material")
    assert applies, "no apply_kustomize call site found"
    assert ensures, "no ensure_signing_material call site found"
    assert any(e > max(applies) for e in ensures), (
        f"ensure_signing_material is only called at {ensures}, all before "
        f"apply_kustomize at {applies}. The overlay's committed `{{}}` then wins "
        f"and a signed overlay enforces with zero approved kids.")


def test_the_deploy_reads_the_keyset_back_from_the_cluster():
    """Every other check in k8s_deploy.py reports what was SENT. The overwrite was
    invisible to all of them because each apply really did succeed, so only a
    read-back can catch it."""
    src = _deploy_src()
    assert "def _verify_keyset_landed" in src, "_verify_keyset_landed is missing"
    assert _call_lines(src, "self._verify_keyset_landed"), (
        "_verify_keyset_landed is defined but never CALLED — k8s_deploy.py must "
        "read the keyset back from the cluster and assert it is non-empty after "
        "applying the overlay")
    fn = src.split("def _verify_keyset_landed", 1)[1][:1600]
    assert "get(" in fn and "agents.json" in fn, (
        "_verify_keyset_landed must read ConfigMap data from the cluster")


def test_the_deploy_proves_the_running_pod_enforces():
    """`_verify_keyset_landed` proves the ConfigMap; the pod must be proven too.

    The keyset is a volume mount read once at startup (live reload deliberately
    absent, eventbridge/__main__.py:65), so a pod that started while the
    overlay's `{}` was in place keeps an empty set until it restarts — and an
    unchanged overlay triggers no rollout, so a re-deploy would fix the ConfigMap
    and leave the process enforcing with nothing. The deploy must restart the
    deployment itself and read the pod's OWN startup line back.
    """
    src = _deploy_src()
    assert "def _verify_keyset_enforcing" in src, "_verify_keyset_enforcing is missing"
    assert _call_lines(src, "self._verify_keyset_enforcing"), (
        "_verify_keyset_enforcing is defined but never CALLED — the deploy must "
        "prove the RUNNING pod enforces with the keyset, not only that the "
        "ConfigMap holds it")
    assert re.search(r'\["rollout", "restart", "deploy/eventbridge"\]', src), (
        "a pod that started on the overlay's `{}` never re-reads the keyset, and "
        "an unchanged overlay triggers no rollout — the deploy must restart "
        "deploy/eventbridge itself")


# ---- the forged-RESPONSE beat must build its group through the API ----------
#
# `assert_forged_event_refused` needs a group that is OPEN (so a forged
# `group.completed` has something to settle) and already holds one GENUINE finished
# answer (so there is a real row the forgery fails to overwrite).
#
# An earlier revision asked for that in one call, `expected: 2` alongside a single
# prompt, which eventbridge/handlers.py:325 refuses as contradictory:
#
#     {"error": "expected=2 contradicts 1 prompts"}
#
# so the stage died on a 400 having tested nothing about signatures. That validation
# catches a real operator error and is not the thing to relax. The group is now built
# in two calls: `expected` with no prompts (allowed by handlers.py:322), then one
# member via POST /v0/agents with `groupid` (handlers.py:283).
#
# Two prompts with the forgery racing the second member was the other candidate and
# was rejected: in mock mode a turn is ~2s while the forgery must first start a pod,
# so the batch would reliably settle legitimately first and the stage would fail for
# a reason unrelated to signatures.

def _demo_flow_src() -> str:
    return (REPO / "scripts" / "k8s_demo_flow.py").read_text()


def _forged_response_stage() -> str:
    src = _demo_flow_src()
    start = src.index("def assert_forged_event_refused")
    return src[start:start + 4000]


def test_the_forgery_target_group_is_not_created_with_a_contradictory_payload():
    """`expected` and `prompts` together must agree, or handlers.py returns 400."""
    stage = _forged_response_stage()
    bad = re.search(r'"expected":\s*2[^}]*"prompts"|"prompts"[^}]*"expected":\s*2',
                    stage, re.DOTALL)
    assert not bad, (
        "the forgery-target group sends `expected` together with a differing number "
        "of prompts; handlers.py:325 rejects that as contradictory and the stage "
        "fails on a 400 before testing anything about signing")


def test_the_forgery_target_group_is_open_and_gains_one_real_member():
    """Both halves of the setup must be present: an `expected` group, and a member
    submitted separately with `groupid` so the batch is provably 1-of-2."""
    stage = _forged_response_stage()
    assert '"expected": 2' in stage, (
        "the forgery target must declare `expected` so the group cannot settle on "
        "its own while one member is outstanding")
    assert '"groupid": gid' in stage, (
        "one genuine member must be submitted via POST /v0/agents with `groupid`; "
        "without a real finished answer three of this beat\'s assertions "
        "(finished == 1, forged text absent, rejection row) check nothing")
