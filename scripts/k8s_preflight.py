#!/usr/bin/env python3
"""Pre-deployment checks. docs/specs/0002-phase1-keda-kubernetes.md §12, task T4.1.

Every check corresponds to a failure that is slow or confusing to diagnose later,
so every one fails loudly WITH the remediation. Goal 1 (§2) — a reachable public
URL — is why checks 8 and 9 exist before and around the deploy rather than after.

The checks split into two phases because check 9 can only run once EventBridge is
up:

    --phase pre     checks 1-8 and 10-14, before anything is applied
    --phase public  check 9 only, the gate between deploy steps 4 and 5
    --phase all     both (default when run standalone against a live deploy)

  python3 scripts/k8s_preflight.py
  python3 scripts/k8s_preflight.py --namespace kev2 --overlay demo
  python3 scripts/k8s_preflight.py --skip 6,7        # skip the slow pod probes
  python3 scripts/k8s_preflight.py --phase public

Exit 0 only if every check that ran passed.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import socket
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from imagelib import DEFAULT_REGISTRY, DEFAULT_TAG, Images, manifest_platforms, registry_manifest  # noqa: E402
from k8slib import Kubectl, condition_is, condition_reason, dig, wait_for  # noqa: E402
from proclib import Checks, die  # noqa: E402

PREFIX = "preflight"
HERE = pathlib.Path(__file__).resolve().parent
K8S = HERE.parent / "k8s"

# The namespace holding the shared Strimzi operator and Kafka cluster.
KAFKA_NS = "kafka"
KAFKA_CLUSTER = "my-cluster"
BOOTSTRAP = f"{KAFKA_CLUSTER}-kafka-bootstrap.{KAFKA_NS}.svc:9092"

# The probe that caught Finding 3 (arbitrary-UID writability), reproduced exactly.
UID_PROBE = r"""
import os, pathlib, sys
print(f"uid={os.getuid()} gid={os.getgid()} groups={os.getgroups()} "
      f"HOME={os.environ.get('HOME')} TMPDIR={os.environ.get('TMPDIR')}")
for label in ("TMPDIR", "HOME"):
    d = os.environ.get(label)
    if not d:
        print(f"{label} is unset"); sys.exit(1)
    p = pathlib.Path(d, ".rossoctl-uid-probe")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("ok"); p.unlink()
    print(f"write {label} {d} OK")
