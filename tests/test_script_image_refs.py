"""Every throwaway-pod image ref must be a VALID reference. eventing-lab#2.

This file exists because of a real failure, found only when the demo was finally run
against a cluster: `audit_topic.py` and both forgery stages in `k8s_demo_flow.py` built
their `--image` argument with an f-string,

    f"{args.registry}/rossoctl-eventrunner:{args.tag}"

while `imagelib.DEFAULT_REGISTRY` is `"quay.io/aslomnet/"` — already slash-terminated.
The result was `quay.io/aslomnet//rossoctl-eventrunner:dev`, which podman rejects
outright:

    error: Invalid image name "quay.io/aslomnet//rossoctl-eventrunner:dev":
    invalid reference format

`imagelib.normalize_prefix` exists precisely to prevent this, and `Images` is the one
notion of "what ref are we talking about" that imagelib's own docstring says the build,
the preflight and the deploy must share. The deploy path went through `Images` and was
correct; these three sites bypassed it and were not.

Why it went unnoticed: the broken sites are all *in-cluster pod* launches, so no unit
test and no amount of `--dry-run` reaches them — only a real cluster does. The
assertions here are therefore deliberately static (they read the source), because a
test that needed a cluster to catch a cluster-only bug would not have caught it either.

What this does not do: check that the image exists or is pullable. That is preflight
check 4's job, and it hard-fails by design.
"""
from __future__ import annotations

import pathlib
import re

# `scripts/` is on sys.path via tests/conftest.py — it is not a package.
from imagelib import DEFAULT_REGISTRY, DEFAULT_TAG, Images, normalize_prefix

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

# A registry reference may not contain an empty path component. Anchored on the
# "//" that the bug produced, but excluding a scheme's "://" so a URL in a comment
# or a docstring is not a false positive.
_DOUBLE_SLASH = re.compile(r"(?<!:)//")


def _image_ref_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    """Lines that build an eventrunner/eventbridge image reference."""
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if "rossoctl-eventrunner:" in line or "rossoctl-eventbridge:" in line:
            out.append((n, line))
    return out


# ---- the invariant the bug broke -------------------------------------------

def test_the_default_registry_prefix_is_slash_terminated():
    """The premise of the whole bug. If this ever changes, the f-string sites
    would have been correct and this file's reasoning needs revisiting."""
    assert DEFAULT_REGISTRY.endswith("/"), (
        "DEFAULT_REGISTRY is expected to carry its trailing slash; `Images` and "
        "`normalize_prefix` are built around that")


def test_Images_does_not_double_the_slash():
    imgs = Images(DEFAULT_REGISTRY, DEFAULT_TAG)
    for ref in (imgs.eventrunner, imgs.eventbridge):
        assert not _DOUBLE_SLASH.search(ref), f"{ref} has an empty path component"
    assert imgs.eventrunner == "quay.io/aslomnet/rossoctl-eventrunner:dev"


def test_Images_is_correct_whether_or_not_the_prefix_has_a_slash():
    """The property that makes `Images` the right fix rather than stripping a
    slash at each call site: both spellings of the prefix yield the same ref."""
    with_slash = Images("quay.io/aslomnet/", "dev").eventrunner
    without = Images("quay.io/aslomnet", "dev").eventrunner
    assert with_slash == without == "quay.io/aslomnet/rossoctl-eventrunner:dev"


def test_normalize_prefix_is_idempotent():
    assert normalize_prefix(normalize_prefix("r.io/x")) == normalize_prefix("r.io/x")


# ---- the regression: no call site may rebuild a ref by hand ----------------

