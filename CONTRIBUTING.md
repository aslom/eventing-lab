# Contributing to <PROJECT>

The **contribution process** — how to claim an issue, how review works, how to become a
maintainer, and the code of conduct — is documented once for the whole project in
[`rossoctl/rossoctl`](https://github.com/rossoctl/rossoctl/blob/main/CONTRIBUTING.md). See
[`GOVERNANCE.md`](GOVERNANCE.md) for the full index.

This file covers only what is **specific to this repo**: how to build it, test it, and what
its conventions are.

## Prerequisites

<!-- Replace with this repo's real prerequisites. -->

- <language/toolchain and minimum version>
- [pre-commit](https://pre-commit.com/#install) (git hooks)

## Setup

```sh
git clone https://github.com/rossoctl/<PROJECT>.git
cd <PROJECT>

# Install the git hooks. The commit-msg hook is required: it rewrites AI
# Co-Authored-By trailers to the project's Assisted-By convention.
pre-commit install
pre-commit install --hook-type commit-msg

# <build / dependency install command>
```

## Test and lint

```sh
# <test command>
# <lint command>

# The docs/ convention's own gates (CI runs these too):
./scripts/check-no-committed-plans.sh
./scripts/check-doc-headers.sh
```

## Documentation

This repo uses the rossoctl documentation convention. **If you are adding or changing a
document, read [`docs/README.md`](docs/README.md) first** — it decides where each kind of
document goes. The three rules that most often catch people out:

1. **Never commit a plan.** `docs/plans/` is local-only and CI enforces it. Move anything
   durable into a spec, ADR or report before deleting your plan.
2. **Specs are edited when reality corrects them; reports never are.** A report is
   append-only — it is the evidence of what actually happened, including wrong predictions.
3. **ADRs are immutable.** Supersede with a new one rather than editing.

## Commits

- Conventional-commit style subject (`feat:`, `fix:`, `docs:`, `chore:`).
- The trailer is **`Assisted-By:`**, not `Co-Authored-By:`. The `commit-msg` hook rewrites it
  for you, which is why installing hooks is not optional.

---

_Assisted-By: Claude Code_
