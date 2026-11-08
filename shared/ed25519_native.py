"""Native Ed25519 verification through libcrypto, with no new dependency.

`signing.verify` is pure Python (§1.1, stdlib only) and costs **183-201 ms per
verification on the x86 nodes this runs on** — measured, not estimated. That is fine for
signing one terminal event per agent turn, and ruinous for the one job that verifies a
whole topic: `scripts/audit_topic.py` spent **93.2 of its 98.8 seconds** in
`signing.verify` on a real 4410-event topic (488 of them signed). Everything else —
imports, the Kafka read of both topics — was 5.6 s combined.

This module binds OpenSSL's Ed25519 via `ctypes`, which brings the same verification to
**0.12 ms**, a ~1500x speedup, and takes that 93 s to 0.06 s.

**Why ctypes rather than `cryptography` or `pynacl`.** Neither is installed, in this venv
or in the runner image, and §1.1's stdlib-only rule is what keeps the laptop path working
with no build step. `ctypes` is stdlib, and `libcrypto` is already present in the image
because `ssl` links it — so this adds a fast path without adding a dependency, a wheel to
compile, or an image rebuild. If the stdlib-only rule is ever relaxed, `cryptography` is
still the better long-term trade (it is audited, and it does not hand-roll the binding).

**It is a fast path, never the only path.** `load()` returns None whenever libcrypto, the
symbols, or the algorithm are missing or behave unexpectedly, and `signing.verify` then
runs the pure implementation unchanged. Three things must stay true, and
`tests/test_ed25519_native.py` pins each:

  1. Both implementations agree — on valid, tampered, wrong-key and malformed input.
  2. The pure path still works when this module is forced to be unavailable.
  3. A verification this module ACCEPTS is one the pure implementation also accepts.

(1) is the one that matters. A fast path that silently disagrees with the reference is
worse than no fast path: it would turn the audit's verdicts into noise while making them
arrive quicker, which is the exact failure `audit_topic.py`'s verdict vocabulary exists
to prevent.

**Free-threading.** The project runs a free-threaded interpreter (`Py_GIL_DISABLED=1`;
`Dockerfile-eventrunner` asserts it at build time). `ctypes` releases the GIL around the
foreign call, so these verifications genuinely parallelise where the pure-Python ones
could not. We do not thread them here: at 0.12 ms, a whole topic verifies in
milliseconds, and a thread pool would add complexity to something that is no longer the
bottleneck. What matters for free-threading is thread SAFETY, and it holds — every call
below allocates its own `EVP_PKEY` and `EVP_MD_CTX`, shares nothing mutable, and touches
no module-level state after `load()`. OpenSSL 3.x is itself thread-safe.

The verification direction uses only public inputs, so unlike `signing._scalar_mult`
there is no secret-dependent timing to reason about. Signing is deliberately NOT
accelerated here: it is not a bottleneck anywhere, and a seed is a secret this module
would then have to be careful with.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import threading
from typing import Callable

# OpenSSL's NID for Ed25519. Stable ABI constant (obj_mac.h), not a version-dependent
# value — but `load()` verifies a known-answer test anyway rather than trusting it.
_NID_ED25519 = 1087

_ED25519_PUBLIC_KEY_LEN = 32
_ED25519_SIGNATURE_LEN = 64

# A pure-Python-generated (message, signature, public key) triple, used as a
# known-answer test so `load()` proves the binding WORKS rather than merely resolving.
# A wrong NID, an ABI change or a FIPS build that disables EdDSA all surface here as a
# refusal to load, instead of as verifications that quietly always fail — which would
# read as "every event is a forgery" on the audit output.
_KAT_PUB = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
_KAT_MSG = b""
_KAT_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")

_lock = threading.Lock()
_cached: Callable[[bytes, bytes, bytes], bool] | None = None
_tried = False
_reason = "not attempted"


def unavailable_reason() -> str:
    """Why the native path is not in use, for a one-line log. Empty when it IS in use."""
    return "" if _cached is not None else _reason


def load() -> Callable[[bytes, bytes, bytes], bool] | None:
    """A `verify(message, signature, pub) -> bool` backed by libcrypto, or None.

    Cached: resolving the library and running the known-answer test costs more than a
    verification, so doing it per event would defeat the purpose. The result is cached
    either way, so a missing libcrypto is diagnosed once rather than on every event.
    """
    global _cached, _tried, _reason
    with _lock:
        if _tried:
            return _cached
        _tried = True
        try:
            _cached = _build()
            _reason = ""
        except Exception as e:  # noqa: BLE001 — ANY failure means "use the pure path"
            # Deliberately broad. This is an optional accelerator, and there is no
            # failure here worth propagating to a caller who has a working fallback:
            # a missing library, a renamed symbol, a FIPS policy, an OSError from a
            # truncated .so. Every one of them means the same thing.
            _cached = None
            _reason = f"{e.__class__.__name__}: {e}"
        return _cached


def _candidates() -> list[str]:
    """Where to look for a loadable libcrypto, best first.

    `ctypes.util.find_library` alone is not usable here. On macOS it returns
    `/usr/lib/libcrypto.dylib`, which Apple ships for its own use and refuses to let a
    third party load — the process does not raise, it **aborts**:

        WARNING: ... is loading libcrypto in an unsafe way
        [SIGABRT]

    An abort cannot be caught, so it would take the whole audit down rather than
    falling back. Hence an explicit list, with Apple's copy filtered out below, and an
    override for anyone whose library is somewhere else entirely.

    Linux — where this actually matters, because that is where the audit pod runs — has
    no such restriction, and `find_library` resolves `libcrypto.so.3` correctly there.
    """
    import os
    import sys

    out: list[str] = []
    override = os.environ.get("ED25519_LIBCRYPTO", "").strip()
    if override:
        # An explicit path wins and is NOT filtered: an operator naming a library is
        # making a deliberate choice, and silently ignoring it would be worse than
        # failing the known-answer test below.
        return [override]
    if sys.platform == "darwin":
        # Homebrew and MacPorts, which are loadable. Apple's /usr/lib copy is not, so
        # it is never added — see the abort above.
        out += [
            "/opt/homebrew/opt/openssl@3/lib/libcrypto.dylib",
            "/opt/homebrew/opt/openssl/lib/libcrypto.dylib",
            "/usr/local/opt/openssl@3/lib/libcrypto.dylib",
            "/opt/local/lib/libcrypto.dylib",
        ]
    found = ctypes.util.find_library("crypto")
    if found and not (sys.platform == "darwin" and found.startswith("/usr/lib/")):
        out.append(found)
    if sys.platform != "darwin":
        # Named sonames, for a container whose ldconfig cache `find_library` cannot read.
        out += ["libcrypto.so.3", "libcrypto.so.1.1", "libcrypto.so"]
    return out


def _build() -> Callable[[bytes, bytes, bytes], bool]:
    """Resolve libcrypto, bind the four symbols, and prove it verifies correctly."""
    cands = _candidates()
    if not cands:
        raise OSError("no loadable libcrypto candidate on this platform")
    lib = None
    errors = []
    for cand in cands:
        try:
            lib = ctypes.CDLL(cand)
            break
        except OSError as e:
            errors.append(f"{cand}: {e}")
    if lib is None:
        raise OSError("; ".join(errors) or "no libcrypto could be loaded")

    # Bind explicitly. Without argtypes, ctypes guesses from the Python values and a
    # 64-bit pointer can be truncated to int on some ABIs — which would show up as a
    # crash or a wrong answer rather than an exception.
    new_key = lib.EVP_PKEY_new_raw_public_key
    new_key.restype = ctypes.c_void_p
    new_key.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]

    ctx_new = lib.EVP_MD_CTX_new
    ctx_new.restype = ctypes.c_void_p
    ctx_new.argtypes = []

    verify_init = lib.EVP_DigestVerifyInit
    verify_init.restype = ctypes.c_int
    verify_init.argtypes = [ctypes.c_void_p] * 4 + [ctypes.c_void_p]

    # EVP_DigestVerify (one-shot), not EVP_DigestVerifyUpdate/Final: Ed25519 is a
    # one-shot algorithm in OpenSSL and the streaming calls fail on it.
    verify_oneshot = lib.EVP_DigestVerify
    verify_oneshot.restype = ctypes.c_int
    verify_oneshot.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                               ctypes.c_char_p, ctypes.c_size_t]

    ctx_free = lib.EVP_MD_CTX_free
    ctx_free.restype = None
    ctx_free.argtypes = [ctypes.c_void_p]

    key_free = lib.EVP_PKEY_free
    key_free.restype = None
    key_free.argtypes = [ctypes.c_void_p]

    def verify(message: bytes, signature: bytes, pub: bytes) -> bool:
        """Mirrors `signing.verify`'s contract exactly: False, never raising.

        The length guards come first and match the pure implementation's, so a
        malformed input is rejected identically by both rather than reaching OpenSSL
        and being rejected for a different reason.
        """
        if len(signature) != _ED25519_SIGNATURE_LEN:
            return False
        if len(pub) != _ED25519_PUBLIC_KEY_LEN:
            return False
        key = new_key(_NID_ED25519, None, pub, len(pub))
        if not key:
            # A public key OpenSSL will not load is not a valid key, which is the same
            # verdict the pure path reaches via `_decode_point` raising.
            return False
        ctx = ctx_new()
        if not ctx:
            key_free(key)
            return False
        try:
            if verify_init(ctx, None, None, None, key) != 1:
                return False
            # Exactly 1 is success. 0 is "did not verify"; negative is an operation
            # error. Both are False here — the caller gets the same answer the pure
            # implementation gives, and `audit_topic` reports it as signed-BAD.
            return verify_oneshot(ctx, signature, len(signature),
                                  message, len(message)) == 1
        finally:
            ctx_free(ctx)
            key_free(key)

    # Known-answer test: accept a valid RFC 8032 vector, reject a tampered one, reject
    # a wrong key. A binding that passes all three is doing real Ed25519.
    if not verify(_KAT_MSG, _KAT_SIG, _KAT_PUB):
        raise RuntimeError("libcrypto rejected a valid Ed25519 test vector")
    if verify(_KAT_MSG + b"x", _KAT_SIG, _KAT_PUB):
        raise RuntimeError("libcrypto accepted a tampered Ed25519 test vector")
    if verify(_KAT_MSG, _KAT_SIG, bytes(_ED25519_PUBLIC_KEY_LEN)):
        raise RuntimeError("libcrypto accepted an Ed25519 signature under a wrong key")
    return verify