import eventrunner.config as c
f = c.load()
print(f"FIX-VERIFIED mock={f.mock_claude}")
# A CA bundle is required for any outbound HTTPS (ntfy fan-out, the selftest's
# public-tunnel probes). debian:*-slim ships none, and without it every HTTPS
# request dies with CERTIFICATE_VERIFY_FAILED — silently, at runtime.
import ssl
print("CA-BUNDLE", ssl.get_default_verify_paths().cafile or "MISSING")
"""


# Which service each `*_REQUIRE_*SIGNATURE` key belongs to, and the module whose
# importability proves that service can actually honour it. Check 14 probes one pod per
# ENFORCING service, so an overlay that enforces neither pays nothing.
#
# The module is each service's `__main__` on purpose, and it is the whole difference
# between this check working and not. Both images run `python -m <pkg>`, so `__main__`
# is exactly what the container executes — and it is the only target that actually
# catches the 2026-10-14 bug. Verified by blocking `eventrunner` at the import system:
# `eventbridge.kafka_in` imports FINE without it and would have passed against the
# broken image; `eventbridge.__main__` raises ModuleNotFoundError, because
# `handlers.py:18` does `from eventrunner import agentspec` at module level. Picking a
# plausible-looking module instead of the one that runs is how a check verifies an
# intent rather than a state. Importing is side-effect free: both files guard `main()`
# behind `if __name__ == "__main__"`.
#
# Each key is read by the service named here, but SIGNED by the other one:
# ER_REQUIRE_SIGNATURE is verified by eventrunner, and the signature it demands is
# produced by eventbridge (kafka_out.py); EB_REQUIRE_RESPONSE_SIGNATURE is the mirror.
# So the enforcing service is never self-sufficient — see check_signing_code_present's
# "probe anyway" branch, which exists because of this.
ENFORCEMENT_KEYS = {
    "eventrunner": ("ER_REQUIRE_SIGNATURE", "eventrunner.__main__"),
    "eventbridge": ("EB_REQUIRE_RESPONSE_SIGNATURE", "eventbridge.__main__"),
}


def configmap_value(rendered: str, name: str, key: str) -> str | None:
    """One key's value out of a rendered ConfigMap, or None when it is absent.

    Regex over the rendered YAML rather than a parser because §1.1 bans `pyyaml` and
    this needs no more: the same approach `tests/test_manifests.py` has used since
    Phase 1. Splitting on document separators first matters — `eventing-config` is one
    of several ConfigMaps in the render, and a bare search would happily read a key
    out of a different document.
    """
    for doc in (d for d in re.split(r"(?m)^---\s*$", rendered) if d.strip()):
        if not re.search(r"(?m)^kind:\s*ConfigMap\s*$", doc):
            continue
        if not re.search(rf"(?m)^\s+name:\s*{re.escape(name)}\s*$", doc):
            continue
        m = re.search(rf'(?m)^\s+{re.escape(key)}:\s*"?([^"\n]*)"?\s*$', doc)
        return m.group(1).strip() if m else None
    return None


class Preflight:
    def __init__(self, k: Kubectl, c: Checks, args) -> None:
        self.k = k
        self.c = c
        self.args = args
        self.ns = args.namespace
        self.images = Images(args.registry, args.tag)
        self.request_topic = f"{self.ns}-requests"
        self.response_topic = f"{self.ns}-responses"
        self.consumer_group = f"{self.ns}-eventrunner"
        self.node_arches: set[str] = set()
        self._node_image_cache: set[str] | None = None

    # ---- 1 --------------------------------------------------------------
    def check_cluster(self) -> None:
        """Deploying into the wrong cluster is the worst outcome available here,
        and it is silent — so the context is printed and must be acknowledged."""
        ctx = self.k.current_context() or "(none)"
        self.c.log(f"target context: {ctx}")
        self.c.log(f"target namespace: {self.ns}")
        ver = self.k.call(["version", "-o", "json"], namespace=False, timeout=60)
        if not self.c.expect_run(ver, "cluster is reachable"):
            die("cannot reach the cluster — check your kubeconfig / oc login",
                prefix=PREFIX)
        try:
            v = json.loads(ver.out)
            self.c.log(f"server: {dig(v, 'serverVersion', 'gitVersion', default='?')}")
        except (ValueError, KeyError):
            pass
        if not (self.args.context or self.args.yes):
            self.c.fail(
                "the target cluster was explicitly confirmed",
                f"refusing to act on the ambient current-context.\n"
                f"  Re-run with --context {ctx!r} to pin it, or --yes to accept it.")
        else:
            self.c.ok("the target cluster was explicitly confirmed")

    # ---- 2 --------------------------------------------------------------
    def check_crds(self) -> None:
        versions = self.k.crd_served_versions("kafkatopics.kafka.strimzi.io")
        self.c.expect("v1" in versions,
                      "KafkaTopic CRD serves kafka.strimzi.io/v1",
                      f"served versions: {versions or 'CRD absent'}. Strimzi 1.0.x "
                      f"serves ONLY v1; a v1beta2 manifest fails outright.")
        so = self.k.crd_served_versions("scaledobjects.keda.sh")
        self.c.expect(bool(so), "ScaledObject CRD is present",
                      "KEDA is not installed in this cluster")

    # ---- 3 --------------------------------------------------------------
    def check_strimzi_watch_scope(self) -> None:
        """Finding 1. STRIMZI_NAMESPACE is a downward-API fieldRef to the
        operator's own namespace, so it watches ONLY that namespace. A KafkaTopic
        created anywhere else is accepted by the API server and then silently
        ignored — no reconcile, no event, no error."""
        dep = self.k.get("deploy", "strimzi-cluster-operator",
                         namespace=KAFKA_NS, missing_ok=True)
        if dep is None:
            self.c.fail("the Strimzi cluster operator was found",
                        f"no deploy/strimzi-cluster-operator in ns {KAFKA_NS}")
            return
        watched: str | None = None
        for container in dig(dep, "spec", "template", "spec", "containers", default=[]):
            for env in container.get("env") or []:
                if env.get("name") != "STRIMZI_NAMESPACE":
                    continue
                if env.get("value"):
                    watched = env["value"]
                else:
                    fp = dig(env, "valueFrom", "fieldRef", "fieldPath", default="")
                    # A fieldRef to metadata.namespace means "my own namespace".
                    watched = KAFKA_NS if fp == "metadata.namespace" else f"<{fp}>"
        self.c.expect(watched == KAFKA_NS,
                      f"Strimzi watches ns {KAFKA_NS}, where the topics are created",
                      f"STRIMZI_NAMESPACE resolves to {watched!r}; KafkaTopics in "
                      f"{KAFKA_NS} would be ignored silently")
        kafka = self.k.get("kafka", KAFKA_CLUSTER, namespace=KAFKA_NS, missing_ok=True)
        if kafka is None:
            self.c.fail(f"Kafka cluster {KAFKA_CLUSTER} exists in ns {KAFKA_NS}", "not found")
        else:
            self.c.expect(condition_is(kafka, "Ready"),
                          f"Kafka/{KAFKA_CLUSTER} is Ready",
                          condition_reason(kafka, "Ready"))
            # Not a failure: this cluster uses ephemeral storage on purpose, and
            # the CR reports Warning=True forever because of it.
            if condition_is(kafka, "Warning"):
                self.c.warn(f"Kafka/{KAFKA_CLUSTER} reports "
                            f"{condition_reason(kafka, 'Warning')[:90]} — expected on "
                            f"an ephemeral single-node test cluster; a restart loses "
                            f"all topic data")

    # ---- 4 + 5 ----------------------------------------------------------
    def check_images(self) -> None:
        """Finding 2. Visibility is a registry setting a human can flip back, and
        the failure mode is a slow, confusing ImagePullBackOff deep into a deploy.
        So this is an UNAUTHENTICATED manifest request — exactly what the kubelet
        does with no pull secret — and it hard-fails. There is no pull-secret
        fallback path: if it fails, make the repositories public again."""
        self.node_arches = self.k.node_architectures()
        self.c.expect(bool(self.node_arches),
                      f"node architectures read: {sorted(self.node_arches)}")
        want = {f"linux/{a}" for a in self.node_arches}
        refs = self.images.all(with_claude=(self.args.overlay == "demo"))
        for ref in refs:
            # A locally built image that was `kind load`ed is on the node and in NO
            # registry, so an anonymous manifest GET is a 404 BY DESIGN. Demanding a
            # 200 unconditionally failed the documented local-image path (the demo
            # script's own `--tag` procedure) and advised "make the repository public",
            # which sends the reader to Quay for an image that was never meant to be
            # there. The arch assertion is skipped along with it: there is no manifest
            # list to read, and an image already on this node runs on this node's
            # architecture by virtue of being there. The predicate itself — registry
            # first, node as fallback — is `_pullable_or_on_node`, shared with the
            # signing probe so the rule cannot drift between the two.
            ok, from_registry, body, why = self._pullable_or_on_node(ref)
            if ok and not from_registry:
                self.c.ok(f"{ref} is {why}")
                continue
            if not self.c.expect(ok,
                                 f"{ref} is anonymously pullable (HTTP 200)",
                                 f"{why}\n"
                                 f"  Make the repository public again; there is no "
                                 f"pull-secret fallback by design.\n"
                                 f"  For a locally built image, load it onto the node: "
                                 f"kind load docker-image {ref} --name <cluster>"):
                continue
            plats = manifest_platforms(body)
            if not plats:
                # A single manifest rather than a list: cannot confirm the arch
                # from the index, so say so instead of pretending.
                self.c.warn(f"{ref} is a single-platform manifest — cannot verify it "
                            f"matches {sorted(want)} without pulling it")
                continue
            self.c.expect(want <= plats,
                          f"{ref} covers the cluster's architecture {sorted(want)}",
                          f"manifest has {sorted(plats)}; an arch mismatch surfaces "
                          f"at runtime as `exec format error`")

    # ---- 6 --------------------------------------------------------------
    def check_arbitrary_uid(self) -> None:
        """Finding 3, re-run as the exact probe that caught it. OpenShift assigns
        an arbitrary UID from the namespace range and overrides the image's USER;
        the process always gets gid 0, so every writable path must be group-0
        owned and group-writable. Deliberately mounts NO volume — a mounted volume
        masks the problem."""
        # On a cluster where the namespace already exists (the reference cluster)
        # this is a no-op; on a fresh Kind cluster the namespace does not exist yet
        # and the probe pod would fail with `namespaces "kev1" not found`. Creating
        # it here is safe — deploy step 1 does exactly the same, idempotently.
        if self.k.get("namespace", self.ns, namespace=False, missing_ok=True) is None:
            self.c.log(f"creating namespace {self.ns} for the probe")
            if not self.c.expect_run(self.k.ensure_namespace(self.ns),
                                     f"namespace {self.ns} created for the probe"):
                return
        name = "rossoctl-preflight-uid"
        self.k.delete("pod", name, namespace=self.ns)
        pod = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": self.ns,
                         "labels": {"app.kubernetes.io/part-of": "rossoctl-eventing"}},
            "spec": {
                "restartPolicy": "Never",
                "securityContext": {"runAsNonRoot": True,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "probe",
                    "image": self.images.eventrunner,
                    "command": ["python", "-c", UID_PROBE],
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}},
                }],
            },
        }
        res = self.k.apply_stdin(json.dumps(pod), namespace=self.ns)
        if not self.c.expect_run(res, "arbitrary-UID probe pod created"):
            return
        try:
            out = wait_for(
                lambda: dig(self.k.get("pod", name, namespace=self.ns, missing_ok=True) or {},
                            "status", "phase", default="") in ("Succeeded", "Failed"),
                timeout=180, interval=3)
            phase = dig(self.k.get("pod", name, namespace=self.ns, missing_ok=True) or {},
                        "status", "phase", default="?")
            logs = self.k.logs(name, namespace=self.ns, tail=40)
            if not self.c.expect(out and phase == "Succeeded",
                                 "the image runs under an SCC-assigned arbitrary UID "
                                 "with NO volume mounted",
                                 f"phase={phase}\n{logs}\n"
                                 f"{self.k.describe('pod', name, namespace=self.ns)[-800:]}"):
                return
            for line in logs.splitlines():
                if line.startswith("uid="):
                    self.c.log(f"  {line}")
            self.c.expect_contains(logs, "write TMPDIR", "the probe wrote $TMPDIR")
            self.c.expect_contains(logs, "write HOME", "the probe wrote $HOME")
            self.c.expect_contains(logs, "FIX-VERIFIED",
                                   "the image can load its own config")
            self.c.expect("CA-BUNDLE MISSING" not in logs,
                          "the image has a CA bundle for outbound HTTPS",
                          "ssl.get_default_verify_paths().cafile is None — every "
                          "HTTPS request (ntfy, selftest probes) will fail with "
                          "CERTIFICATE_VERIFY_FAILED. Install ca-certificates.")
        finally:
            self.k.delete("pod", name, namespace=self.ns)

    # ---- 7 --------------------------------------------------------------
    def check_keda_reaches_kafka(self) -> None:
        """Resolves RQ-3, re-measured at deploy time because it can change.

        A numeric lag value from the external metrics API can only come from KEDA
        having connected to the broker in ns kafka from ns keda and queried the
        topic. A broken scaler means silent no-scaling.

        Uses a throwaway Deployment as the scale target: KEDA's admission webhook
        rejects a second ScaledObject pointing at an existing one.
        """
        probe_topic, temporary = self._probe_topic()
        if probe_topic is None:
            self.c.fail("a topic was available to probe KEDA's Kafka reachability",
                        f"no Ready KafkaTopic in ns {KAFKA_NS}, and one could not be "
                        f"created — check the Strimzi operator is reconciling")
            return
        dep_name = so_name = "rossoctl-preflight-scaler"
        self.k.delete("scaledobject", so_name, namespace=self.ns)
        self.k.delete("deploy", dep_name, namespace=self.ns)
        target = {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": dep_name, "namespace": self.ns},
            "spec": {"replicas": 0,
                     "selector": {"matchLabels": {"app": dep_name}},
                     "template": {"metadata": {"labels": {"app": dep_name}},
                                  "spec": {"containers": [
                                      {"name": "pause",
                                       "image": self.images.eventrunner,
                                       "command": ["sleep", "3600"]}]}}},
        }
        so = {
            "apiVersion": "keda.sh/v1alpha1", "kind": "ScaledObject",
            "metadata": {"name": so_name, "namespace": self.ns},
            "spec": {"scaleTargetRef": {"name": dep_name},
                     "minReplicaCount": 0, "maxReplicaCount": 1,
                     "pollingInterval": 5,
                     "triggers": [{"type": "kafka", "metadata": {
                         "bootstrapServers": BOOTSTRAP,
                         "consumerGroup": "rossoctl-preflight-probe",
                         "topic": probe_topic,
                         "lagThreshold": "1",
                         "offsetResetPolicy": "earliest"}}]},
        }
        try:
            if not self.c.expect_run(self.k.apply_stdin(json.dumps(target), namespace=self.ns),
                                     "probe scale target created"):
                return
            if not self.c.expect_run(self.k.apply_stdin(json.dumps(so), namespace=self.ns),
                                     "probe ScaledObject accepted by the KEDA webhook"):
                return
            ready = wait_for(
                lambda: condition_is(self.k.get("scaledobject", so_name,
                                                namespace=self.ns, missing_ok=True) or {},
                                     "Ready"),
                timeout=120, interval=3)
            live = self.k.get("scaledobject", so_name, namespace=self.ns, missing_ok=True) or {}
            if not self.c.expect(ready,
                                 f"KEDA reaches Kafka at {BOOTSTRAP} (ScaledObject Ready)",
                                 f"Ready={condition_reason(live, 'Ready')}\n"
                                 f"  Cross-namespace DNS or a NetworkPolicy is blocking "
                                 f"the scaler."):
                return
            metric = f"s0-kafka-{probe_topic}"
            path = (f"/apis/external.metrics.k8s.io/v1beta1/namespaces/{self.ns}/"
                    f"{metric}?labelSelector=scaledobject.keda.sh%2Fname%3D{so_name}")
            got = wait_for(lambda: self._metric_value(path) is not None,
                           timeout=90, interval=5)
            value = self._metric_value(path)
            self.c.expect(got and value is not None,
                          f"the external metrics API returns a numeric lag "
                          f"({metric} = {value})",
                          "no numeric value — KEDA's metrics adapter is not serving "
                          "this trigger")
        finally:
            self.k.delete("scaledobject", so_name, namespace=self.ns)
            self.k.delete("deploy", dep_name, namespace=self.ns)
            if temporary:
                self.k.delete("kafkatopic", probe_topic, namespace=KAFKA_NS)

    def _metric_value(self, path: str):
        try:
            body = self.k.raw(path)
        except RuntimeError:
            return None
        for item in (body or {}).get("items") or []:
            v = item.get("value")
            if v is None:
                continue
            try:
                return int(str(v).rstrip("m") or 0)
            except ValueError:
                return None
        return None

    def _probe_topic(self) -> tuple[str | None, bool]:
        """A topic to point the probe trigger at. Returns (name, we_created_it).

        Prefers our own request topic, then any Ready topic, and finally creates a
        throwaway one. The last case is what a brand-new cluster needs: with no
        topic at all the check could only be skipped, and skipping the one check
        that proves KEDA can reach Kafka is the wrong trade — a broken scaler means
        silent no-scaling.
        """
        topics = self.k.items("kafkatopic", namespace=KAFKA_NS)
        ready = [t for t in topics if condition_is(t, "Ready")]
        for t in ready:
            if dig(t, "metadata", "name") == self.request_topic:
                return self.request_topic, False
        if ready:
            return dig(ready[0], "metadata", "name"), False

        name = "rossoctl-preflight-probe"
        self.c.log(f"no Ready KafkaTopic in ns {KAFKA_NS} — creating {name} for the probe")
        topic = {
            "apiVersion": "kafka.strimzi.io/v1", "kind": "KafkaTopic",
            "metadata": {"name": name, "namespace": KAFKA_NS,
                         "labels": {"strimzi.io/cluster": KAFKA_CLUSTER,
                                    "app.kubernetes.io/part-of": "rossoctl-eventing"}},
            "spec": {"partitions": 1, "replicas": 1},
        }
        if not self.k.apply_stdin(json.dumps(topic), namespace=KAFKA_NS).ok:
            return None, False
        ok = wait_for(lambda: condition_is(
            self.k.get("kafkatopic", name, namespace=KAFKA_NS, missing_ok=True) or {},
            "Ready"), timeout=120, interval=3)
        if not ok:
            self.k.delete("kafkatopic", name, namespace=KAFKA_NS)
            return None, False
        return name, True

    # ---- 8 --------------------------------------------------------------
    def check_ingress_capability(self) -> None:
        """Goal 1: fail before deploying, not after. An ingress problem discovered
        at the end wastes the whole cycle."""
        # Aggregated API, not a CRD — see Kubectl.api_group_serves.
        has_route = self.k.api_group_serves("route.openshift.io/v1", "Route")
        classes = [dig(c, "metadata", "name") for c in self.k.items("ingressclass",
                                                                   namespace=False)]
        self.c.expect(has_route or classes,
                      f"the cluster can serve ingress (Route CRD={has_route}, "
                      f"IngressClasses={classes})",
                      "no Route CRD and no IngressClass — nothing can expose "
                      "EventBridge publicly")
        domain = self._apps_domain()
        if not domain:
            self.c.warn("could not read the cluster apps domain "
                        "(ingresses.config.openshift.io/cluster) — skipping the "
                        "wildcard DNS check")
            return
        self.c.log(f"  apps domain: {domain}")
        host = f"eventbridge-{self.ns}.{domain}"
        try:
            addrs = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
            self.c.ok(f"wildcard DNS resolves {host} -> {sorted(addrs)}")
        except socket.gaierror as e:
            self.c.fail(f"wildcard DNS resolves {host}",
                        f"{e}\n  The Route will be created but nothing will reach it.")

    def _apps_domain(self) -> str:
        obj = self.k.get("ingresses.config.openshift.io", "cluster",
                         namespace=False, missing_ok=True)
        return dig(obj or {}, "spec", "domain", default="")

    # ---- 9 (own phase) --------------------------------------------------
    def check_public_url(self, url: str | None = None) -> bool:
        """The gate between deploy steps 4 and 5. Goal 1, proven not assumed:
        `GET https://<route>/healthz` from OUTSIDE the cluster must return 200."""
        base = url or self.route_url()
        if not base:
            self.c.fail("the public URL is reachable from outside the cluster",
                        f"no Route/eventbridge in ns {self.ns} yet")
            return False
        target = f"{base.rstrip('/')}/healthz"

        def probe():
            try:
                with urllib.request.urlopen(target, timeout=5) as r:
                    return r.status == 200
            except (urllib.error.URLError, OSError):
                return False

        out = wait_for(probe, timeout=self.args.public_url_timeout, interval=3)
        return self.c.expect(
            out, f"public URL answers 200: {target} (after {out.elapsed_s:.0f}s)",
            "the Route exists but is not routable. Check the router pods, the "
            "Route's status.ingress conditions, and that the Service has endpoints.")

    def route_url(self) -> str:
        """The externally reachable base URL, from a Route or an Ingress.

        Both are supported because the two target environments differ only here:
        OpenShift has the Route API, Kind has ingress-nginx (§3.2). Discovering it
        rather than requiring --public-url means the same command line works on
        both.
        """
        r = self.k.get("route", "eventbridge", namespace=self.ns, missing_ok=True)
        if r is not None:
            host = dig(r, "spec", "host", default="")
            if host:
                scheme = "https" if dig(r, "spec", "tls", default=None) else "http"
                return f"{scheme}://{host}"
        return self.ingress_url()

    def ingress_url(self) -> str:
        """Base URL from the Ingress, including the host port Kind maps.

        An Ingress records the host it serves but not the port the controller is
        reachable on from outside the cluster — on Kind that is an
        `extraPortMappings` entry in the cluster config, which the API cannot tell
        us. So the port comes from --ingress-port (default 30080, matching
        k8s/kind/kind-cluster.yaml).
        """
        ing = self.k.get("ingress", "eventbridge", namespace=self.ns, missing_ok=True)
        if ing is None:
            return ""
        rules = dig(ing, "spec", "rules", default=[])
        host = dig(rules[0], "host", default="") if rules else ""
        if not host:
            return ""
        tls = dig(ing, "spec", "tls", default=None)
        scheme = "https" if tls else "http"
        port = int(getattr(self.args, "ingress_port", 0) or 30080)
        default_port = 443 if tls else 80
        suffix = "" if port == default_port else f":{port}"
        return f"{scheme}://{host}{suffix}"

    # ---- 10 -------------------------------------------------------------
    def check_topic_collisions(self) -> None:
        """Resolves RQ-5. Consuming someone else's topic would look like a working
        deploy that mysteriously processes foreign events."""
        for name in (self.request_topic, self.response_topic):
            t = self.k.get("kafkatopic", name, namespace=KAFKA_NS, missing_ok=True)
            if t is None:
                self.c.ok(f"topic name {name} is free (or ours to create)")
                continue
            owned = dig(t, "metadata", "labels", "app.kubernetes.io/part-of",
                        default="") == "rossoctl-eventing"
            self.c.expect(owned, f"pre-existing KafkaTopic {name} is ours",
                          f"a KafkaTopic named {name} exists in ns {KAFKA_NS} without "
                          f"our app.kubernetes.io/part-of label — someone else may own "
                          f"it. Pick a different namespace prefix (--namespace) or "
                          f"label the topic if it really is yours.")

    # ---- 11 -------------------------------------------------------------
    def check_namespace(self) -> None:
        ns = self.k.get("namespace", self.ns, namespace=False, missing_ok=True)
        if ns is None:
            self.c.log(f"namespace {self.ns} does not exist yet; deploy will create it")
            res = self.k.apply_stdin(
                json.dumps({"apiVersion": "v1", "kind": "Namespace",
                            "metadata": {"name": self.ns}}),
                namespace=False, server_dry_run=True)
            self.c.expect_run(res, f"namespace {self.ns} is creatable")
            return
        self.c.ok(f"namespace {self.ns} exists")
        uid_range = dig(ns, "metadata", "annotations",
                        "openshift.io/sa.scc.uid-range", default="")
        if uid_range:
            self.c.log(f"  SCC uid-range: {uid_range} (arbitrary-UID containers)")
        for q in self.k.items("resourcequota", namespace=self.ns):
            hard = dig(q, "status", "hard", default={})
            used = dig(q, "status", "used", default={})
            self.c.log(f"  quota {dig(q, 'metadata', 'name')}: used={used} hard={hard}")

    # ---- 12 -------------------------------------------------------------
    def check_storageclass(self) -> None:
        """Only relevant when the overlay asks for a PVC; otherwise a missing
        provisioner is not this deploy's problem."""
        if self.args.overlay != "demo":
            self.c.log("overlay does not request a PVC — skipping the StorageClass check")
            return
        defaults = [dig(s, "metadata", "name") for s in self.k.items("storageclass",
                                                                    namespace=False)
                    if dig(s, "metadata", "annotations",
                           "storageclass.kubernetes.io/is-default-class",
                           default="false") == "true"]
        self.c.expect(bool(defaults),
                      f"a default StorageClass exists: {defaults}",
                      "the demo overlay's PVC would stay Pending forever")


    # ---- 13 -------------------------------------------------------------
    def check_claude_cli_image(self) -> None:
        """T2.4's gate. The demo needs an image that actually carries the CLI;
        without it every real run fails with `claude binary not found`, and mock
        mode is exactly what stops the e2e test from catching that.

        This runs on a real cluster node rather than at build time on purpose: the
        CLI bundles a `bun` binary that aborts under QEMU user-mode emulation, so a
        cross-built image cannot execute it on an arm64 builder. Here it runs
        natively.
        """
        if self.args.overlay != "demo":
            self.c.log("overlay does not use the claude image — skipping")
            return
        ref = self.images.eventrunner_claude
        status, body, err = registry_manifest(ref)
        if not self.c.expect(status == 200, f"{ref} is anonymously pullable",
                             f"status={status} {err}\n"
                             f"  Build it: python3 scripts/build_images.py --with-claude"):
            return
        name = "rossoctl-preflight-claude"
        self.k.delete("pod", name, namespace=self.ns)
        pod = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": self.ns},
            "spec": {"restartPolicy": "Never",
                     "securityContext": {"runAsNonRoot": True,
                                         "seccompProfile": {"type": "RuntimeDefault"}},
                     "containers": [{
                         "name": "probe", "image": ref,
                         "command": ["sh", "-c",
                                     "claude --version && python -c "
                                     "'import eventrunner.config as c; "
                                     "print(\"config-ok\", c.load().mock_claude)'"],
                         "securityContext": {"allowPrivilegeEscalation": False,
                                             "capabilities": {"drop": ["ALL"]}},
                     }]},
        }
        if not self.c.expect_run(self.k.apply_stdin(json.dumps(pod), namespace=self.ns),
                                 "claude-image probe pod created"):
            return
        try:
            wait_for(lambda: dig(self.k.get("pod", name, namespace=self.ns,
                                            missing_ok=True) or {},
                                 "status", "phase", default="") in ("Succeeded", "Failed"),
                     timeout=240, interval=3)
            phase = dig(self.k.get("pod", name, namespace=self.ns, missing_ok=True) or {},
                        "status", "phase", default="?")
            logs = self.k.logs(name, namespace=self.ns, tail=20)
            self.c.expect(phase == "Succeeded",
                          "the derived image runs `claude --version` on a real node",
                          f"phase={phase}\n{logs}")
            for line in logs.splitlines():
                self.c.log(f"  {line}")
            self.c.expect_contains(logs, "Claude Code", "the CLI reported its version")
            self.c.expect_contains(logs, "config-ok",
                                   "the image still loads EventRunner's config")
        finally:
            self.k.delete("pod", name, namespace=self.ns)

    # ---- shared by 4 and 14 ---------------------------------------------
    def _pullable_or_on_node(self, ref: str) -> tuple[bool, bool, dict | None, str]:
        """`(ok, from_registry, body, why)` — can the kubelet get this image at all?

        One predicate for the two checks that ask it. Check 4 and the signing probe
        had the same `status != 200 and ref in self._node_images()` test written out
        twice with different reporting, and the PR's own history is that fixing one
        and not the other left the deploy blocked a check earlier. Shared, so the next
        change to the rule lands in both by construction.

        Registry FIRST, node only as the fallback. Asking the node unconditionally
        would make a pullability question need a reachable cluster to answer something
        the registry can answer alone, and would break every caller that drives this
        with a fake Kubectl.

        `from_registry` is what check 4 needs on top of the verdict: an image on the
        node has no manifest list to read, so the arch assertion has to be skipped
        with it rather than run against an empty body.
        """
        status, body, err = registry_manifest(ref)
        if status == 200:
            return True, True, body, ""
        if ref in self._node_images():
            return True, False, None, "present on a node (loaded locally)"
        return False, False, None, f"status={status} {err}"

    def _node_images(self) -> set[str]:
        """Images the kubelets report, cached, and empty rather than raising.

        Only consulted when the registry has already said no, so a cluster that
        cannot be reached degrades to "the registry's answer stands" instead of
        turning a pullability question into a connection error.
        """
        if self._node_image_cache is None:
            try:
                self._node_image_cache = self.k.node_images()
            except Exception:  # noqa: BLE001 — any kubectl failure means "unknown"
                self._node_image_cache = set()
        return self._node_image_cache

    def signing_enforcement(self) -> dict[str, bool]:
        """Which services THIS overlay turns enforcement on for.

        Read from the rendered manifests, not inferred from the overlay's name. That
        distinction is the whole point of #10: a name is an intent, the ConfigMap is
        the state, and checks 12/13 gate on the name — so a future `demo-signed` or
        `ocp-signed` would silently skip this check if it did the same.

        Rendering is local and needs no cluster (`k8slib.Kubectl.kustomize`). A key
        that is absent reads as off, matching both services' own defaults
        (`eventrunner/config.py` `_bool(..., False)`, `eventbridge/config.py`
        `require_response_signature: bool = False`).
        """
        overlay = K8S / "overlays" / self.args.overlay
        rendered = self.k.kustomize(str(overlay))
        # A render with no `eventing-config` in it is not "enforcement off" — it is a
        # check that failed to find the state it reads. Both look identical through
        # `configmap_value`'s None, and quietly reporting "nothing to assert" is the
        # §10 failure mode itself, so refuse instead. A `namePrefix`, a
        # `configMapGenerator` (which suffixes a content hash) or a rename all trigger
        # this; `eventing-config` is an unconditional resource of
        # `k8s/base/kustomization.yaml`, so its absence is always a lookup bug.
        if not re.search(r"(?m)^\s+name:\s*eventing-config\s*$", rendered):
            raise RuntimeError(
                f"overlay {self.args.overlay!r} renders no ConfigMap named "
                f"'eventing-config' — check 14 cannot read what is enforced. Fix the "
                f"lookup rather than letting the check pass having verified nothing.")
        return {
            svc: (configmap_value(rendered, "eventing-config", key) or "").lower() == "true"
            for svc, (key, _mod) in ENFORCEMENT_KEYS.items()
        }

    # ---- 14 -------------------------------------------------------------
    def check_signing_code_present(self) -> None:
        """Check 4 proves an image can be PULLED. This proves it can do the thing the
        overlay is about to enforce.

        #10, and the reason it is a separate check rather than a line in check 4:
        pullability and contents are different properties, and on 2026-10-14 check 4
        passed green against an EventBridge image that had no signing code in it —
        `handlers.py` imports `eventrunner.agentspec` and the Dockerfile never COPYed
        it. The deploy then enabled enforcement on top. EventRunner refused every
        request, including this system's own traffic, while the banner read
        `ENFORCING`. Four indicators were green and diagnosis took an evening.

        An import, not a grep for a log string: the bug that happened WAS an
        ImportError, so importing reproduces it exactly, and a banner string is a line
        somebody may reword. The module imported is each service's `__main__` — see
        ENFORCEMENT_KEYS for why that specific choice is what makes this check catch
        the original bug rather than pass alongside it.
        """
        enforcing = self.signing_enforcement()
        if not any(enforcing.values()):
            self.c.log(f"overlay {self.args.overlay!r} enforces no signatures "
                       f"(ER_REQUIRE_SIGNATURE / EB_REQUIRE_RESPONSE_SIGNATURE are "
                       f"not \"true\") — nothing to assert")
            return
        for svc, on in sorted(enforcing.items()):
            if not on:
                # NOT `continue`: enforcement in one direction makes the OTHER
                # service's signing code load-bearing, because that is who produces
                # the signature being demanded (ER_REQUIRE_SIGNATURE -> eventbridge
                # signs, via kafka_out.py; EB_REQUIRE_RESPONSE_SIGNATURE ->
                # eventrunner signs, via emit.py). Probing only the enforcing service
                # would have missed 2026-10-14 in its original orientation, where the
                # image without the code was the BRIDGE's and the service that refused
                # everything was the runner. If anything is enforced at all, both
                # images must carry the code.
                self.c.log(f"{svc}: enforcement off here, but the other direction "
                           f"enforces and this service is its signer — probing anyway")
            self._probe_signing_code(svc, getattr(self.images, svc),
                                     ENFORCEMENT_KEYS[svc][1])

    def _probe_signing_code(self, svc: str, ref: str, module: str) -> None:
        """Run the service's own image and make it import what enforcement needs."""
        # The probe must not spend 180 s in ImagePullBackOff and then report
        # `phase=Pending` under a message about missing signing code — check 4 proves
        # pullability but is skippable (`--skip 4`), so this re-establishes it.
        #
        # **Present on the node OR pullable, not pullable alone.** An earlier revision
        # demanded a 200 from the registry, which made this check unreachable in the
        # one situation it was written for: a locally built image that was `kind
        # load`ed is on the node and in NO registry, so an anonymous manifest GET is a
        # 404 by design. Found by running it — on 2026-10-19 both checks 4 and 14
        # hard-failed a `:local2` build that was sitting on the node, which is the same
        # "verified an intent, not a state" shape as the bugs this check exists to
        # catch. The node is also the authority the kubelet itself consults.
        # The predicate is shared with check 4 — see `_pullable_or_on_node` for the
        # registry-first ordering and why the node is only the fallback.
        ok, _from_registry, _body, why = self._pullable_or_on_node(ref)
        if not ok:
            self.c.fail(
                f"{ref} is pullable or present on a node",
                f"not on any node, and the registry says {why}\n"
                f"  Build and push it:  python3 scripts/build_images.py\n"
                f"  Or build locally:   python3 scripts/build_images.py --local "
                f"--tag {self.args.tag} && kind load docker-image {ref} "
                f"--name <cluster>")
            return
        self.c.ok(f"{ref} is pullable or present on a node")
        name = f"rossoctl-preflight-signing-{svc}"
        self.k.delete("pod", name, namespace=self.ns)
        probe = (
            "import shared.signing as s; "
            "s.verify_request; s.response_decision; s.sign_into; "
            f"import {module}; "
            "print('signing-ok')"
        )
        pod = {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": self.ns},
            "spec": {"restartPolicy": "Never",
                     "securityContext": {"runAsNonRoot": True,
                                         "seccompProfile": {"type": "RuntimeDefault"}},
                     "containers": [{
                         "name": "probe", "image": ref,
                         "command": ["python", "-c", probe],
                         "securityContext": {"allowPrivilegeEscalation": False,
                                             "capabilities": {"drop": ["ALL"]}},
                     }]},
        }
        if not self.c.expect_run(self.k.apply_stdin(json.dumps(pod), namespace=self.ns),
                                 f"{svc} signing-code probe pod created"):
            return
        try:
            wait_for(lambda: dig(self.k.get("pod", name, namespace=self.ns,
                                            missing_ok=True) or {},
                                 "status", "phase", default="") in ("Succeeded", "Failed"),
                     timeout=180, interval=3)
            phase = dig(self.k.get("pod", name, namespace=self.ns, missing_ok=True) or {},
                        "status", "phase", default="?")
            logs = self.k.logs(name, namespace=self.ns, tail=40)
            detail = (f"phase={phase} image={ref}\n{logs}\n"
                      f"  This overlay enforces signatures, and this image cannot "
                      f"verify them. Rebuild and re-push (or `kind load`) the image "
                      f"for tag {self.args.tag!r}; deploying now would refuse every "
                      f"event, including this system's own, while the startup banner "
                      f"still reads ENFORCING.")
            if not self.c.expect(phase == "Succeeded",
                                 f"{svc} image {ref} imports the signing code it "
                                 f"is about to enforce", detail):
                return
            self.c.expect_contains(logs, "signing-ok",
                                   f"{svc} probe reported signing-ok")
        finally:
            self.k.delete("pod", name, namespace=self.ns)


