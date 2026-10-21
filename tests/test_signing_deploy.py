"""Key generation and the deploy-time key material. §11.

Signing was wired in examples#879 but could not be switched on in a cluster: nothing generated
keys and no Deployment mounted any. These cover the two pieces that close that, and the
properties worth pinning are mostly about what must NOT happen — a seed must not be
printed, must not be world-readable, must not be shared between the two services, and a
half-configured key directory must not reach a cluster that is enforcing.
"""
from __future__ import annotations

import json
import pathlib
import stat
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import gen_signing_keys as G  # noqa: E402
import k8s_deploy as D  # noqa: E402
from k8s_deploy import ensure_signing_material  # noqa: E402
from k8slib import Kubectl  # noqa: E402
from proclib import Checks, Result  # noqa: E402

from shared import ce, keyset  # noqa: E402
from shared import signing as S  # noqa: E402

# ---- generation -------------------------------------------------------------

def test_generate_makes_one_bridge_key_and_the_asked_for_runners():
    keys = G.generate(3)
    assert sorted(keys) == ["eb-01", "runner-01", "runner-02", "runner-03"]
    assert all(len(s) == 32 for s in keys.values()), "an Ed25519 seed is 32 bytes"
    assert len(set(keys.values())) == 4, "every seed must be distinct"


def test_the_bridge_and_runner_never_share_a_seed():
    """The property the whole design rests on. One shared seed would mean EventBridge
    holds the key it verifies runner responses with, so it could forge any agent's
    answer — which is exactly why HMAC was rejected (docs/specs/0003-phase2-identity-on-the-event-path.md §4.3)."""
    keys = G.generate(1)
    assert keys["eb-01"] != keys["runner-01"]


def test_the_keyset_holds_public_keys_derived_from_the_seeds(tmp_path):
    """Derived, never typed: a keyset that disagrees with the keys it authorizes shows
    up as "signing is broken" with nothing pointing at the key set."""
    keys = G.generate(2)
    ks = json.loads(G.keyset_json(keys))
    assert sorted(ks) == sorted(keys)
    for kid, seed in keys.items():
        assert ks[kid] == S.public_key(seed).hex()
        assert ks[kid] != seed.hex(), "a seed must never land in the keyset"


def test_the_generated_keyset_loads_and_authorizes_its_own_signatures(tmp_path):
    """End to end through the real verification path, not just shape checks."""
    keys = G.generate(1)
    G.write_seeds(tmp_path, keys)
    (tmp_path / "agents.json").write_text(G.keyset_json(keys))
    ks = keyset.load(str(tmp_path / "agents.json"))

    seed = S.load_seed(str(tmp_path / "runner-01" / "seed.hex"))
    assert seed == keys["runner-01"], "load_seed must read back what was written"
    evt = ce.new_event(type=ce.TYPE_RESPONSE, source="rossoctl://er/test",
                       datacontenttype="application/json", correlationid="c",
                       sessionuuid="s", sequence=1, phase="result", final="true",
                       data={"text": "hi"})
    S.sign_into(evt, seed, "runner-01")
    back = ce.from_kafka_binary(*ce.to_kafka_binary(evt))
    ok, why = S.verify_with_keyset(back, ks)
    assert ok, why


def test_seeds_are_written_unreadable_by_anyone_else(tmp_path):
    """A seed written world-readable is the control gone, and it is invisible from the
    outside — nothing fails, the key is just no longer private."""
    keys = G.generate(1)
    paths = G.write_seeds(tmp_path, keys)
    assert (stat.S_IMODE(tmp_path.stat().st_mode) & 0o077) == 0, \
        "the key directory must not be group- or world-accessible"
    for p in paths:
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == 0o600, f"{p} is {oct(mode)}, must be 0600"


def test_regenerating_refuses_to_clobber_a_deployed_key(tmp_path):
    """An existing seed may already be deployed and in an approved keyset. Silently
    replacing it revokes a working identity with no indication why."""
    keys = G.generate(1)
    G.write_seeds(tmp_path, keys)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        G.write_seeds(tmp_path, G.generate(1))
    # --force is the deliberate path.
    rotated = G.generate(1)
    G.write_seeds(tmp_path, rotated, force=True)
    assert S.load_seed(str(tmp_path / "eb-01" / "seed.hex")) == rotated["eb-01"]