def test_no_script_interpolates_a_slash_before_the_image_name():
    """The exact shape of the bug: `{registry}/rossoctl-…`.

    A slash written between the registry expression and the image name doubles
    whatever `normalize_prefix` would have supplied. Scripts must go through
    `Images`, which is the only place that decides this.
    """
    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        for n, line in _image_ref_lines(path):
            if re.search(r"(registry|REGISTRY|prefix)[^\n]*?\}/rossoctl-", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, (
        "these lines interpolate a slash before the image name, which doubles the "
        "one already in DEFAULT_REGISTRY — use `Images(registry, tag).eventrunner` "
        "instead:\n  " + "\n  ".join(offenders))


def test_the_pod_launching_scripts_use_Images():
    """Positive counterpart: the three sites that launch a throwaway pod must
    actually reference `Images`, so the test above cannot be satisfied by
    deleting the code it guards."""
    for name in ("audit_topic.py", "k8s_demo_flow.py"):
        src = (SCRIPTS / name).read_text()
        assert "Images" in src, f"{name} should build refs through imagelib.Images"
        assert re.search(r"Images\([^)]*\)\.eventrunner", src), (
            f"{name} should launch its pod on Images(...).eventrunner")


def test_every_image_ref_line_in_scripts_is_a_valid_reference():
    """Belt and braces across all of `scripts/`: no built ref may contain an
    empty path component, however it was constructed."""
    bad = []
    for path in sorted(SCRIPTS.glob("*.py")):
        for n, line in _image_ref_lines(path):
            code = line.split("#", 1)[0]
            if _DOUBLE_SLASH.search(code):
                bad.append(f"{path.name}:{n}: {line.strip()}")
    assert not bad, "invalid image references:\n  " + "\n  ".join(bad)


# ---- the in-pod import contract --------------------------------------------
#
# `audit_topic.py` re-executes ITSELF inside a pod on the eventrunner image, which
# carries `shared/` and `eventrunner/` but NOT `scripts/` (Dockerfile-eventrunner
# COPYs exactly those two). So any scripts/-local module it imports at module level
# is unavailable there, and the pod dies with ModuleNotFoundError before argparse
# runs. That is a cluster-only failure: it cannot happen on the host, where
# `scripts/` is on sys.path.
#
# This was found by the first real run (eventing-lab#2) and had been latent since
# the in-cluster path was written — the earlier double-slash bug aborted the pod
# before the import was ever reached, hiding it.

def test_audit_topic_imports_with_no_scripts_dir_on_the_path():
    """The pod's conditions: /app (shared/) plus /tmp (this file + proclib.py),
    and no scripts/ anywhere. A module-level `import imagelib` fails here."""
    import subprocess
    import sys
    import tempfile

    repo = SCRIPTS.parent
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        # Exactly what audit_in_cluster ships into /tmp.
        (tmpdir / "audit_topic.py").write_text((SCRIPTS / "audit_topic.py").read_text())
        (tmpdir / "proclib.py").write_text((SCRIPTS / "proclib.py").read_text())
        # Drop scripts/ from sys.path while KEEPING stdlib — assigning the whole
        # list would also remove stdlib and the failure would be meaningless.
        # repo root stands in for /app (it holds shared/).
        prog = (
            "import sys;"
            f"sys.path[:] = [p for p in sys.path if 'scripts' not in p];"
            f"sys.path.insert(0, {str(repo)!r});"
            f"sys.path.insert(0, {str(tmpdir)!r});"
            "import importlib.util as u;"
            f"spec = u.spec_from_file_location('audit_topic', {str(tmpdir / 'audit_topic.py')!r});"
            "m = u.module_from_spec(spec); spec.loader.exec_module(m);"
            "print('IMPORT-OK')"
        )
        r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                           text=True, timeout=120, cwd=tmp)
    assert "IMPORT-OK" in r.stdout, (
        "audit_topic.py cannot be imported under the pod's sys.path — the "
        "in-cluster audit will die before it runs.\n"
        f"stdout: {r.stdout}\nstderr: {r.stderr[-2000:]}")


def test_audit_topic_does_not_import_host_only_modules_at_module_level():
    """imagelib and k8slib are host-only; importing either at module level
    reintroduces the pod failure above."""
    src = (SCRIPTS / "audit_topic.py").read_text()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and not line[:1].isspace():
            assert "imagelib" not in stripped and "k8slib" not in stripped, (
                f"module-level host-only import: {stripped!r} — import it inside "
                f"audit_in_cluster() instead")


