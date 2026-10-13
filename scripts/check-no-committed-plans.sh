#!/usr/bin/env bash
# Fail if anything but README.md is committed under docs/plans/.
#
# The .gitignore rule is advisory -- one `git add -f` defeats it, and plans have a habit of
# arriving in a PR that is mostly about something else. This makes the local-only rule real.
set -uo pipefail

offenders=$(git ls-files docs/plans/ | grep -v '^docs/plans/README\.md$' || true)

if [[ -n $offenders ]]; then
	cat >&2 <<-EOF
		Committed files found under docs/plans/:

		$offenders

		Plans are local-only work artifacts: write -> execute -> delete. Before removing a plan,
		move anything durable out of it:
		  - a decision you made      -> docs/adrs/
		  - a design detail          -> edit the relevant docs/specs/ doc
		  - a number you measured    -> docs/reports/

		See docs/plans/README.md.
	EOF
	exit 1
fi

printf 'no committed plans\n'