def test_the_cli_never_prints_a_seed(tmp_path, capsys):
    """stdout and stderr both. A terminal transcript pasted into an issue is the most
    likely way a demo key escapes."""
    rc = G.main(["--out", str(tmp_path), "--runners", "2"])
    assert rc == 0
    out = capsys.readouterr()
    printed = out.out + out.err
    for kid in ("eb-01", "runner-01", "runner-02"):
        seed_hex = S.load_seed(str(tmp_path / kid / "seed.hex")).hex()
        assert seed_hex not in printed, f"{kid}'s seed was printed"
        assert S.public_key(bytes.fromhex(seed_hex)).hex() in printed, \
            f"{kid}'s public key should be shown"


def test_print_keyset_alone_writes_nothing(tmp_path, capsys):
    rc = G.main(["--print-keyset"])
    assert rc == 0
    ks = json.loads(capsys.readouterr().out)
    assert sorted(ks) == ["eb-01", "runner-01"]
    assert list(tmp_path.iterdir()) == [], "no files should be written"


# ---- deploy-time application ------------------------------------------------

class _FakeKubectl:
    """Records what would be applied. `get` returns None — nothing pre-exists."""

    def __init__(self):
        self.applied: list[dict] = []

    def get(self, kind, name, namespace=None, missing_ok=False):
        return None

    def apply_stdin(self, body, namespace=None):
        self.applied.append(json.loads(body))
        return Result(argv=["kubectl", "apply"], rc=0, out="configured", err="",
                      duration_s=0.01, launched=True, timed_out=False)


def _keydir(tmp_path, *, runners=1):
    keys = G.generate(runners)
    G.write_seeds(tmp_path, keys)
    (tmp_path / "agents.json").write_text(G.keyset_json(keys))
    return keys


def _apply(keydir, **kw):
    k, c = _FakeKubectl(), Checks(prefix="test")
    ensure_signing_material(k, c, "kev1", keydir, dry_run=kw.get("dry_run", False))
    return k, c


def test_a_valid_key_directory_applies_two_secrets_and_one_configmap(tmp_path):
    keys = _keydir(tmp_path)
    k, c = _apply(str(tmp_path))
    assert c.failed == 0
    names = [(m["kind"], m["metadata"]["name"]) for m in k.applied]
    assert names == [("Secret", "eventbridge-signing-key"),
                     ("Secret", "eventrunner-signing-key"),
                     ("ConfigMap", "eventing-keyset")]
    secrets = [m for m in k.applied if m["kind"] == "Secret"]
    # The data key is the file name the mount and *_SIGNING_KEY_PATH both assume.
    assert all(list(m["stringData"]) == ["seed.hex"] for m in secrets)
    assert (secrets[0]["stringData"]["seed.hex"]
            != secrets[1]["stringData"]["seed.hex"]), \
        "the two services must be given different seeds"
    assert secrets[0]["stringData"]["seed.hex"].strip() == keys["eb-01"].hex()


def test_the_applied_configmap_holds_only_public_keys(tmp_path):
    keys = _keydir(tmp_path)
    k, _ = _apply(str(tmp_path))
    cm = [m for m in k.applied if m["kind"] == "ConfigMap"][0]
    ks = json.loads(cm["data"]["agents.json"])
    assert sorted(ks) == sorted(keys)
    for kid, seed in keys.items():
        assert ks[kid] == S.public_key(seed).hex()
        assert seed.hex() not in json.dumps(cm), "a seed leaked into the ConfigMap"


def test_without_a_key_directory_nothing_is_applied(tmp_path):
    """A redeploy must not silently switch enforcement off and strand a signed topic."""
    k, c = _apply(None)
    assert c.failed == 0 and k.applied == []


def test_a_missing_key_directory_fails_with_the_command_to_run(tmp_path, capsys):
    """`Checks.failures` keeps only the description; the actionable detail is printed.
    Assert on the output, since that is what the operator actually sees."""
    k, c = _apply(str(tmp_path / "absent"))
    assert c.failed == 1 and k.applied == []
    assert "gen_signing_keys.py" in capsys.readouterr().out, \
        "the failure should name the command that fixes it"


def test_an_empty_keyset_is_refused(tmp_path):
    """`{}` is a valid keyset that approves nobody — deployed under enforcement it
    rejects every event, which reads as a broken system rather than a config error."""
    (tmp_path / "agents.json").write_text("{}")
    k, c = _apply(str(tmp_path))
    assert c.failed == 1 and k.applied == []