def _shipped_into_the_pod() -> dict[str, str]:
    """The files `audit_in_cluster` writes into the pod, decoded from its own argv.

    Built by calling the real code path rather than by re-deriving the list here: a
    test that keeps its own copy of what SHOULD be shipped passes when both copies
    drift together, which is precisely the bug class this file exists for.
    """
    import base64
    import json
    import zlib

    import audit_topic

    class _FakeRes:
        ok, out, err = True, "", ""

    captured: dict[str, object] = {}

    class _FakeK:
        def get(self, *a, **kw):
            # EB_VERIFY_KEYSET_PATH is read with a bare dig(), which RAISES for an
            # absent path by design (k8slib's whole premise), so the fake ConfigMap
            # must carry it. Empty string = "no keyset", the listing-only branch.
            return {"data": {"KAFKA_BOOTSTRAP": "b:9092",
                             "REQUEST_TOPIC": "t-req",
                             "RESPONSE_TOPIC": "t-res",
                             "EB_VERIFY_KEYSET_PATH": ""}}

        def call(self, args, **kw):
            captured["argv"] = args
            return _FakeRes()

    class _Args:
        context = kubeconfig = None
        namespace = "kev1"
        bootstrap = "b:9092"
        request_topic, response_topic = "t-req", "t-res"
        bridge_kid = ""
        chains = verify = json_ = no_color = False
        corr = highlight = label = highlight_label = None
        limit, pod_timeout = 2000, 240.0
        registry, tag = DEFAULT_REGISTRY, DEFAULT_TAG

    # `json` shadows the stdlib name inside the class body, so it is set after.
    _Args.json = False
    import k8slib
    import proclib
    orig_kubectl = k8slib.Kubectl
    k8slib.Kubectl = lambda **kw: _FakeK()
    try:
        audit_topic.audit_in_cluster(_Args(), proclib.Checks(prefix="t"))
    finally:
        k8slib.Kubectl = orig_kubectl

    argv = captured["argv"]
    prog = next(a for a in argv if "zlib.decompress" in str(a))
    blob = prog.split("base64.b64decode('")[1].split("')")[0]
    return json.loads(zlib.decompress(base64.b64decode(blob)))


def test_audit_topic_ships_proclib_into_the_pod():
    """`proclib` IS needed in the pod (audit_locally uses Checks/die), so it must
    travel with the script rather than be imported from scripts/."""
    assert "proclib.py" in _shipped_into_the_pod()


def test_audit_topic_ships_its_own_shared_package_into_the_pod():
    """`shared/` travels too, and this is the regression that forced it.

    The pod used to import `shared` from `/app` — the eventrunner image. A current
    `audit_topic.py` therefore met whatever `shared/` that image was built with, and
    `--label` died in the cluster on:

        AttributeError: 'Row' object has no attribute 'label'

    `audit.Row.label` is ADR-0003; the deployed image predated it. `imagePullPolicy:
    Always` cannot fix this — the image is current in the registry and stale in its
    content — and pinning an audit to the image lifecycle of the thing it audits is
    the wrong coupling regardless.

    So every module the audit imports is shipped: `shared.audit` needs `ce` and
    `signing`, `signing` needs `ed25519_native` and `ce`, and `audit_locally` loads
    `keyset` directly. A missing one is a pod-only ModuleNotFoundError.
    """
    shipped = _shipped_into_the_pod()
    for mod in ("__init__", "ce", "keyset", "signing", "ed25519_native", "audit"):
        assert f"shared/{mod}.py" in shipped, (
            f"shared/{mod}.py must be shipped into the pod, not imported from /app")
    assert "label" in shipped["shared/audit.py"], (
        "the shipped shared/audit.py is not the one with Row.label — the host copy "
        "should travel verbatim")


