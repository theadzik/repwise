# Commit messages and releases

Commits are written in [Conventional
Commits](https://www.conventionalcommits.org/en/v1.0.0/) form, and the version
number is derived from them by
[Commitizen](https://commitizen-tools.github.io/commitizen/).

The work is split in two, deliberately:

| When | What runs | What it does |
| --- | --- | --- |
| Every commit | `cz check`, via pre-commit's `commit-msg` hook | Rejects a message that is not conventional |
| At release time | `cz bump`, by hand | Reads every commit since the last tag, picks the new version, writes it, updates the changelog, commits and tags |

A normal commit never changes the version. That is not a limitation of the
tooling - see [why not bump on every
commit](#why-not-bump-on-every-commit).

- [Setup](#setup)
- [Commit message format](#commit-message-format)
- [What each type does](#what-each-type-does)
- [Cutting a release](#cutting-a-release)
- [Why not bump on every commit](#why-not-bump-on-every-commit)
- [Gotchas](#gotchas)

## Setup

The hook is not active in a fresh clone until it is installed - the config is
version controlled, but git hooks are per clone:

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install
```

`default_install_hook_types` in `.pre-commit-config.yaml` is what makes the bare
`install` enough. Without it, `pre-commit install` wires up the `pre-commit`
stage alone - which never sees a commit message - and this check would silently
never run.

To stop it: `.venv/bin/pre-commit uninstall`. See
[contributing](contributing.md#checks) for the other hooks.

## Commit message format

```text
type(scope)!: summary

optional body

BREAKING CHANGE: what a user has to change.
```

Only `type` and `summary` are required. The scope is free-form; useful ones here
are module names - `progression`, `config`, `cli`, `payloads`.

```text
feat(cli): add --since to update older sessions
fix(progression): stop a deload rebasing the target upward
docs: document the release process
build(deps): bump garminconnect to 0.3.8
refactor(planner)!: return a Plan instead of mutating payloads
```

A rejected message looks like this, and the commit does not happen:

```text
commitizen check.........................................................Failed
- hook id: commitizen
- exit code: 14

commit validation: failed!
please enter a commit message in the commitizen format.
```

Nothing is lost when that happens - fix the message and commit again. If you
were mid-`git commit` with an editor, the text is still in
`.git/COMMIT_EDITMSG`.

## What each type does

The type decides the release. These are the accepted types and their effect on
the next `cz bump`:

| Type | Version effect | In the changelog |
| --- | --- | --- |
| `feat` | minor | yes |
| `fix` | patch | yes |
| `refactor` | patch | yes |
| `perf` | patch | yes |
| `docs`, `test`, `chore`, `ci`, `build`, `style`, `revert` | none | no |
| any type with `!`, or a `BREAKING CHANGE:` footer | major | yes, in its own section |

`refactor` and `perf` counting as a patch is worth knowing: a change with no
user-visible effect still produces a release.

While the version is below 1.0.0, `major_version_zero = true` in
`pyproject.toml` holds a breaking change to a **minor** bump, so `!` gives
0.1.0 → 0.2.0 rather than 1.0.0. Delete that line when releasing 1.0.0.

## Cutting a release

```bash
.venv/bin/cz bump --dry-run    # what would happen, changing nothing
.venv/bin/cz bump              # do it
git push --follow-tags
```

`--follow-tags` matters. A plain `git push` sends the bump commit and leaves the
tag behind, so the release exists locally and nowhere else.

With nothing worth releasing since the last tag, `cz bump` changes nothing and
says so - `NO_COMMITS_FOUND` when there are no commits at all,
`NO_COMMITS_TO_BUMP` when there are but none of them earn a release. The second
case prints a misleading `bump: version 0.1.0 → 0.1.0` line first; it is not
about to re-cut the existing tag, and the refusal on the next line is the real
answer.

One bump touches four things:

| What | How |
| --- | --- |
| `[project].version` in `pyproject.toml` | `version_provider = "pep621"`, so this is the single source of truth |
| `__version__` in `src/workout/__init__.py` | Listed in `version_files`, kept in step |
| `CHANGELOG.md` | Prepended, grouped by type |
| A commit and a tag | `bump: version 0.1.0 → 0.2.0`, tagged `0.2.0` |

Tags carry no `v` prefix - `0.2.0`, not `v0.2.0`. That is Commitizen's default
and matches the 0.1.0 tag cut by hand before any of this existed, so
`tag_format` is set in `pyproject.toml` only to pin it against an upstream
change of default. Switching to `v` later means setting
`tag_format = "v$version"` and listing the old form in `legacy_tag_formats`, so
the existing tag stays readable; there is no reason to.

To override the computed version - a release that deserves a minor bump
although every commit was a `fix`, say:

```bash
.venv/bin/cz bump --increment MINOR
.venv/bin/cz bump 1.0.0            # or state the version outright
```

## Why not bump on every commit

A `commit-msg` hook that bumped the version and tagged would be the obvious
thing to reach for. It does not work, for four separate reasons:

1. **The hook stages are in the wrong order.** By the time a hook can read the
   commit message, git has already written the tree for the commit. Editing
   `pyproject.toml` in a `commit-msg` hook leaves a dirty working tree, not a
   bumped version inside the commit. The one stage that *can* change staged
   files, `pre-commit`, runs before the message exists - so it cannot know
   whether this is a `feat` or a `fix`.
2. **Not every commit is a release.** A branch with one `feat` and two `fix`
   commits would walk 0.2.0, 0.2.1, 0.2.2 and burn three version numbers on one
   change. Conventional-commit tooling aggregates everything since the last tag
   for exactly this reason.
3. **Tags do not survive history rewriting.** Amend, rebase or squash-merge and
   the tagged commit stops existing. GitHub's squash merge replaces the commit
   messages with the PR title, so a locally computed bump would be based on
   messages that never reach `main`.
4. **Hooks are skippable and per clone.** `git commit --no-verify`, or a clone
   where nobody ran `pre-commit install`, and the bump silently does not happen.
   Acceptable for a lint. Not acceptable for the thing that assigns version
   numbers.

So the hook does what a hook is good at - refusing a malformed message
immediately, before it is in the history - and the version is computed once,
from the commits, when a release is actually wanted.

Moving `cz bump` into a GitHub Actions workflow on `main` is a reasonable later
step, and changes none of the above; the bump would still be one aggregate
decision per release, just made by CI instead of by hand.

## Gotchas

- **Squash merges rewrite the message.** If a PR is squash-merged, the commit
  that lands on `main` carries the PR *title*, which no local hook has checked.
  Title the PR conventionally too, or merge with rebase to keep the checked
  messages.
- **Dependency updates do not bump the version.** `build(deps): ...` is
  intentionally inert, so a week of Dependabot merges does not produce
  releases. When a dependency bump does matter - a `garminconnect` release that
  fixes login, say - either write it as `fix(deps): ...` or force the bump with
  `cz bump --increment PATCH`.
- **The changelog starts at 0.1.0.** `changelog_start_rev` is set, because
  history before that tag predates conventional commits and there is nothing to
  generate from.
- **Two pins to keep in step.** `rev:` in `.pre-commit-config.yaml` and the
  `commitizen` pin in `pyproject.toml` should name the same version, so the
  rules the hook enforces are the rules `cz bump` reads.
  `pre-commit autoupdate` moves the first; Dependabot moves the second.
- **`cz commit` exists** if you would rather be prompted through the format
  than remember it. It is optional - a hand-written message that passes the
  check is no different.
