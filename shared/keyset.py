"""The approved-key set: `kid` -> Ed25519 public key. Stdlib only.

`signing.verify_with_keyset` consults this module, so this file **is** the
authorization list: a signed event whose `kid` is not in the set is refused, and so is
one carrying no signature at all. Approving an agent means adding its public key here;
revoking it means removing the entry. That is a deliberate design choice rather than a
placeholder for something richer:

* It needs no issuer, no network call, and no clock, so it works identically on a
  laptop and in a cluster.
* The `kid` indirection is the part a richer issuer would reuse: selecting a key by
  the name the token carries is what SPIRE-issued keys would also need.

**What the upgrade path to SPIRE actually costs.** An earlier revision of this
docstring claimed the set "is the same shape as a JWKS, so the upgrade path — SPIRE
issuing and rotating the keys — replaces *where the keys come from* without changing a
line of verification logic." That is false in three ways, and it is corrected here
rather than quietly deleted because the talk points readers at this file:

* **The verifier accepts EdDSA only.** `signing.verify_event` refuses any other `alg`
  outright; SPIRE issues EC or RSA SVIDs. Supporting them means an issuer-agnostic
  verifier — EC/RSA added to a hand-rolled pure-Python implementation whose
  `_scalar_mult` is documented as not side-channel resistant, or relaxing the
  no-C-extensions rule and adopting `cryptography`.
* **This is not JWKS shape.** A JWKS is `{"keys": [ {kty, kid, x, ...}, ... ]}` — a
  *list of key objects*. `load()` requires a flat JSON object of `kid` to an encoded
  32-byte string, so it rejects a JWKS document rather than reading it.
* **Only the kid lookup survives.** The algorithm and the file format do not.

So the upgrade is a real piece of work, not a configuration change. See
`docs/specs/0003-phase2-identity-on-the-event-path.md` §8.3.

What the set does not do at all is attest anything. It records which keys an operator
approved, not which workloads a platform vouched for. Say that plainly when demoing it.

One consequence that is easy to miss: a flat set makes the asymmetric keys
**attribution, not restriction.** It tells you *which approved key* signed, not that
this particular agent was permitted to answer this particular request — EventBridge's
own key is in the verify set by requirement, so its signature validates on any run's
answer, as does any approved runner's. Group lifecycle events are pinned to
`EB_SIGNING_KID` for exactly this reason; member answers are not.

File format — a JSON object of `kid` to public key, each 32 bytes encoded as hex,
base64 or base64url:

    {
      "runner-01": "3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29",
      "runner-02": "O2onvM8oCu..."
    }

Only public keys belong here. A 32-byte Ed25519 *seed* is indistinguishable from a
public key by length alone, so `load()` cannot tell you that you pasted a private
key by mistake — keep the set in a ConfigMap, never a Secret, so the distinction
stays obvious in review.
"""
from __future__ import annotations

import base64
import json
import pathlib


def _decode_key(value: str, kid: str) -> bytes:
    """Decode one 32-byte public key. Same ladder as `signing.load_seed`."""
    text = value.strip()
    for decode in (bytes.fromhex,
                   lambda s: base64.b64decode(s, validate=True),
                   lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))):
        try:
            out = decode(text)
        except Exception:  # noqa: BLE001
            continue
        if len(out) == 32:
            return out
    raise ValueError(
        f"key for kid {kid!r} is not a 32-byte Ed25519 public key "
        f"(hex, base64 or base64url)")


class KeySet:
    """An immutable `kid` -> public key mapping.

    Loaded once at startup. Rotation means restarting with a new file; live reload
    is deliberately absent, because a verifier that silently widens the set it
    trusts is harder to reason about than one that restarts.
    """

    def __init__(self, keys: dict[str, bytes]) -> None:
        self._keys = dict(keys)

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, kid: object) -> bool:
        return kid in self._keys

    @property
    def kids(self) -> tuple[str, ...]:
        """Approved key ids, sorted. Safe to log — a public key id is not a secret."""
        return tuple(sorted(self._keys))

    def select(self, kid: str | None) -> bytes | None:
        """The key named by `kid`, or None if it is unknown or absent.

        A `None` kid returns the only key when the set holds exactly one, which
        keeps a single-key deployment working without every signer having to name
        its key. With two or more keys an unnamed token is ambiguous, and guessing
        would mean accepting a signature from any approved agent when the event
        claimed to be from none of them — so it returns None and the caller
        refuses.
        """
        if kid is None:
            if len(self._keys) == 1:
                return next(iter(self._keys.values()))
            return None
        return self._keys.get(kid)


def load(path: str | pathlib.Path) -> KeySet:
    """Read an approved-key set from a JSON file.

    Raises on a malformed file rather than starting with a partial set: an
    authorization list that silently lost an entry fails closed for a legitimate
    agent, which looks like a broken deployment rather than a security control.
    """
    p = pathlib.Path(path)
    raw = json.loads(p.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: expected a JSON object of kid -> public key")
    keys: dict[str, bytes] = {}
    for kid, value in raw.items():
        if not isinstance(kid, str) or not kid:
            raise ValueError(f"{p}: every kid must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError(f"{p}: key for kid {kid!r} must be a string")
        keys[kid] = _decode_key(value, kid)
    return KeySet(keys)


def load_if_set(path: str | pathlib.Path | None) -> KeySet | None:
    """`load()` when a path is configured, else None — meaning "not enabled"."""
    if not path:
        return None
    return load(path)


def refusal_if_enforcing_empty(
    ks: "KeySet | None",
    *,
    enforcing: bool,
    service: str,
    path: str | pathlib.Path | None,
    env_var: str,
) -> str | None:
    """The message to exit with when enforcement is on and the set is empty, else None.

    An approved set with zero kids admits nothing: `select()` returns None for every
    kid, so under enforcement every event is refused — including the service's own
    genuine traffic. There is no deployment that configuration serves, and it is not
    self-announcing: the startup banner reads `ENFORCING` either way, so the symptom
    is a system that refuses everything while every indicator is green. That is how
    an evening went on eventing-lab#2 (see `../docs/reports/phase2-cluster-run-report.md`
    §4.3), and the refusal here is the fix.

    The policy is not new — it is the one `eventbridge/__main__.py` already applies to
    a *missing* `EB_SIGNING_KID`, and the comment above both load sites argues for it
    in general: a service that believes it is enforcing but is not fails silently,
    while one that will not start says so in `kubectl logs` before it accepts a
    request. An empty set is the same class of misconfiguration and wider in effect —
    a missing kid breaks attribution of group events, an empty set breaks everything.

    Two configurations are deliberately left alone, because both are meaningful:

    * **audit only** with an empty set — it admits everything and the banner says so;
    * **no keyset at all** plus `require` — single-key verification, a different and
      documented path.

    Returns a string rather than raising so the caller owns the exit, which keeps this
    testable without catching SystemExit and lets each service name its own variable.
    """
    if ks is None or len(ks) or not enforcing:
        return None
    return (
        f"[{service}] {env_var} points at an approved set with 0 kid(s) "
        f"({path}), and signature enforcement is on. A set with no keys can "
        f"verify nothing, so every event would be refused — including this "
        f"system's own. Add the approved public keys, or turn enforcement off "
        f"to run in audit-only mode."
    )