def test_a_malformed_keyset_is_refused_before_it_reaches_the_cluster(tmp_path):
    (tmp_path / "agents.json").write_text('{"runner-01": "not-a-key"}')
    k, c = _apply(str(tmp_path))
    assert c.failed == 1 and k.applied == []


def test_a_kid_missing_from_the_approved_set_is_refused(tmp_path):
    """The service would start, sign under that kid, and have every event refused by
    the other side — a failure with no local symptom."""
    _keydir(tmp_path)
    (tmp_path / "agents.json").write_text(json.dumps({"someone-else": "ab" * 32}))
    k, c = _apply(str(tmp_path))
    assert c.failed == 1 and k.applied == []


def test_a_missing_seed_file_is_refused(tmp_path):
    keys = _keydir(tmp_path)
    (tmp_path / "runner-01" / "seed.hex").unlink()
    k, c = _apply(str(tmp_path))
    assert c.failed == 1
    # The bridge's Secret may already have been applied; the runner's must not be.
    assert not any(m["metadata"]["name"] == "eventrunner-signing-key"
                   for m in k.applied)
    assert keys  # keys were generated; the point is the deploy stopped


def test_dry_run_applies_nothing(tmp_path):
    _keydir(tmp_path)
    k, c = _apply(str(tmp_path), dry_run=True)
    assert c.failed == 0 and k.applied == []


# ---- the seed must DERIVE the approved public key ------------------------
#
# Review finding on examples#884: every other check passes on a pair that does not agree, which
# is the one divergence producing the cluster this validation exists to prevent — pods
# start (both volumes are `optional: true`), enforcement is on, the service signs with a
# key nobody approved, and every event is refused with nothing local to look at.


@pytest.mark.parametrize("kid", ["eb-01", "runner-01"])
def test_a_seed_that_does_not_match_the_approved_key_is_refused(tmp_path, kid):
    """The likeliest real case is not hand-editing: an `agents.json` copied from a
    teammate or a previous cluster while the seeds on disk are local."""
    _keydir(tmp_path)
    # Replace one seed, leaving agents.json alone — so the kid is still in the keyset
    # and the file still exists, which is all the other checks look at.
    other = G.generate(1)["eb-01"]
    (tmp_path / kid / "seed.hex").write_text(other.hex() + "\n")

    k, c = _apply(str(tmp_path))
    assert c.failed == 1, "a mismatched seed/keyset pair was accepted"
    assert not any(m["kind"] == "ConfigMap" for m in k.applied), \
        "the keyset was applied despite a mismatched pair"


def test_the_mismatch_refusal_names_both_fingerprints(tmp_path, capsys):
    """An operator has to be able to tell which half is wrong, so the message carries a
    prefix of each key and names the two remedies."""
    _keydir(tmp_path)
    (tmp_path / "eb-01" / "seed.hex").write_text(G.generate(1)["eb-01"].hex() + "\n")
    _apply(str(tmp_path))
    out = capsys.readouterr().out
    assert "derives" in out and "approves" in out
    assert "gen_signing_keys.py" in out, "the message should name the fix"


def test_a_corrupt_seed_file_is_refused_rather_than_crashing(tmp_path):
    """`load_seed` raises on a non-hex or wrong-length file; that must become a named
    check failure, not a traceback out of the deploy script."""
    _keydir(tmp_path)
    (tmp_path / "eb-01" / "seed.hex").write_text("not-a-seed\n")
    k, c = _apply(str(tmp_path))
    assert c.failed == 1 and k.applied == []


def test_a_matching_directory_is_still_accepted(tmp_path):
    """The gate must not reject what it should allow — the check is an equality, and an
    equality test that always fails is as useless as one that never does."""
    _keydir(tmp_path, runners=2)
    k, c = _apply(str(tmp_path))
    assert c.failed == 0
    assert len(k.applied) == 3  # two Secrets + the keyset ConfigMap


# ---- write_seeds is all-or-nothing --------------------------------------