CHECKS = {
    1: ("cluster reachable + context confirmed", "check_cluster"),
    2: ("Strimzi + KEDA CRDs", "check_crds"),
    3: ("Strimzi watch scope + Kafka cluster", "check_strimzi_watch_scope"),
    4: ("images anonymously pullable + arch", "check_images"),
    6: ("arbitrary-UID writability", "check_arbitrary_uid"),
    7: ("KEDA reaches Kafka + metrics API", "check_keda_reaches_kafka"),
    8: ("ingress capability + wildcard DNS", "check_ingress_capability"),
    10: ("topic-name collisions", "check_topic_collisions"),
    11: ("namespace + quota", "check_namespace"),
    12: ("default StorageClass (demo overlay)", "check_storageclass"),
    13: ("claude CLI image runs on a real node (demo overlay)", "check_claude_cli_image"),
    14: ("images contain the signing code the overlay enforces", "check_signing_code_present"),
}
# Check 5 (node arch vs image platforms) is folded into check 4: it needs the same
# manifest fetch, and splitting it would mean fetching twice.


def run_pre(pf: Preflight, skip: set[int]) -> None:
    for num, (title, method) in CHECKS.items():
        if num in skip:
            pf.c.log(f"skipping check {num} ({title})")
            continue
        pf.c.section(f"check {num}: {title}")
        getattr(pf, method)()


