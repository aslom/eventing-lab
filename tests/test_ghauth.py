"""GitHub sign-in: device flow, token -> login, and the approved-user list.

No test here touches the network. The device flow and `GET /user` are exercised
through injected fakes, because a test suite that reaches GitHub would be slow,
flaky, rate-limited, and would fail entirely on a machine without credentials.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import re
import sys

import pytest

from eventbridge import auth, ghauth
from eventbridge.config import Cfg as EbCfg

# ---- the approved-user list -------------------------------------------------

def test_parse_allowed_users_splits_and_lowercases():
    assert ghauth.parse_allowed_users("Alice, BOB ,carol") == {"alice", "bob", "carol"}


def test_parse_allowed_users_ignores_blanks():
    assert ghauth.parse_allowed_users(" , ,alice, ") == {"alice"}


def test_parse_allowed_users_empty_is_empty():
    assert ghauth.parse_allowed_users("") == frozenset()


def test_is_allowed_is_case_insensitive():
    """GitHub logins are case-insensitive, so Alice and alice are one account.
    Comparing case-sensitively would refuse a genuinely approved user."""
    allowed = ghauth.parse_allowed_users("mrsabath")
    assert ghauth.is_allowed("mrsabath", allowed)
    assert ghauth.is_allowed("MrSabath", allowed)
    assert ghauth.is_allowed("MRSABATH", allowed)


def test_is_allowed_denies_when_the_list_is_empty():
    """The other reading — "empty means everybody" — would turn a missing
    environment variable into an open door."""
    assert not ghauth.is_allowed("anyone", frozenset())


def test_is_allowed_denies_a_non_member():
    assert not ghauth.is_allowed("stranger", ghauth.parse_allowed_users("alice,bob"))


# ---- the login cache --------------------------------------------------------

class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_cache_returns_what_was_put():
    c = ghauth.LoginCache(300.0, now=FakeClock())
    c.put("tok", "alice")
    assert c.get("tok") == "alice"


def test_cache_misses_an_unknown_token():
    assert ghauth.LoginCache(300.0).get("nope") is None


def test_cache_entry_expires():
    clock = FakeClock()
    c = ghauth.LoginCache(300.0, now=clock)
    c.put("tok", "alice")
    clock.advance(299)
    assert c.get("tok") == "alice"
    clock.advance(2)
    assert c.get("tok") is None


def test_cache_never_stores_the_token_itself():
    """A memory dump or a careless log of the cache must not yield a credential."""
    c = ghauth.LoginCache(300.0)
    c.put("gho_supersecret", "alice")
    assert "gho_supersecret" not in repr(c.__dict__)
    assert all("gho_supersecret" not in k for k in c._entries)


def test_cache_separates_distinct_tokens():
    c = ghauth.LoginCache(300.0)
    c.put("tok-a", "alice")
    c.put("tok-b", "bob")
    assert (c.get("tok-a"), c.get("tok-b")) == ("alice", "bob")


# ---- resolve (cache + fetch) ------------------------------------------------

def test_resolve_uses_the_cache_and_does_not_refetch():
    calls = []

    def fetch(token):
        calls.append(token)
        return "alice", None

    c = ghauth.LoginCache(300.0)
    assert ghauth.resolve("tok", c, fetch=fetch) == ("alice", None)
    assert ghauth.resolve("tok", c, fetch=fetch) == ("alice", None)
    assert len(calls) == 1, "second call should have been served from the cache"


def test_resolve_does_not_cache_a_failure():
    """So a GitHub outage cannot pin a legitimate user to a failure for the whole
    TTL — each request retries rather than replaying a cached refusal.

    Note this does NOT speed up revocation: see
    `test_a_revoked_token_keeps_working_until_its_positive_entry_expires`."""
    c = ghauth.LoginCache(300.0)
    ghauth.resolve("tok", c, fetch=lambda t: (None, "GitHub rejected this token"))
    assert len(c) == 0


def test_a_revoked_token_keeps_working_until_its_positive_entry_expires():
    """The revocation window is the POSITIVE TTL, and it is worth pinning.

    A cache hit short-circuits `resolve()` before `fetch_login` runs, so a token
    revoked on GitHub keeps authenticating until its entry expires. Not caching
    failures does nothing for this — that only stops an outage pinning a
    legitimate user to a refusal.

    Bounded and configurable, and 300 s is a defensible trade against the
    5000/hour budget. But it is a real window, and a design doc claiming
    otherwise is worse than one that states it.
    """
    clock = FakeClock()
    cache = ghauth.LoginCache(300.0, now=clock)
    assert ghauth.resolve("tok", cache, fetch=lambda t: ("alice", None)) == ("alice", None)

    revoked = lambda t: (None, "GitHub rejected this token")  # noqa: E731
    assert ghauth.resolve("tok", cache, fetch=revoked)[0] == "alice", "cache hit"
    clock.advance(299)
    assert ghauth.resolve("tok", cache, fetch=revoked)[0] == "alice", "still inside the TTL"
    clock.advance(2)
    assert ghauth.resolve("tok", cache, fetch=revoked)[0] is None, "TTL expired -> refused"


def test_a_shorter_ttl_shortens_the_revocation_window():
    """The knob that actually controls it, so `EB_GITHUB_CACHE_TTL_S` is not
    mistaken for a pure performance setting."""
    clock = FakeClock()
    cache = ghauth.LoginCache(5.0, now=clock)
    ghauth.resolve("tok", cache, fetch=lambda t: ("alice", None))
    clock.advance(6)
    assert ghauth.resolve("tok", cache,
                          fetch=lambda t: (None, "GitHub rejected this token"))[0] is None


def test_resolve_works_without_a_cache():
    assert ghauth.resolve("tok", None, fetch=lambda t: ("bob", None)) == ("bob", None)


# ---- fetch_login error mapping ---------------------------------------------

class FakeHTTPError(Exception):
    def __init__(self, code):
        self.code = code


def _patched_fetch(monkeypatch, *, raises=None, body=None):
    import urllib.error

    class _Err(urllib.error.HTTPError):
        def __init__(self, code):
            self.code = code  # bypass HTTPError's heavy __init__

    def fake_urlopen(req, timeout=None):
        if raises is not None:
            raise _Err(raises)

        class _R:
            def read(self_inner):
                return json.dumps(body or {}).encode()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return _R()

    monkeypatch.setattr(ghauth.urllib.request, "urlopen", fake_urlopen)


def test_fetch_login_returns_the_login(monkeypatch):
    _patched_fetch(monkeypatch, body={"login": "mrsabath"})
    assert ghauth.fetch_login("tok") == ("mrsabath", None)


def test_fetch_login_401_is_reported_as_a_rejection(monkeypatch):
    _patched_fetch(monkeypatch, raises=401)
    login, err = ghauth.fetch_login("tok")
    assert login is None and "rejected" in err


def test_fetch_login_403_mentions_the_rate_limit(monkeypatch):
    _patched_fetch(monkeypatch, raises=403)
    login, err = ghauth.fetch_login("tok")
    assert login is None and "rate limit" in err


def test_fetch_login_500_is_not_reported_as_a_bad_credential(monkeypatch):
    """Telling a legitimate user "invalid credential" when GitHub was merely
    broken sends them debugging the wrong thing."""
    _patched_fetch(monkeypatch, raises=500)
    login, err = ghauth.fetch_login("tok")
    assert login is None
    assert "rejected" not in err and "500" in err


def test_fetch_login_network_failure_says_unreachable(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("dns go boom")
    monkeypatch.setattr(ghauth.urllib.request, "urlopen", boom)
    login, err = ghauth.fetch_login("tok")
    assert login is None and "cannot reach GitHub" in err


def test_fetch_login_missing_login_field(monkeypatch):
    _patched_fetch(monkeypatch, body={"id": 1})
    login, err = ghauth.fetch_login("tok")
    assert login is None and "no login" in err


# ---- device flow ------------------------------------------------------------

def _patch_post(monkeypatch, responses):
    """Serve queued JSON responses to `_post_form`, recording the URLs called."""
    seen = []

    def fake_post(url, fields, timeout):
        seen.append((url, fields))
        return responses.pop(0)

    monkeypatch.setattr(ghauth, "_post_form", fake_post)
    return seen


def test_start_device_flow_returns_the_codes(monkeypatch):
    seen = _patch_post(monkeypatch, [{
        "device_code": "dc", "user_code": "WDJB-MJHT",
        "verification_uri": ghauth.VERIFICATION_URL, "expires_in": 900, "interval": 5}])
    out = ghauth.start_device_flow("cid")
    assert out["user_code"] == "WDJB-MJHT"
    assert seen[0][0] == ghauth.DEVICE_CODE_URL


def test_start_device_flow_requests_no_scopes(monkeypatch):
    """Least access that answers "who is this": GET /user needs no scope."""
    seen = _patch_post(monkeypatch, [{"device_code": "dc", "user_code": "U"}])
    ghauth.start_device_flow("cid")
    assert seen[0][1]["scope"] == ""


def test_start_device_flow_explains_device_flow_disabled(monkeypatch):
    """The single most common setup mistake, so it gets its own message."""
    _patch_post(monkeypatch, [{"error": "device_flow_disabled"}])
    with pytest.raises(RuntimeError, match="Enable Device Flow"):
        ghauth.start_device_flow("cid")


def test_start_device_flow_reports_other_errors(monkeypatch):
    _patch_post(monkeypatch, [{"error": "unauthorized_client",
                               "error_description": "bad client"}])
    with pytest.raises(RuntimeError, match="unauthorized_client"):
        ghauth.start_device_flow("cid")


def test_poll_returns_the_token_once_authorised(monkeypatch):
    _patch_post(monkeypatch, [
        {"error": "authorization_pending"},
        {"error": "authorization_pending"},
        {"access_token": "gho_abc"},
    ])
    slept = []
    tok = ghauth.poll_for_token("cid", "dc", interval=5, sleep=slept.append)
    assert tok == "gho_abc"
    assert slept == [5.0, 5.0, 5.0]


def test_poll_honours_slow_down_by_adding_five_seconds(monkeypatch):
    """Polling faster than GitHub asks rate-limits the whole app, not just us."""
    _patch_post(monkeypatch, [
        {"error": "slow_down", "interval": 5},
        {"access_token": "gho_abc"},
    ])
    slept = []
    ghauth.poll_for_token("cid", "dc", interval=5, sleep=slept.append)
    assert slept == [5.0, 10.0]


def test_poll_raises_on_expired_token(monkeypatch):
    _patch_post(monkeypatch, [{"error": "expired_token"}])
    with pytest.raises(TimeoutError, match="expired"):
        ghauth.poll_for_token("cid", "dc", interval=1, sleep=lambda s: None)


def test_poll_raises_when_the_user_declines(monkeypatch):
    _patch_post(monkeypatch, [{"error": "access_denied"}])
    with pytest.raises(RuntimeError, match="declined"):
        ghauth.poll_for_token("cid", "dc", interval=1, sleep=lambda s: None)


def test_poll_gives_up_at_the_deadline(monkeypatch):
    _patch_post(monkeypatch, [{"error": "authorization_pending"}] * 50)
    clock = FakeClock()

    def sleep(dt):
        clock.advance(dt)

    with pytest.raises(TimeoutError):
        ghauth.poll_for_token("cid", "dc", interval=5, expires_in=20,
                              sleep=sleep, now=clock)


# ---- auth.resolve: the 401 / 403 distinction -------------------------------

def _env(token: str | None = None) -> dict:
    e = {"wsgi.input": io.BytesIO(b""), "REQUEST_METHOD": "POST"}
    if token:
        e["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return e


def _cfg(**kw) -> EbCfg:
    return EbCfg(tmpdir="/tmp/x", **kw)


def test_resolve_allows_anonymous_when_nothing_is_configured():
    ident, iss, status, why = auth.resolve(_env(), _cfg())
    assert (ident, iss, status, why) == (None, None, None, None)


def test_resolve_401_without_a_credential_when_github_is_on():
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"alice"}))
    ident, iss, status, why = auth.resolve(_env(), cfg)
    assert status == 401 and ident is None


def test_resolve_401_when_github_rejects_the_token():
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"alice"}))
    ident, iss, status, why = auth.resolve(
        _env("bad"), cfg, fetch=lambda t: (None, "GitHub rejected this token"))
    assert status == 401 and "rejected" in why


def test_resolve_403_for_a_real_user_who_is_not_approved():
    """The demo-worthy case: authenticated, and still refused."""
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"alice"}))
    ident, iss, status, why = auth.resolve(
        _env("gho_x"), cfg, fetch=lambda t: ("mallory", None))
    assert status == 403
    assert "mallory" in why, "the refusal should name the identity it refused"
    assert ident is None


def test_resolve_allows_an_approved_user_and_records_the_issuer():
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"mrsabath"}))
    ident, iss, status, why = auth.resolve(
        _env("gho_x"), cfg, fetch=lambda t: ("mrsabath", None))
    assert (ident, iss, status) == ("mrsabath", "github", None)


def test_resolve_is_case_insensitive_about_the_approved_login():
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"mrsabath"}))
    ident, _, status, _ = auth.resolve(
        _env("gho_x"), cfg, fetch=lambda t: ("MrSabath", None))
    assert status is None and ident == "MrSabath"


def test_break_glass_does_not_pay_a_doomed_github_call():
    """Static tokens are checked BEFORE GitHub, so the fallback is fastest exactly
    when it is needed. The previous order meant every break-glass request during
    an outage paid a full fetch_login timeout on a call that could never succeed,
    against the same API budget the cache exists to protect."""
    calls = []

    def fetch(token):
        calls.append(token)
        return None, "cannot reach GitHub: OSError"

    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"alice"}),
               auth_tokens={"emergency": "operator"})
    ident, iss, status, _ = auth.resolve(_env("emergency"), cfg, fetch=fetch)
    assert (ident, iss, status) == ("operator", None, None)
    assert calls == [], "a static token must not trigger a GitHub lookup"


def test_a_github_token_is_unaffected_by_the_static_first_order():
    """The reorder must not shadow real sign-in."""
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"mrsabath"}),
               auth_tokens={"emergency": "operator"})
    ident, iss, status, _ = auth.resolve(
        _env("gho_real"), cfg, fetch=lambda t: ("mrsabath", None))
    assert (ident, iss, status) == ("mrsabath", "github", None)


def test_a_static_token_still_works_as_a_break_glass_credential():
    """An operator keeping one alongside GitHub sign-in should not be locked out
    when GitHub is unreachable."""
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"alice"}),
               auth_tokens={"emergency": "operator"})
    ident, iss, status, _ = auth.resolve(
        _env("emergency"), cfg, fetch=lambda t: (None, "cannot reach GitHub: OSError"))
    assert (ident, iss, status) == ("operator", None, None)


def test_static_token_path_reports_no_issuer():
    """Absence of an issuer is the signal that this identity was not verified."""
    cfg = _cfg(auth_tokens={"tok": "alice"})
    ident, iss, status, _ = auth.resolve(_env("tok"), cfg)
    assert (ident, iss, status) == ("alice", None, None)


def test_resolve_401_for_a_wrong_static_token():
    cfg = _cfg(auth_tokens={"tok": "alice"})
    _, _, status, _ = auth.resolve(_env("wrong"), cfg)
    assert status == 401


def test_github_on_with_only_an_allowed_list_still_enforces():
    """Setting the list but forgetting the client id must not silently allow."""
    cfg = _cfg(allowed_users=frozenset({"alice"}))
    _, _, status, _ = auth.resolve(_env(), cfg)
    assert status == 401


def test_the_cache_means_one_github_call_for_repeated_requests():
    calls = []
    cfg = _cfg(github_client_id="cid", allowed_users=frozenset({"alice"}))
    cache = ghauth.LoginCache(300.0)

    def fetch(token):
        calls.append(token)
        return "alice", None

    for _ in range(5):
        ident, _, status, _ = auth.resolve(_env("gho_x"), cfg, cache=cache, fetch=fetch)
        assert status is None and ident == "alice"
    assert len(calls) == 1


# ---- scripts/login.py: the CLI that was missing ----------------------------
#
# `start_device_flow` and `poll_for_token` were implemented and tested from Phase 2
# and nothing called them, so the device flow existed and was unreachable: the only
# way to get a token for the signed demo was a PAT, which is a different credential.
# These cover the CLI's own decisions, none of which need GitHub.

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import login as L  # noqa: E402

FAKE_TOKEN = "gho_token_that_is_not_real"


def _no_sleep(monkeypatch):
    """Make `poll_for_token`'s pacing free.

    It takes `sleep`/`now` as parameters for exactly this, but their defaults bind
    `time.sleep` at def time, so patching `ghauth.time.sleep` does not reach it — and
    `interval: 0` does not help either, because `float(interval or DEFAULT_INTERVAL_S)`
    reads 0 as falsy and falls back to 5 s. Without this these tests really sleep.

    The pacing itself stays covered by the `poll_for_token` tests above, which drive it
    directly with injected `sleep`/`now`. These tests are about the CLI.
    """
    real_poll = ghauth.poll_for_token
    monkeypatch.setattr(
        ghauth, "poll_for_token",
        lambda *a, **kw: real_poll(*a, **{**kw, "sleep": lambda s: None}))


def _fake_github(monkeypatch, *, pending=1, token=FAKE_TOKEN, login="mrsabath",
                 device_resp=None):
    """Drive the CLI with no network. Returns the list of URLs posted to."""
    calls: list[str] = []

    def post(url, fields, timeout):
        calls.append(url)
        if url == ghauth.DEVICE_CODE_URL:
            return device_resp or {
                "device_code": "dc1", "user_code": "WXYZ-1234",
                "verification_uri": "https://github.com/login/device", "interval": 0}
        if len([c for c in calls if c == ghauth.ACCESS_TOKEN_URL]) <= pending:
            return {"error": "authorization_pending"}
        return {"access_token": token}

    monkeypatch.setattr(ghauth, "_post_form", post)
    monkeypatch.setattr(ghauth, "fetch_login",
                        lambda t, timeout=5.0: (login, None))
    # Wrap `poll_for_token` to inject a no-op clock. It takes `sleep`/`now` as
    # parameters for exactly this, but their defaults bind `time.sleep` at def time,
    # so patching `ghauth.time.sleep` does not reach it — and `interval: 0` does not
    # help either, because `float(interval or DEFAULT_INTERVAL_S)` reads 0 as falsy
    # and falls back to 5 s. Without this these tests really sleep: 10 s each.
    #
    # The pacing itself stays tested by the `poll_for_token` tests above, which drive
    # it directly with injected `sleep`/`now`; these tests are about the CLI.
    _no_sleep(monkeypatch)
    return calls


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = L.main(argv)
    return rc, out.getvalue(), err.getvalue()


def test_the_token_goes_to_stdout_alone(monkeypatch):
    """So `export T=$(python3 scripts/login.py ...)` captures the token and nothing
    else. Every human-facing line has to be on stderr for that to hold."""
    _fake_github(monkeypatch)
    rc, out, err = _run(["--client-id", "Ov23liFAKE"])
    assert rc == 0
    assert out == FAKE_TOKEN + "\n", f"stdout must be the token alone, got {out!r}"
    assert FAKE_TOKEN not in err, "the token must not be duplicated onto stderr"


def test_the_user_code_is_shown_to_the_human(monkeypatch):
    """The code is the only thing the user has to act on; it goes on stderr with the
    URL, and it must be printed BEFORE any browser launch so a failed launch still
    leaves it on screen."""
    _fake_github(monkeypatch)
    rc, _, err = _run(["--client-id", "Ov23liFAKE"])
    assert rc == 0
    assert "WXYZ-1234" in err
    assert "github.com/login/device" in err


def test_export_mode_emits_both_variables(monkeypatch):
    """The login is what `--gh-login` needs, and asking the user to retype it from a
    log line is how the identity beat gets run against the wrong account."""
    _fake_github(monkeypatch, login="mrsabath")
    rc, out, _ = _run(["--client-id", "Ov23liFAKE", "--export"])
    assert rc == 0
    assert f"export DEMO_GH_TOKEN={FAKE_TOKEN}" in out
    assert "export DEMO_GH_LOGIN=mrsabath" in out


def test_a_failed_login_lookup_warns_that_the_identity_beat_will_skip(monkeypatch):
    """The quiet failure this guards. `fetch_login` failing is not fatal for the token,
    and the command still exits 0 — correctly, printing the token is its job. But
    `--export` then emits DEMO_GH_TOKEN with NO DEMO_GH_LOGIN, the documented
    invocation passes `--gh-login ""`, and `assert_identity_gate` SKIPS while every
    other stage authenticates fine. A green sweep that silently did not run the
    identity beat is the exact shape of the bugs this effort keeps finding — so the
    warning has to name the consequence, not just the lookup failure."""
    _fake_github(monkeypatch)
    monkeypatch.setattr(ghauth, "fetch_login",
                        lambda t, timeout=5.0: (None, "HTTP Error 403: rate limited"))
    rc, out, err = _run(["--client-id", "Ov23liFAKE", "--export"])

    assert rc == 0, "a token that works is still a success"
    assert f"export DEMO_GH_TOKEN={FAKE_TOKEN}" in out
    assert "DEMO_GH_LOGIN" not in out, "there is no login to export"
    assert "rate limited" in err, "say why the lookup failed"
    assert "SKIP" in err and "--gh-login" in err, \
        "the warning must name the consequence and the remedy, on stderr"
    assert FAKE_TOKEN not in err, "the token still must not reach stderr"


def test_the_flow_warns_when_a_token_has_no_login_on_an_auth_overlay():
    """The mirror, on the side that actually skips. `main()` already warns for a login
    with no token; the reverse is the quieter half, because every ordinary stage
    authenticates and only the newest beat goes missing."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "k8s_demo_flow.py").read_text()
    main = src[src.index("def main("):]
    assert "args.gh_login" in main and "kind-signed" in main, \
        "the token-without-login case must be detected for auth-enabled overlays"
    warn = main[main.index("install_default_credential"):]
    warn = warn[:warn.index("f.stage0()")]
    assert "SKIP" in warn, \
        "the note must say the identity beat will skip, not merely that a flag is unset"