def test_a_partial_rotation_writes_nothing_at_all(tmp_path):
    """Review finding on examples#884: `write_seeds` wrote kid-by-kid and raised mid-loop, so if
    the FIRST kid in sorted order was missing and a later one present, the first got a
    brand-new seed while `agents.json` — rewritten by the caller, afterwards — kept the
    old public key for it. That is the state the module docstring promises cannot
    happen: "the keyset cannot disagree with the keys it is supposed to authorize"."""
    keys = _keydir(tmp_path, runners=1)
    before = (tmp_path / "agents.json").read_text()
    eb_seed = tmp_path / "eb-01" / "seed.hex"
    original_eb = eb_seed.read_text()
    eb_seed.unlink()                      # first in sorted order; runner-01 survives

    with pytest.raises(SystemExit) as e:
        G.write_seeds(tmp_path, keys)
    assert "already exist" in str(e.value)
    assert "Nothing was written" in str(e.value)

    # The refusal left the directory exactly as it was: no new seed for eb-01, so
    # agents.json cannot have drifted away from what is on disk.
    assert not eb_seed.exists(), "a new seed was written despite the refusal"
    assert (tmp_path / "agents.json").read_text() == before
    assert original_eb  # the original was readable before we removed it


def test_force_still_rotates_every_key(tmp_path):
    """The pre-check must not make `--force` a no-op."""
    keys = _keydir(tmp_path, runners=1)
    first = (tmp_path / "eb-01" / "seed.hex").read_text()
    fresh = G.generate(1)
    G.write_seeds(tmp_path, fresh, force=True)
    assert (tmp_path / "eb-01" / "seed.hex").read_text() != first
    assert keys  # the originals existed; the point is force overwrote them


# ---- preflight check 14: the image must contain the code being enforced -----
#
# eventing-lab#10. Check 4 proves an image can be PULLED; on 2026-10-14 it passed
# green against an EventBridge image with no signing code in it, and the deploy then
# turned enforcement on. These cover the decision (which overlay enforces what) and
# the wiring, neither of which needs a cluster. The probe pod itself does — see the
# PR, and eventing-lab#14.

import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import k8s_preflight as PF  # noqa: E402

_HAVE_KUBECTL = shutil.which("kubectl") is not None
needs_kubectl = pytest.mark.skipif(not _HAVE_KUBECTL, reason="kubectl not on PATH")

REPO = pathlib.Path(__file__).resolve().parent.parent


class _Args:
    """Only the attributes Preflight.__init__ and check 14 read."""

    def __init__(self, overlay):
        self.overlay = overlay
        self.namespace = "kev1"
        self.registry = "quay.io/aslomnet/"
        self.tag = "local1"
        self.context = None
        self.kubeconfig = None


def _pf(overlay, k=None):
    from k8slib import Kubectl
    return PF.Preflight(k or Kubectl(), Checks(prefix="test"), _Args(overlay))


def test_configmap_value_reads_the_named_document_not_just_any_match():
    """The reason this splits on `---` first: the render holds several ConfigMaps, and
    a bare search would read a key out of whichever one happened to match."""
    rendered = (
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: other\ndata:\n"
        '  ER_REQUIRE_SIGNATURE: "true"\n'
        "---\n"
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: eventing-config\ndata:\n"
        '  ER_REQUIRE_SIGNATURE: "false"\n'
    )
    assert PF.configmap_value(rendered, "eventing-config", "ER_REQUIRE_SIGNATURE") == "false"
    assert PF.configmap_value(rendered, "other", "ER_REQUIRE_SIGNATURE") == "true"


def test_configmap_value_is_none_when_the_key_is_absent():
    """Absent must read as off, matching both services' own defaults — not as a
    crash, and not as on."""
    rendered = ("apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
                "  name: eventing-config\ndata:\n  SOMETHING_ELSE: \"x\"\n")
    assert PF.configmap_value(rendered, "eventing-config", "ER_REQUIRE_SIGNATURE") is None
    assert PF.configmap_value(rendered, "nope", "ER_REQUIRE_SIGNATURE") is None


def test_signing_enforcement_refuses_a_render_with_no_eventing_config(monkeypatch):
    """A render missing the `eventing-config` ConfigMap entirely is not "enforcement
    off" — it is a check that failed to find the state it reads, and that is
    indistinguishable from a genuinely-absent key through `configmap_value`'s `None`.
    A `namePrefix`, a `configMapGenerator` (content-hash suffix) or a rename all do
    this, and `eventing-config` is an unconditional resource of
    `k8s/base/kustomization.yaml`, so the only honest response is to refuse."""
    pf = _pf("kind")
    monkeypatch.setattr(pf.k, "kustomize", lambda path: (
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: something-else\ndata:\n"
        '  ER_REQUIRE_SIGNATURE: "true"\n'))
    with pytest.raises(RuntimeError, match="renders no ConfigMap named 'eventing-config'"):
        pf.signing_enforcement()