def build_args(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace", "-n", default=os.environ.get("NS", "kev1"))
    ap.add_argument("--kubeconfig", default=None,
                    help="kubeconfig file (e.g. .kube/config-kind for a Kind cluster)")
    ap.add_argument("--context", default=None, help="pin the kube context explicitly")
    ap.add_argument("--yes", action="store_true",
                    help="accept the ambient current-context without pinning it")
    # `kind-signed` is listed so the standalone invocation documented above
    # (`python3 scripts/k8s_preflight.py --overlay kind-signed`) stops dying with
    # `invalid choice` — check 14 is exactly the check someone wants to run that way.
    # Note this parser is NOT what gated the deploy path: `k8s_deploy.py` builds its
    # own args and passes them straight into `Preflight` (k8s_deploy.py:688), so
    # `kind-signed` was always validated there, by that script's own `choices`.
    ap.add_argument("--overlay", default="test",
                    choices=("test", "kind", "demo", "kind-signed", "kind-signed-auth",
                             "kind-signed-device"))
    ap.add_argument("--registry", default=os.environ.get("REGISTRY_PREFIX", DEFAULT_REGISTRY))
    ap.add_argument("--tag", default=os.environ.get("TAG", DEFAULT_TAG))
    ap.add_argument("--phase", default="pre", choices=("pre", "public", "all"))
    ap.add_argument("--skip", default="", help="comma-separated check numbers to skip")
    ap.add_argument("--public-url-timeout", type=float, default=120.0)
    ap.add_argument("--ingress-port", type=int, default=30080,
                    help="host port the ingress controller is reachable on "
                         "(Kind maps 80 -> 30080 in k8s/kind/kind-cluster.yaml)")
    ap.add_argument("--url", default=None, help="check 9: probe this URL instead of the Route")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = build_args(argv)
    c = Checks(prefix=PREFIX)
    k = Kubectl(context=args.context, namespace=args.namespace,
                kubeconfig=args.kubeconfig)
    pf = Preflight(k, c, args)
    skip = {int(s) for s in args.skip.split(",") if s.strip()}

    if args.phase in ("pre", "all"):
        run_pre(pf, skip)
    if args.phase in ("public", "all"):
        c.section("check 9: public URL reachable end-to-end")
        pf.check_public_url(args.url)
    return c.summary()


if __name__ == "__main__":
    sys.exit(main())
