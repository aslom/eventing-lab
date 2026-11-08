# ADR-0002: Verify Ed25519 through libcrypto when it loads, falling back to pure Python

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** aslom

## Context

`shared/signing.verify` is pure Python because §1.1 requires stdlib only — no build step, no
wheel, identical behaviour on a laptop and in a cluster. The cost of that, measured rather
than estimated:

| | per verification |
| --- | --- |
| pure Python, x86 node (where this deploys) | **183–201 ms** |
| pure Python, arm64 laptop | 110 ms |

That is invisible when signing one terminal event per agent turn. It is ruinous for the one
job that verifies in bulk. On a real 4410-event ykt1 topic (488 signed), `audit_topic.py`
spent:

```
import            0.53 s
kafka read        5.03 s
verify/classify  93.24 s   ← 94% of runtime
TOTAL            98.80 s
```

So 94% of a ~99-second command was one function, and no other optimisation could matter. The
cost is `_scalar_mult`: ~253 iterations of `_edwards_add`, each doing a modular inversion via
`pow(x, P-2, P)`.

Neither `cryptography` nor `pynacl` is installed — not in the venv, not in the runner image —
so there was no native path simply waiting to be imported.

## Decision

We will try **OpenSSL's Ed25519 via `ctypes`** (`shared/ed25519_native.py`) and **fall back to
the pure implementation automatically** when it is unavailable. `signing.verify` keeps its
signature and contract; no caller changes.

`libcrypto` is already present because `ssl` links it, so this adds a fast path without adding
a dependency, a wheel to compile, or an image rebuild.

### Alternatives considered

- **Adopt `cryptography`** — the better long-term trade: audited, and it does not hand-roll a
  binding. Rejected for now because it breaks the stdlib-only rule that keeps the laptop path
  working with no build step. If that rule is relaxed, this is what to switch to.
- **`pynacl`** — same objection, smaller and less widely audited.
- **Thread the pure implementation** — the project runs free-threaded
  (`Py_GIL_DISABLED=1`), so this would help. But 8 threads buys ~8×, where libcrypto buys
  ~1350×, and the pure path would still be the thing being parallelised.
- **Harden `_scalar_mult` by hand** (windowed/precomputed multiplication) — maybe 3–5×, for
  hand-written curve arithmetic in a trust boundary. The existing docstring already warns
  against promoting it; making it more complex is the wrong direction.
- **Drop verification from the audit** or sample it — faster and useless. The audit's whole
  claim is that every terminal verified.

## Consequences

- Positive: **0.144 ms per verification on x86, a 1355× speedup** (0.048 ms / 2280× on
  arm64). The audit's verify step went 93.2 s → ~0.07 s, and end to end 99 s → **11 s**, where
  the remainder is Kafka read and pod startup. Verdicts unchanged: 452 signatures verified,
  all four assertions still pass.
- Positive: no new dependency, no image change required for the capability itself.
- Positive: thread-safe by construction — every call allocates its own `EVP_PKEY` and
  `EVP_MD_CTX` and shares nothing mutable — and `ctypes` releases the GIL around the foreign
  call, so it parallelises on the free-threaded interpreter. We do not thread it: at 0.144 ms
  there is nothing left worth parallelising.
- Negative / accepted cost: a hand-written FFI binding in a verification path. Mitigated by a
  **known-answer test at load time** (accept a valid RFC 8032 vector, reject a tampered one,
  reject a wrong key) so a wrong NID, an ABI change or a FIPS build that disables EdDSA
  surfaces as a refusal to load rather than as verifications that quietly always fail — which
  would read as "every event is a forgery", arriving fast.
- Negative / accepted cost: two implementations that must agree.
  `tests/test_ed25519_native.py` pins agreement on valid, tampered, wrong-key, malformed and
  random input, asserts nothing the fast path accepts is rejected by the reference, and proves
  the fallback still works when the native path is forced off.
- Negative / accepted cost: `ctypes.util.find_library("crypto")` is not usable alone. On macOS
  it returns Apple's `/usr/lib/libcrypto.dylib`, which refuses third-party loading by
  **aborting the process** (SIGABRT, uncatchable) rather than raising — so that path is
  excluded explicitly and Homebrew's is preferred. `ED25519_LIBCRYPTO` overrides.
- Follow-up owed: the pure implementation stays as the reference and is never removed. It is
  what makes the stdlib-only constraint true where libcrypto cannot be loaded — which includes
  a plain macOS laptop without Homebrew's OpenSSL.