@needs_kubectl
def test_the_signed_overlay_is_detected_as_enforcing_both_directions():
    """Driven off the REAL render, because the point of #10 is to read the state
    rather than trust a name."""
    assert _pf("kind-signed").signing_enforcement() == {
        "eventrunner": True, "eventbridge": True}


@needs_kubectl
@pytest.mark.parametrize("overlay", ["test", "kind", "demo"])
def test_the_unsigned_overlays_are_detected_as_not_enforcing(overlay):
    assert _pf(overlay).signing_enforcement() == {
        "eventrunner": False, "eventbridge": False}


def test_the_check_creates_no_pod_when_nothing_is_enforced(monkeypatch):
    """The unsigned fast path must pay nothing. A pod here would add ~30-60 s to every
    `k8s_deploy.py` run against `test`/`kind`/`demo`."""
    pf = _pf("kind")
    monkeypatch.setattr(pf, "signing_enforcement",
                        lambda: {"eventrunner": False, "eventbridge": False})
    monkeypatch.setattr(pf, "_probe_signing_code",
                        lambda *a, **k: pytest.fail("probed despite no enforcement"))
    pf.check_signing_code_present()
    assert pf.c.failed == 0


def test_both_services_are_probed_when_only_one_direction_enforces(monkeypatch):
    """Enforcement and signing sit on opposite ends of the wire: `ER_REQUIRE_SIGNATURE`
    is verified by eventrunner but SIGNED by eventbridge (`kafka_out.py`), and
    vice versa. So the service that must carry the signing code when one direction
    enforces is the OTHER one, not just the enforcing one — probing only the
    enforcing service would have missed 2026-10-14 in its original orientation,
    where the image without the code was the bridge's and the service that refused
    everything was the runner."""
    pf = _pf("kind-signed")
    monkeypatch.setattr(pf, "signing_enforcement",
                        lambda: {"eventrunner": True, "eventbridge": False})
    probed = []
    monkeypatch.setattr(pf, "_probe_signing_code",
                        lambda svc, ref, mod: probed.append((svc, mod)))
    pf.check_signing_code_present()
    assert sorted(probed) == [("eventbridge", "eventbridge.__main__"),
                              ("eventrunner", "eventrunner.__main__")]


def test_the_probe_fails_fast_on_an_unpullable_image_without_creating_a_pod(monkeypatch):
    """Check 4 normally proves pullability first, but it is skippable (`--skip 4`),
    and this check must not then burn its full 180s wait in ImagePullBackOff only to
    report `phase=Pending` under a message about missing signing code -- the wrong
    diagnosis for a pull failure."""
    pf = _pf("kind-signed")
    monkeypatch.setattr(PF, "registry_manifest",
                        lambda ref, **kw: (404, None, "not found"))
    created = []
    monkeypatch.setattr(pf.k, "apply_stdin",
                        lambda *a, **k: created.append(1) or pytest.fail("pod created"))
    pf._probe_signing_code("eventrunner", "example.com/not-pushed:latest",
                           "eventrunner.__main__")
    assert created == []
    assert pf.c.failed == 1


def test_the_probe_targets_each_services_own_main_module():
    """This is the test that matters most, and it exists because the first version of
    this check got it WRONG: it imported `eventbridge.kafka_in`, which imports fine
    without `eventrunner/` present and would therefore have passed against the exact
    image that caused the 2026-10-14 evening.

    `__main__` is what `python -m <pkg>` runs, and it is the module that reaches
    `handlers.py:18`'s `from eventrunner import agentspec`. A future edit that
    "simplifies" this back to a leaf module reintroduces the bug silently, so the
    choice is pinned here rather than left to the comment.
    """
    assert PF.ENFORCEMENT_KEYS["eventbridge"][1] == "eventbridge.__main__"
    assert PF.ENFORCEMENT_KEYS["eventrunner"][1] == "eventrunner.__main__"
    for svc, (_key, mod) in PF.ENFORCEMENT_KEYS.items():
        assert mod == f"{svc}.__main__"