def test_no_browser_is_opened_by_default(monkeypatch):
    """§2.2's decision, kept: auto-opening fails silently over SSH and inside a
    container, which is where this is most often run. `--open` is opt-in."""
    opened = []
    monkeypatch.setattr(L.webbrowser, "open", lambda u: opened.append(u) or True)
    _fake_github(monkeypatch)
    _run(["--client-id", "Ov23liFAKE"])
    assert opened == [], "a browser must not open without --open"


def test_open_launches_the_verification_page(monkeypatch):
    opened = []
    monkeypatch.setattr(L.webbrowser, "open", lambda u: opened.append(u) or True)
    _fake_github(monkeypatch)
    _run(["--client-id", "Ov23liFAKE", "--open"])
    assert opened == ["https://github.com/login/device"]


def test_a_browser_that_will_not_launch_is_not_fatal(monkeypatch):
    """The code and URL are already printed, so a failed launch is a message, not an
    error — the user can still finish by hand."""
    monkeypatch.setattr(L.webbrowser, "open",
                        lambda u: (_ for _ in ()).throw(OSError("no display")))
    _fake_github(monkeypatch)
    rc, out, err = _run(["--client-id", "Ov23liFAKE", "--open"])
    assert rc == 0 and out.strip() == FAKE_TOKEN
    assert "manually" in err


