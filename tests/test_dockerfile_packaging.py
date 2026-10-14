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
