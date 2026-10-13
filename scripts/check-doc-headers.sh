#!/usr/bin/env bash
# Verify the docs/ convention's required headers are present.
#
# The headers are what make the convention legible: a spec without a Status is a spec nobody
# can tell is current, and a report without a "not verified" section is a report that only
# records good news. Templates (0000-*) and READMEs are exempt.
#
# Usage: scripts/check-doc-headers.sh [docs_dir]
set -uo pipefail

DOCS="${1:-docs}"
fail=0

# report <file> <message>
report() {
	printf '%s: %s\n' "$1" "$2" >&2
	fail=1
}

# require <file> <regex> <description>
require() {
	local file=$1 pattern=$2 desc=$3
	if ! grep -Eqi -- "$pattern" "$file"; then
		report "$file" "missing $desc"
	fi
}

# skip <file> -> true for templates and directory READMEs
skip() {
	local base
	base=$(basename "$1")
	[[ $base == README.md || $base == 0000-* ]]
}

check_dir() {
	local dir=$1
	shift
	[[ -d $dir ]] || return 0
	local file
	while IFS= read -r file; do
		skip "$file" && continue
		local spec
		for spec in "$@"; do
			require "$file" "${spec%%::*}" "${spec##*::}"
		done
	done < <(find "$dir" -maxdepth 1 -name '*.md' | sort)
}

# ADRs: Status, Date, Deciders. The deciders line is what makes a decision attributable.
check_dir "$DOCS/adrs" \
	'^[-* ]*\*\*Status:\*\*|^Status:::`Status:` line' \
	'^[-* ]*\*\*Date:\*\*|^Date:::`Date:` line' \
	'^[-* ]*\*\*Deciders:\*\*|^Deciders:::`Deciders:` line'

# Specs: Version (so corrections are traceable) and Status.
check_dir "$DOCS/specs" \
	'^Version:::`Version:` line' \
	'^Status:::`Status:` line'

# Reports: Status, the spec they measure against, and the honesty section.
check_dir "$DOCS/reports" \
	'^Status:::`Status:` line' \
	'^Spec:::`Spec:` line linking the spec it measures' \
	'not verified:::a "What is NOT verified" section'

if ((fail)); then
	printf '\ndoc header check failed. See %s/README.md for the required headers.\n' "$DOCS" >&2
	exit 1
fi

printf 'doc headers OK\n'