def test_the_forgotten_device_flow_checkbox_gets_its_own_message(monkeypatch):
    """The most common setup mistake. A generic "GitHub refused" would send the
    operator to the wrong page."""
    _fake_github(monkeypatch, device_resp={"error": "device_flow_disabled"})
    rc, out, err = _run(["--client-id", "Ov23liFAKE"])
    assert rc == 1 and out == ""
    assert "Enable Device Flow" in err


def test_a_declined_sign_in_exits_non_zero(monkeypatch):
    def post(url, fields, timeout):
        if url == ghauth.DEVICE_CODE_URL:
            return {"device_code": "d", "user_code": "U", "interval": 0}
        return {"error": "access_denied"}
    monkeypatch.setattr(ghauth, "_post_form", post)
    _no_sleep(monkeypatch)
    rc, out, err = _run(["--client-id", "Ov23liFAKE"])
    assert rc == 1 and out == ""
    assert "declined" in err


def test_no_scopes_are_requested_by_the_cli_path(monkeypatch):
    """Through the CLI, not just through `start_device_flow` directly: an unscoped
    token reads the login and cannot read a repository even if it leaks."""
    seen = {}

    def post(url, fields, timeout):
        if url == ghauth.DEVICE_CODE_URL:
            seen.update(fields)
            return {"device_code": "d", "user_code": "U", "interval": 0}
        return {"access_token": FAKE_TOKEN}

    monkeypatch.setattr(ghauth, "_post_form", post)
    monkeypatch.setattr(ghauth, "fetch_login", lambda t, timeout=5.0: ("x", None))
    _no_sleep(monkeypatch)
    _run(["--client-id", "Ov23liFAKE"])
    assert seen.get("scope") == "", f"scope must be empty, got {seen.get('scope')!r}"


def test_the_cli_writes_no_token_to_disk():
    """There is deliberately no token cache: a file holding a GitHub credential is a
    thing to leak, and the flow takes ten seconds to repeat."""
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "login.py").read_text()
    assert not re.search(r"\.write_text\(|\.write_bytes\(|open\([^)]*['\"]w", src), \
        "login.py must not write the token anywhere"


def test_the_client_id_is_required_but_is_not_treated_as_a_secret():
    """The device flow uses no client secret, which is why the id is a plain argument
    and why a `--client-secret` flag would be a design error."""
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "login.py").read_text()
    assert "--client-secret" not in src
    with contextlib.redirect_stderr(io.StringIO()):
        with pytest.raises(SystemExit):
            L.main([])
