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