def test_the_bridge_main_really_does_need_eventrunner_present():
    """The premise behind the module choice, asserted rather than assumed: block
    `eventrunner` the way a Dockerfile that never COPYed it would, and the bridge's
    `__main__` must fail to import. If this ever stops being true the probe has
    quietly lost its teeth, and this test says so."""
    code = (
        "import sys, importlib\n"
        "class B:\n"
        "    def find_spec(self, name, target=None, path=None):\n"
        "        if name.split('.')[0] == 'eventrunner':\n"
        "            raise ModuleNotFoundError(name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "try:\n"
        "    importlib.import_module('eventbridge.__main__')\n"
        "    print('IMPORTED')\n"
        "except ModuleNotFoundError:\n"
        "    print('REFUSED')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=str(REPO))
    assert "REFUSED" in r.stdout, (
        f"eventbridge.__main__ imported without eventrunner — the probe would no "
        f"longer catch the original bug.\nstdout={r.stdout!r} stderr={r.stderr[-300:]!r}")


def test_check_14_is_registered_and_would_be_dispatched():
    """The wiring guard. A method nothing dispatches is the shape of the bug being
    fixed. Mutation-checked: removing the CHECKS entry fails this."""
    assert 14 in PF.CHECKS, "check 14 is not registered in CHECKS"
    title, method = PF.CHECKS[14]
    assert method == "check_signing_code_present"
    assert callable(getattr(PF.Preflight, method, None))
    # run_pre dispatches by getattr over CHECKS, so registration is sufficient —
    # but assert the contract rather than trusting it.
    src = (REPO / "scripts" / "k8s_preflight.py").read_text()
    assert "getattr(pf, method)()" in src, "run_pre no longer dispatches by name"


def test_kind_signed_is_an_accepted_overlay_for_preflight():
    """`k8s_deploy.py` already passes `kind-signed` into Preflight, so preflight's own
    parser rejecting it made the value unvalidated and unread — part of why check 4
    never saw the problem."""
    args = PF.build_args(["--overlay", "kind-signed"])
    assert args.overlay == "kind-signed"


@pytest.mark.parametrize("overlay", ["kind-signed", "kind-signed-auth"])
def test_the_key_material_guard_fires_for_every_signed_overlay(monkeypatch, overlay):
    """The guard in `main()` used to read `args.overlay == "kind-signed"` exactly —
    the same name-equality bug this PR fixed in `signed_beats`. `kind-signed-auth`
    inherits both enforcement flags from `kind-signed`, so a deploy with no key
    material and no `--signing-keys` must be refused for it too, rather than walking
    past the one guard that exists to stop a cluster that refuses every event with no
    obvious cause."""
    monkeypatch.setattr(Kubectl, "get", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        D.main(["--overlay", overlay])


def test_the_key_material_guard_does_not_fire_for_an_unsigned_overlay(monkeypatch):
    """The converse of the above: an overlay that enforces nothing must not be
    blocked for lacking key material it will never read."""
    monkeypatch.setattr(Kubectl, "get", lambda *a, **k: None)
    # check-only short-circuits before anything that needs a real cluster.
    try:
        D.main(["--overlay", "kind", "--check-only"])
    except SystemExit as e:
        assert e.code in (0, None), (
            "the unsigned overlay must not die on the key-material guard")


# ---- a locally loaded image is not in any registry (found on a cluster) -----
#
# Checks 4 and 14 both demanded HTTP 200 from an anonymous manifest GET, which made
# them unreachable for the documented local-image path: an image built with
# `build_images.py --local` and `kind load`ed is ON THE NODE and in NO registry, so the
# registry answers 404 by design. Found on 2026-10-19 by running preflight against a
# `:local2` build that was sitting on the node — both checks hard-failed with
# "make the repository public again", which is the wrong remedy for an image that was
# never meant to be in a registry.
#
# Same shape as what these checks exist to catch: an assertion about an intent (is it
# in the registry?) standing in for the state that matters (can the node run it?).


class _NodeImageKubectl(_FakeKubectl):
    """A cluster whose node already holds the refs it is given."""

    def __init__(self, present=()):
        super().__init__()
        self._present = set(present)
        self.pods_created: list[str] = []

    def node_images(self):
        return set(self._present)

    def node_architectures(self):
        return {"arm64"}

    def items(self, kind, **kw):
        return []

    def delete(self, *a, **kw):
        return None


def test_node_images_reads_every_name_the_kubelet_reports():
    """`status.images[].names` is the only place a locally loaded image appears: it is
    on the node and in no registry. This is also what the kubelet itself consults."""
    from k8slib import Kubectl

    class K(Kubectl):
        def __init__(self):
            pass

        def items(self, kind, **kw):
            assert kind == "nodes"
            return [{"status": {"images": [
                {"names": ["quay.io/x/a:local2", "quay.io/x/a@sha256:deadbeef"]},
                {"names": ["quay.io/x/b:dev"]},
                {},                       # a node entry with no names at all
            ]}}]

    names = K().node_images()
    assert "quay.io/x/a:local2" in names
    assert "quay.io/x/b:dev" in names
    assert "quay.io/x/a@sha256:deadbeef" in names


def test_the_signing_probe_accepts_an_image_present_on_the_node(monkeypatch):
    """The bug. A `kind load`ed image must reach the probe rather than being rejected
    as un-pullable — and the registry must not even be consulted for it, because the
    answer would be a misleading 404."""
    import k8s_preflight as PF

    monkeypatch.setattr(PF, "registry_manifest",
                        lambda ref, **kw: (404, None, "not found"))

    ref = "quay.io/aslomnet/rossoctl-eventrunner:local2"
    k = _NodeImageKubectl(present={ref})
    c = Checks(prefix="test")
    pf = PF.Preflight(k, c, _Args("kind-signed"))
    # Stop before the pod: the thing under test is the gate, not the probe.
    monkeypatch.setattr(pf.k, "apply_stdin",
                        lambda *a, **kw: Result(argv=["kubectl"], rc=1, out="", err="",
                                                duration_s=0.0, launched=False,
                                                timed_out=False))
    pf._probe_signing_code("eventrunner", ref, "eventrunner.__main__")
    assert not any("pullable" in f for f in c.failures), \
        f"a node-local image must pass the gate, got {c.failures}"


def test_the_signing_probe_still_fails_an_image_that_is_nowhere(monkeypatch):
    """The fix must not become 'skip the check'. An image neither on the node nor in a
    registry is a real failure, and the message has to name both remedies."""
    import k8s_preflight as PF
    monkeypatch.setattr(PF, "registry_manifest",
                        lambda ref, **kw: (404, None, "not found"))
    c = Checks(prefix="test")
    pf = PF.Preflight(_NodeImageKubectl(present=set()), c, _Args("kind-signed"))
    pf._probe_signing_code("eventrunner", "quay.io/x/missing:local9",
                           "eventrunner.__main__")
    assert c.failed == 1, "a nowhere-image must fail"
    joined = " ".join(c.failures)
    assert "pullable or present on a node" in joined


def test_check_4_accepts_a_node_local_image_without_asking_the_registry(monkeypatch):
    """Check 4 had the same defect, and fixing only check 14 would leave the deploy
    blocked one check earlier — with "make the repository public again" as the advice
    for an image that was never meant to be in a registry."""
    import k8s_preflight as PF
    monkeypatch.setattr(PF, "registry_manifest",
                        lambda ref, **kw: (404, None, "nf"))
    c = Checks(prefix="test")
    args = _Args("kind-signed")
    # The refs check 4 builds come from `Images(registry, tag)`, so the node has to
    # hold exactly those — matching on a tag the args do not use would pass this test
    # against code that still asked the registry.
    pf = PF.Preflight(_NodeImageKubectl(), c, args)
    imgs = set(PF.Images(args.registry, args.tag).all())
    pf.k._present = imgs
    pf.check_images()
    assert c.failed == 0, f"node-local images must pass check 4, got {c.failures}"


# ---- --tag was accepted and ignored (found on a cluster, 2026-10-19) --------
#
# `--tag` was declared in `build_args` and referenced NOWHERE else in k8s_deploy.py:
# one mention in the whole file. So `--overlay kind-signed-device --tag local2`
# applied the base's `:dev` to both Deployments and ran whatever `:dev` happened to
# be -- five-day-old code whose `Cfg` had no `verify_keyset_path`, so EventBridge
# loaded no keyset and printed no signing banner.
#
# 51 of 52 deploy checks passed. Preflight check 14 passed too, having probed the
# right IMAGE while the pod ran a different one. The only check that was right was
# `_verify_keyset_enforcing`, which reads the process rather than the manifest.
#
# It also made a documented procedure wrong: the demo script and the 2026-10-14
# report both say to pass `--tag local1` to avoid the `:dev` reset, and passing it
# never did that.


def _deploy_src() -> str:
    return (REPO / "scripts" / "k8s_deploy.py").read_text()


def _call_lines(src: str, name: str) -> list[int]:
    """Line numbers where `name` is CALLED — not defined, not named in a comment.

    Same helper and same reason as `tests/test_dockerfile_packaging.py`: an earlier
    guard there passed against code with the call deleted, because a comment
    mentioning the function and its own `def` both matched a raw text search.
    """
    out = []
    for n, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(rf"(?<!def ){re.escape(name)}\s*\(", code):
            out.append(n)
    return out


def test_the_deploy_applies_the_tag_to_both_deployments():
    """The bug: a flag that is parsed and never used is worse than no flag."""
    src = _deploy_src()
    called = _call_lines(src, "self._apply_image_tag")
    assert called, "_apply_image_tag is never called"
    applies = _call_lines(src, "apply_kustomize")
    assert applies and any(c > min(applies) for c in called), (
        "the tag must be applied AFTER apply_kustomize — the overlay sets `:dev`, "
        "so patching before it would be overwritten")


def test_the_tag_patch_sets_both_services_and_relaxes_the_pull_policy():
    """`imagePullPolicy: Always` with a locally built tag means the kubelet goes to
    the registry, 404s, and keeps the container already running — the stale-image
    failure in its quietest form."""
    src = _deploy_src()
    body = src[src.index("def _apply_image_tag"):]
    body = body[:body.index("\n    def ", 1)]
    for svc in ("eventbridge", "eventrunner"):
        assert svc in body, f"{svc} is not patched"
    assert "IfNotPresent" in body
    assert "DEFAULT_TAG" in body, \
        "a :dev deploy from a registry should keep pulling — the relax must be " \
        "conditional on a non-default tag"


def test_the_tag_patch_says_it_will_diff_forever():
    """§14.1 has two mechanisms and this patch defeats both: the render always says
    `:dev`, so `kubectl diff` reports cluster-side drift on every later run, while
    `stamp_digest` stamps the digest of the UNPATCHED render and therefore matches.
    The cheap gate says "unchanged", the authoritative one says "drifted", and they
    contradict each other permanently — for the one workflow the demo script tells you
    to use. `k8s/base/configmap.yaml` states the rule from the safe side: a value the
    manifest never mentions is merged rather than reverted. `image` IS mentioned.

    Not fixable here without making the tag part of the render (the closed #9's
    `images:` transformer), so the requirement is that it be SAID, not discovered as a
    confusing "drift detected" on a cluster nobody touched."""
    src = _deploy_src()
    body = src[src.index("def _apply_image_tag"):]
    body = body[:body.index("\n    def ", 1)]
    assert "drift" in body.lower(), \
        "the permanent-drift consequence must be stated, in the log and the docstring"
    logged = [ln for ln in body.splitlines() if "self.c.log" in ln]
    assert logged, "the note must reach the operator's log, not only the source"
    assert "--force-deploy" in body, \
        "the note must say what to do about it — the fast path is now unreachable"


def test_the_drift_note_is_only_for_a_non_default_tag():
    """A `:dev` deploy renders what it patches, so there is no drift and no note.
    Printing one anyway would train the reader to ignore it."""
    src = _deploy_src()
    body = src[src.index("def _apply_image_tag"):]
    body = body[:body.index("\n    def ", 1)]
    note = body[body.index("self.c.log"):]
    guard = body[:body.index("self.c.log")]
    assert "if not default:" in guard, \
        "the note must be guarded on a non-default tag"
    assert "drift" in note.lower()


def test_the_default_tag_keeps_the_registry_pull_policy():
    """Only a NON-default tag relaxes the policy. Relaxing it for `:dev` too would
    make a registry deploy silently prefer a stale local layer."""
    import k8s_deploy as D
    src = _deploy_src()
    body = src[src.index("def _apply_image_tag"):]
    body = body[:body.index("\n    def ", 1)]
    assert "default = tag == DEFAULT_TAG" in body
    assert D.DEFAULT_TAG, "DEFAULT_TAG must be a real value to compare against"


def test_kubectl_patch_exists_and_does_not_take_a_credential():
    """`kubectl patch` has no `-f -` form for the patch body, so it goes into argv.
    That is fine for manifest fragments and not for secrets — anything secret has to
    keep using `apply_stdin`, which is why this is documented on the method."""
    from k8slib import Kubectl
    assert hasattr(Kubectl, "patch")
    doc = Kubectl.patch.__doc__ or ""
    assert "apply_stdin" in doc, \
        "the docstring must point secrets at apply_stdin"
