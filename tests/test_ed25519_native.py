"""The native Ed25519 fast path must give the SAME answer as the pure reference.

`signing.verify` prefers libcrypto when it loads, because the pure implementation costs
183-201 ms per verification on the x86 nodes this deploys to and `audit_topic.py` spent
93 of 99 seconds there. The speedup is worth having; a fast path that silently disagrees
with the reference is not. It would turn the audit's verdicts into noise while making
them arrive faster — the exact failure that tool's verdict vocabulary exists to prevent.

So these tests pin three properties:

  1. **Agreement.** Both implementations return the same bool on valid, tampered,
     wrong-key, truncated and over-long input.
  2. **The fallback still works.** With the native path forced off, verification is
     unchanged — this is not hypothetical, since `cryptography`/`pynacl` are absent by
     design and macOS refuses to load its own `/usr/lib/libcrypto.dylib`.
  3. **No false accepts.** Anything the fast path accepts, the pure path accepts too.

They are written to pass whether or not libcrypto is present, because CI, a laptop and
the runner image genuinely differ. Where a test needs the native path it skips without
it, and `test_the_pure_path_is_always_exercised` has no such escape.
"""
import importlib
import secrets

import pytest

from shared import ed25519_native, signing


def _pure_verify(message: bytes, signature: bytes, pub: bytes) -> bool:
    """`signing.verify` with the native path forced off, restored afterwards.

    Reaches into the module's cache rather than monkeypatching `ctypes`, so what runs
    is exactly the code an environment without libcrypto would run.
    """
    saved = (ed25519_native._cached, ed25519_native._tried, ed25519_native._reason)
    ed25519_native._cached = None
    ed25519_native._tried = True
    ed25519_native._reason = "disabled by test"
    try:
        return signing.verify(message, signature, pub)
    finally:
        (ed25519_native._cached, ed25519_native._tried,
         ed25519_native._reason) = saved


def _native_or_skip():
    fn = ed25519_native.load()
    if fn is None:
        pytest.skip(f"no libcrypto here: {ed25519_native.unavailable_reason()}")
    return fn


@pytest.fixture
def signed():
    """A real signed event, its canonical signing input, and the key that signed it."""
    from shared import ce
    seed = bytes(range(32))
    pub = signing.public_key(seed)
    ev = ce.new_event(type=ce.TYPE_RESPONSE, source="test",
                      correlationid="test-otter-0001", sequence=1, phase="result",
                      final=True, data={"text": "hello"})
    token = signing.sign_event(ev, seed, kid="k1")
    protected, _, sig_b64 = token.split(".")
    import base64
    payload = base64.urlsafe_b64encode(
        signing.canonical(ev.attrs, ev.data)).rstrip(b"=").decode()
    msg = f"{protected}.{payload}".encode()
    sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    return msg, sig, pub


# ---- 1. the two implementations agree ---------------------------------------

def test_both_implementations_accept_a_genuine_signature(signed):
    msg, sig, pub = signed
    native = _native_or_skip()
    assert native(msg, sig, pub) is True
    assert _pure_verify(msg, sig, pub) is True


@pytest.mark.parametrize("what", ["message", "signature", "key"])
def test_both_implementations_reject_the_same_tampering(signed, what):
    msg, sig, pub = signed
    native = _native_or_skip()
    if what == "message":
        msg = msg + b"x"
    elif what == "signature":
        sig = sig[:-1] + bytes([sig[-1] ^ 0x01])
    else:
        pub = bytes(32)
    assert native(msg, sig, pub) is False
    assert _pure_verify(msg, sig, pub) is False


@pytest.mark.parametrize("sig_len", [0, 1, 63, 65, 128])
def test_both_implementations_reject_a_wrong_length_signature(signed, sig_len):
    """The length guards are duplicated in the native module on purpose, so malformed
    input is refused for the same reason by both rather than reaching OpenSSL."""
    msg, _sig, pub = signed
    native = _native_or_skip()
    bad = bytes(sig_len)
    assert native(msg, bad, pub) is False
    assert _pure_verify(msg, bad, pub) is False