def test_the_shipped_shared_shadows_the_images_copy():
    """The shipped dir must come BEFORE /app on sys.path, or the image wins anyway.

    Shipping the right source and then letting `/app/shared` resolve first would
    reproduce the exact failure above while looking fixed.
    """
    import audit_topic
    src = (SCRIPTS / "audit_topic.py").read_text()
    del audit_topic
    i_app = src.index("sys.path.insert(0,'/app')")
    i_ship = src.index("sys.path.insert(0,str(d))")
    assert i_app < i_ship, (
        "/app must be inserted BEFORE the shipped dir, so that the shipped dir ends "
        "up at sys.path[0] and shadows the image's shared/")


def test_audit_topic_keeps_the_real_registry_defaults_on_the_host():
    """Removing the module-level import must not change what the host sees.
    Caught by ruff as F821 once, so it is pinned here too."""
    import audit_topic
    args = audit_topic.build_args([])
    assert args.registry == DEFAULT_REGISTRY
    assert args.tag == DEFAULT_TAG


# ---- the shell helpers ------------------------------------------------------
#
# `inject-unsigned.sh` and `watch-refusals.sh` were developed OUTSIDE the repo and
# ran only by hand, so they had neither a syntax gate nor the image-ref check that
# this file applies to every .py in scripts/. The injector in particular had a
# hardcoded `--image` that was wrong in two ways at once: it pinned one cluster's
# internal registry, and it named `rossoctl-eventrunner` while the deployment runs
# `rossoctl-eventrunner-claude`, so the pod meant to produce a byte-identical
# forgery was built from a different image than the runner it impersonates.

def _shell_scripts() -> list[pathlib.Path]:
    return sorted(SCRIPTS.glob("*.sh"))


def test_every_shell_script_parses():
    """`bash -n` on each. These are performed live; a typo is found on stage."""
    import subprocess
    for path in _shell_scripts():
        r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert r.returncode == 0, f"{path.name} is not valid bash:\n{r.stderr}"


def test_shell_scripts_carry_no_absolute_home_paths():
    """A path under /Users or /home is one operator's machine, unusable by anyone
    else and silently wrong after a move. `demo-commands.sh` carried seven."""
    bad = []
    for path in _shell_scripts():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "/Users/" in code or "/home/" in code:
                bad.append(f"{path.name}:{n}: {line.strip()}")
    assert not bad, ("absolute home paths in a committed script:\n  "
                     + "\n  ".join(bad))


def test_the_injector_does_not_hardcode_an_image_reference():
    """The forgery must be built from the image the runner is ACTUALLY running,
    read from the Deployment — otherwise 'byte-identical in shape to a genuine
    wake' is false, because the two sides have different `shared.ce`."""
    src = (SCRIPTS / "inject-unsigned.sh").read_text()
    assert "jsonpath='{.spec.template.spec.containers[0].image}'" in src, (
        "inject-unsigned.sh must read the image from deploy/eventrunner")
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        if code.strip().startswith("IMAGE="):
            assert "rossoctl-eventrunner" not in code, (
                f"hardcoded image ref: {code.strip()!r} — read it from the cluster")


def test_the_injector_refuses_to_run_when_enforcement_is_off():
    """Without ER_REQUIRE_SIGNATURE=true the forgery is ACCEPTED and the agent runs,
    so the beat would narrate a control that is switched off. It must abort."""
    src = (SCRIPTS / "inject-unsigned.sh").read_text()
    assert "ER_REQUIRE_SIGNATURE" in src
    assert "exit 1" in src, "the injector must exit non-zero when not enforcing"


def test_the_injector_asserts_the_event_is_actually_unsigned():
    """A bug that accidentally signed it would make the beat narrate a refusal that
    never happened. The absence is the whole point, so it is asserted."""
    src = (SCRIPTS / "inject-unsigned.sh").read_text()
    assert 'assert "signature" not in req.attrs' in src
