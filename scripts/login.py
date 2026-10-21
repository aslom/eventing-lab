#!/usr/bin/env python3
"""Sign in to GitHub with the device flow and print an access token. §2.2.

The two halves of this have existed in `eventbridge/ghauth.py` since Phase 2 —
`start_device_flow` and `poll_for_token`, both tested — and nothing called them. So the
device flow was implemented and unreachable: the only way to get a token for the signed
demo was a Personal Access Token, which is a different credential with different
properties and is not what a user would ever do.

This is that CLI. It exists for the KubeCon identity beat (§8.3 of the demo script),
where "an unauthenticated request is ignored, then I sign in and it works" is the point,
and a PAT pasted from a settings page does not show that.

**Why the device flow and not a web redirect.** A redirect needs this service to host a
callback at a URL GitHub can reach. The demo runs on a laptop behind a VPN, so that is
precisely what it cannot do. The device flow inverts it: we ask GitHub for a code, the
user types the code into a page **GitHub already hosts**, and we poll. Nothing needs to
reach us. It also needs **no client secret**, which is why the client id is an argument
and not a credential.

**No scopes are requested.** `GET /user` returns the login for an unscoped token, so
this asks for the least access that answers "who is this" — the token cannot read a
repository even if it leaks. Worth saying out loud when demoing.

**The token is printed, and that is the whole output.** It is a live credential, so:

  * it goes to **stdout alone**, with every human-facing line on stderr, so
    `export DEMO_GH_TOKEN=$(python3 scripts/login.py --client-id ...)` captures the
    token and nothing else;
  * `--export` prints a ready-to-eval line instead, for the common case;
  * nothing is written to disk. There is no token cache here on purpose — a file holding
    a GitHub credential is a thing to leak, and the flow takes ten seconds to repeat.

  python3 scripts/login.py --client-id Ov23li...
  python3 scripts/login.py --client-id Ov23li... --open      # launch a browser
  eval "$(python3 scripts/login.py --client-id Ov23li... --export)"
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import webbrowser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from eventbridge import ghauth  # noqa: E402

PREFIX = "login"


def _log(msg: str) -> None:
    """Human-facing output goes to STDERR, so stdout carries only the token."""
    print(f"[{PREFIX}] {msg}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sign in to GitHub with the device flow; print an access token.")
    ap.add_argument("--client-id", required=True,
                    help="OAuth App client id, with Device Flow enabled on the app. "
                         "NOT a secret: the device flow uses no client secret.")
    ap.add_argument("--open", action="store_true", dest="open_browser",
                    help="open the verification page in a browser. Off by default "
                         "because auto-opening fails silently over SSH and inside a "
                         "container, which is where this is most often run (§2.2).")
    ap.add_argument("--export", action="store_true",
                    help="print `export DEMO_GH_TOKEN=...` instead of the bare token, "
                         "for use with eval")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="give up after this many seconds (default 900, GitHub's own "
                         "device-code lifetime)")
    args = ap.parse_args(argv)

    try:
        start = ghauth.start_device_flow(args.client_id)
    except RuntimeError as e:
        # `start_device_flow` already gives `device_flow_disabled` its own message,
        # because forgetting that checkbox is the most common setup mistake.
        _log(f"could not start sign-in: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 — urllib raises a wide family
        _log(f"could not reach GitHub: {e.__class__.__name__}: {e}")
        return 1

    code = start["user_code"]
    uri = start.get("verification_uri") or ghauth.VERIFICATION_URL

    # The code is the thing the human needs, so it gets its own lines rather than
    # being buried in a sentence. Printed BEFORE any browser opens: if the launch
    # fails — or opens behind the terminal — the code is already on screen.
    _log("")
    _log(f"  1. open  {uri}")
    _log(f"  2. enter the code   {code}")
    _log("")

    if args.open_browser:
        try:
            opened = webbrowser.open(uri)
        except Exception:  # noqa: BLE001 — platform-specific launch failures
            opened = False
        _log("opened your browser" if opened else
             f"could not open a browser — go to {uri} manually")

    _log("waiting for you to authorise (Ctrl-C to give up)...")
    try:
        token = ghauth.poll_for_token(
            args.client_id, start["device_code"],
            interval=start.get("interval"), expires_in=args.timeout)
    except KeyboardInterrupt:
        _log("cancelled")
        return 130
    except TimeoutError as e:
        _log(str(e))
        return 1
    except RuntimeError as e:
        _log(str(e))
        return 1
    except Exception as e:  # noqa: BLE001
        _log(f"sign-in failed: {e.__class__.__name__}: {e}")
        return 1

    # Who the token belongs to, so the caller does not have to guess the login the
    # identity beat asserts on. A failure here is not fatal — the token is valid
    # regardless, and printing it is this command's job.
    login, err = ghauth.fetch_login(token)
    if login:
        _log(f"signed in as {login}")
        _log(f"  EB_ALLOWED_USERS and ER_ALLOWED_SUBMITTERS must include {login!r}")
        _log(f"  pass --gh-login {login} to scripts/k8s_demo_flow.py")
    else:
        # Not fatal: the token is valid and printing it is this command's job. But say
        # plainly what is missing, because `--export` then emits DEMO_GH_TOKEN without
        # DEMO_GH_LOGIN — and `assert_identity_gate` SKIPS on an empty `--gh-login`
        # while every other stage authenticates fine. A run that silently did not
        # exercise the identity beat still reports success.
        _log(f"signed in, but could not read the login back: {err}")
        _log("  WARNING: no DEMO_GH_LOGIN will be printed. Pass --gh-login <login> to "
             "scripts/k8s_demo_flow.py by hand, or the identity beat will SKIP and "
             "the flow will still report success.")

    # stdout, alone, so command substitution captures exactly this.
    if args.export:
        print(f"export DEMO_GH_TOKEN={token}")
        if login:
            print(f"export DEMO_GH_LOGIN={login}")
    else:
        print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