@pytest.mark.parametrize("pub_len", [0, 31, 33])
def test_both_implementations_reject_a_wrong_length_key(signed, pub_len):
    msg, sig, _pub = signed
    native = _native_or_skip()
    bad = bytes(pub_len)
    assert native(msg, sig, bad) is False
    assert _pure_verify(msg, sig, bad) is False


def test_the_two_agree_on_random_garbage():
    """Fuzz the disagreement case directly. Random input is overwhelmingly invalid, so
    this is really "neither implementation accepts noise, and neither crashes on it"."""
    native = _native_or_skip()
    for _ in range(50):
        msg = secrets.token_bytes(secrets.randbelow(64))
        sig = secrets.token_bytes(64)
        pub = secrets.token_bytes(32)
        assert native(msg, sig, pub) == _pure_verify(msg, sig, pub)


def test_nothing_the_fast_path_accepts_is_rejected_by_the_reference(signed):
    """The asymmetric property that actually matters: a false ACCEPT in the fast path
    would let a forgery audit as signed-ok. A false reject is merely a bug."""
    msg, sig, pub = signed
    native = _native_or_skip()
    cases = [
        (msg, sig, pub),
        (msg + b"x", sig, pub),
        (msg, sig, bytes(32)),
        (b"", sig, pub),
    ]
    for m, s, p in cases:
        if native(m, s, p):
            assert _pure_verify(m, s, p), (
                "the native path accepted something the reference rejects")


# ---- 2. the fallback is real ------------------------------------------------

def test_the_pure_path_is_always_exercised(signed):
    """No skip: this must hold everywhere, because it is what makes the stdlib-only
    constraint true on a machine with no loadable libcrypto."""
    msg, sig, pub = signed
    assert _pure_verify(msg, sig, pub) is True
    assert _pure_verify(msg + b"x", sig, pub) is False


def test_an_unloadable_library_falls_back_rather_than_raising(monkeypatch, signed):
    """A missing or broken libcrypto must degrade, not fail. Simulated by pointing the
    override at a path that cannot be a library."""
    msg, sig, pub = signed
    mod = importlib.reload(ed25519_native)
    monkeypatch.setenv("ED25519_LIBCRYPTO", "/nonexistent/libcrypto.so")
    assert mod.load() is None
    assert mod.unavailable_reason()          # says why, for a one-line log
    # And `signing.verify` still works through the pure path.
    importlib.reload(ed25519_native)
    assert _pure_verify(msg, sig, pub) is True


def test_round_trip_through_the_public_api_agrees(signed):
    """End to end through `verify_with_keyset`, which is what the audit calls."""
    from shared import ce, keyset
    seed = bytes(range(32))
    ev = ce.new_event(type=ce.TYPE_RESPONSE, source="test",
                      correlationid="test-otter-0002", sequence=1, phase="result",
                      final=True, data={"text": "hi"})
    ev.attrs["signature"] = signing.sign_event(ev, seed, kid="k1")
    ks = keyset.KeySet({"k1": signing.public_key(seed)})

    ok_fast, _ = signing.verify_with_keyset(ev, ks)
    saved = (ed25519_native._cached, ed25519_native._tried)
    ed25519_native._cached, ed25519_native._tried = None, True
    try:
        ok_pure, _ = signing.verify_with_keyset(ev, ks)
    finally:
        ed25519_native._cached, ed25519_native._tried = saved
    assert ok_fast == ok_pure is True


# ---- 3. free-threading safety ----------------------------------------------

def test_concurrent_verification_is_thread_safe(signed):
    """Every call allocates its own EVP_PKEY and EVP_MD_CTX and shares nothing mutable,
    so concurrent use is safe on the free-threaded interpreter this project runs
    (`Py_GIL_DISABLED=1`, asserted at image build time). ctypes releases the GIL around
    the foreign call, so these genuinely run in parallel rather than interleaving.
    """
    import concurrent.futures as cf
    msg, sig, pub = signed
    native = _native_or_skip()
    bad = msg + b"x"
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        good = list(ex.map(lambda _: native(msg, sig, pub), range(200)))
        wrong = list(ex.map(lambda _: native(bad, sig, pub), range(200)))
    assert all(good), "a concurrent verification of a valid signature failed"
    assert not any(wrong), "a concurrent verification accepted a tampered message"
