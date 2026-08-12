# Commit messages and releases

Commits are written in [Conventional
Commits](https://www.conventionalcommits.org/en/v1.0.0/) form, the version
number is derived from them by
[Commitizen](https://commitizen-tools.github.io/commitizen/), and the result is
published to [PyPI](https://pypi.org/project/repwise/).

The work is split in two, deliberately:

| When | What runs | What it does |
| --- | --- | --- |
| Every commit | `cz check`, via pre-commit's `commit-msg` hook | Rejects a message that is not conventional |
| When **Release** is dispatched | `.github/workflows/publish.yml` | Reads every commit since the last tag, bumps the version, commits and tags it on `main`, and uploads the result to PyPI |

A normal commit never changes the version. That is not a limitation of the
tooling - see [why not bump on every
commit](#why-not-bump-on-every-commit).

- [Setup](#setup)
- [Commit message format](#commit-message-format)
- [What each type does](#what-each-type-does)
- [Cutting a release](#cutting-a-release)
- [The release App](#the-release-app)
- [Publishing to PyPI](#publishing-to-pypi)
- [Why not bump on every commit](#why-not-bump-on-every-commit)
- [Gotchas](#gotchas)

## Setup

The hook is not active in a fresh clone until it is installed - the config is
version controlled, but git hooks are per clone:

```bash
.venv/bin/pip install -e . --group dev
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

Since 1.0.0 a breaking change costs a major version, and that is the whole
point of having released 1.0.0. Before it, `major_version_zero = true` in
`pyproject.toml` held `!` down to a minor bump; that line was removed with the
1.0.0 bump and should not come back. Write `!` when `workouts.yaml` or the CLI
changes shape under someone, and not otherwise.

## Cutting a release

One click, one run. Dispatch the **Release** workflow from the Actions tab and
it does the whole thing: works out the next version from the commits since the
last tag, writes the version files and the changelog, commits and tags that on
`main`, and uploads the result to PyPI. There is nothing to review and nothing
to merge.

The machinery is `.github/workflows/publish.yml`, which runs three jobs:

1. **`release`** - runs `cz bump --yes`, which writes the version files and the
   changelog, makes the commit and cuts the tag in one step, then pushes the
   commit and the tag to `main` in a single atomic push. It then asks PyPI
   whether that version is already there, which is what the next two jobs are
   keyed on.
2. **`build`** - checks out the tag, builds the sdist and the wheel from it and
   runs `twine check --strict` over them.
3. **`publish`** - uploads them. See [publishing to PyPI](#publishing-to-pypi).

The push is atomic so that the commit and the tag land together or not at all.
The alternative failure - `main` carrying a version whose tag does not exist,
or a tag on a commit nobody else can see - is the one state with no clean
recovery.

`cz bump` can be run against `main` like this only because of who pushes: the
[release App](#the-release-app) is a bypass actor on the `protect-default`
ruleset. Nothing else in the repository can write to `main` directly, and the
dispatch stays manual because a release is a decision.

**A release is not gated by CI.** Bypassing the ruleset bypasses its required
status check too, so nothing runs `ci.yml` over the bump commit before it is
tagged and shipped. What stands in for it is the state of `main` at the moment
you dispatch: every commit under the bump was checked on the way in through a
pull request, and the bump itself only touches generated files. Pushing it does
start a CI run, but that run reports *after* the upload rather than gating it.
So look at `main` before dispatching - a red `main` will release anyway.

To see what the workflow will propose, without changing anything:

```bash
.venv/bin/cz bump --dry-run
```

With nothing worth releasing since the last tag, `cz bump` changes nothing and
says so - `NO_COMMITS_FOUND` when there are no commits at all,
`NO_COMMITS_TO_BUMP` when there are but none of them earn a release. The second
case prints a misleading `bump: version 0.1.0 → 0.1.0` line first; it is not
about to re-cut the existing tag, and the refusal on the next line is the real
answer. The workflow treats both as nothing new to release: it pushes nothing,
and falls back to whatever version `main` already carries - which is what makes
a dispatch after a finished release a no-op, and a dispatch after a failed
upload a retry.

One bump touches four things:

| What | How |
| --- | --- |
| `[project].version` in `pyproject.toml` | `version_provider = "pep621"`, so this is the single source of truth |
| `__version__` in `src/repwise/__init__.py` | Listed in `version_files`, kept in step |
| `CHANGELOG.md` | Prepended, grouped by type, each entry linked to its commit |
| A commit and a tag | `bump: version 0.1.0 → 0.2.0`, tagged `0.2.0` - both made at once, on `main`, by the release App |

Tags carry no `v` prefix - `0.2.0`, not `v0.2.0`. That is Commitizen's default
and matches the 0.1.0 tag cut by hand before any of this existed, so
`tag_format` is set in `pyproject.toml` only to pin it against an upstream
change of default. Switching to `v` later means setting
`tag_format = "v$version"` and listing the old form in `legacy_tag_formats`, so
the existing tag stays readable; there is no reason to.

To override the computed version - a release that deserves a minor bump
although every commit was a `fix`, say - prepare the bump by hand and merge it
as an ordinary pull request, then dispatch **Release**:

```bash
git switch -c release-minor
.venv/bin/cz bump --version-files-only --changelog --increment MINOR
.venv/bin/cz bump --version-files-only --changelog 2.0.0   # or state it outright
git commit -am "bump: version 1.4.0 → 1.5.0"
```

`--version-files-only` is the flag that makes this safe to do locally: it
writes the version files and the changelog and leaves the commit and the tag
alone. `cz bump` without it would cut a tag against a commit that is still
going to be rewritten by the merge.

The dispatch afterwards finds nothing to bump - `bump` is not a type that earns
a release - so it takes the fallback: it tags the version `main` now carries
and publishes that. Do this on its own, with nothing else waiting to be
released, or the dispatch will find the other commits and bump straight past
the version you chose.

## The release App

The `release` job does not use the built-in `GITHUB_TOKEN`. It trades a private
key for an hour-long installation token from a GitHub App, and pushes the bump
commit and its tag to `main` with that.

The App exists because of the ruleset. `protect-default` allows no direct push
to `main`, and `GITHUB_TOKEN` is not exempt from it - which is why releases
used to travel as a self-merging pull request, and why the tag had to be cut in
a later step against whatever commit the merge produced. Listing the App as a
**bypass actor** on that ruleset removes the whole detour: `cz bump` makes the
commit and the tag together, and the push lands both.

| Where | Name | Value |
| --- | --- | --- |
| Variables | `RELEASE_APP_ID` | the App's numeric id |
| Secrets | `RELEASE_APP_PRIVATE_KEY` | the whole `.pem`, `BEGIN` line included |

The App is owned by `theadzik` and installed on this repository alone. The
installation holds **contents: write** and **pull requests: write**; the
workflow asks for `contents` by name rather than taking whatever the
installation happens to hold, so widening the App later does not quietly widen
the release job too. Pull requests are no longer used and the permission could
be dropped from the installation.

The bypass is granted to the App, not to a person, and it is what makes the
release job the single most privileged thing in the repository: it can write
`main` unreviewed and unchecked. That is the trade for a one-click release, and
it is why the key is worth guarding and the dispatch is worth thinking about.

To rotate the key: App settings → **Generate a private key**, then `gh secret
set RELEASE_APP_PRIVATE_KEY < the-new.pem`, then delete the old one on the same
page. Nothing else moves; the App id does not change.

## Publishing to PyPI

No PyPI password exists, here or anywhere - there is nothing to leak and
nothing to rotate. The `publish` job asks GitHub for a short-lived OpenID
Connect token describing the run that is asking, and PyPI trades that for an
upload token valid for fifteen minutes. This is [trusted
publishing](https://docs.pypi.org/trusted-publishers/), and the claim PyPI
actually checks is `job_workflow_ref`:

```text
theadzik/repwise/.github/workflows/publish.yml@refs/heads/main
```

**The filename is part of the credential.** That is why the workflow file is
called `publish.yml` rather than the `release.yml` it mostly is, and why the
publish job has to stay in it. Rename the file, or move that job elsewhere, and
uploads fail until the trusted publisher on PyPI is updated to match.

The publisher is configured once, at [PyPI → Publishing][pypi-publishing]:

| Field | Value |
| --- | --- |
| PyPI Project Name | `repwise` |
| Owner | `theadzik` |
| Repository name | `repwise` |
| Workflow name | `publish.yml` |
| Environment name | `publish` |

[pypi-publishing]: https://pypi.org/manage/account/publishing/

The environment is the other half, and PyPI checks it as a claim of its own.
`publish` has to exist under Settings → Environments, and the name has to be
the one the `publish` job names - because PyPI is configured to accept uploads
only from a job running in it, its protection rules, required reviewers or a
branch and tag restriction, are a real gate on the upload rather than a note
about one. Renaming it in one place and not the other fails the upload after
the tag has already been cut.

Two jobs rather than one, deliberately. `build` runs the project's own build
backend and downloads whatever it asks for; `publish` holds the right to mint
PyPI credentials. Keeping them apart means nothing arriving at build time is
running in a job that can publish - `publish` checks out no code at all, is not
even granted `contents: read`, and runs a single pinned action over a directory
of files. It signs them and uploads [PEP 740](https://peps.python.org/pep-0740/)
attestations on the way, which is what lets anyone check later that a file on
PyPI came from this workflow.

Whether to upload at all is decided by asking PyPI, in the `tag` job, rather
than by inferring it from whether that run created the tag:

```text
https://pypi.org/pypi/repwise/0.9.0/json  ->  404, so publish
                                              200, so there is nothing to do
```

A version reaches PyPI exactly once and can never be replaced or reused, so the
only question worth asking is whether it is up there already. Asking it this
way is also what makes a half-failed release recoverable: dispatch **Release**
again, and the tagging is a no-op while the upload is retried. Anything else -
a `tagged` flag from the tag job, say - would skip the retry precisely when it
is needed.

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
   messages that never reach `main`. This is the reason the tag is created by
   the workflow, after the merge, rather than by whoever ran `cz bump`.
4. **Hooks are skippable and per clone.** `git commit --no-verify`, or a clone
   where nobody ran `pre-commit install`, and the bump silently does not happen.
   Acceptable for a lint. Not acceptable for the thing that assigns version
   numbers.

So the hook does what a hook is good at - refusing a malformed message
immediately, before it is in the history - and the version is computed once,
from the commits, when a release is actually wanted.

Moving `cz bump` into a workflow, as `.github/workflows/publish.yml` now does,
changes none of the above. The bump is still one aggregate decision per
release; it is proposed by CI instead of typed by hand, and merging the pull
request is still where a human decides that a release happens.

## Gotchas

- **Squash merges rewrite the message.** If a PR is squash-merged, the commit
  that lands on `main` carries the PR *title*, which no local hook has checked.
  Title the PR conventionally too, or merge with rebase to keep the checked
  messages. This decides what the changelog says: after a squash it lists PR
  titles, after a rebase the individual commits.
- **The release depends on the App's bypass, not on workflow permissions.**
  Settings → Actions → General → "Allow GitHub Actions to create and approve
  pull requests" decides nothing here any more. What breaks the release
  instead is the App: a key rotated in one place and not the other, an
  installation removed from the repository, or the bypass actor dropped from
  the `protect-default` ruleset. The last one fails at the push, after `cz`
  has already made the commit and the tag locally - nothing reaches `main`,
  and re-dispatching once the bypass is back does the same work again.
- **Nothing checks the release before it ships.** The bypass skips the required
  status check along with everything else, so a red `main` releases just as
  happily as a green one. Look at `main` before dispatching; the CI run the
  bump itself triggers reports after the upload, not before it.
- **A release you do not want is a release you do not dispatch.** There is no
  longer a pull request sitting open to close - nothing happens until you press
  the button, and what it releases is whatever has accumulated by then.
- **A release that went out wrong cannot be unwound.** The old flow could be
  stopped at the merge; this one cannot. The tag is pushed and the upload
  follows in the same run, and a version on PyPI is final (below). The fix is
  always to release again, never to revert - and reverting the bump commit on
  `main` would only leave the tag pointing at history no version claims.
- **Publishing is a job here, not a workflow keyed on tags.** A `push: tags`
  workflow would now fire, since the App's pushes do trigger runs where
  `GITHUB_TOKEN`'s do not - but it buys nothing the `needs:` chain does not
  already give, and it would hand PyPI publishing rights to any run keyed on a
  ref the App can create. Keeping the upload in `publish.yml` also keeps the
  `job_workflow_ref` claim that PyPI checks pointing at one file.
- **A version on PyPI is final.** It cannot be replaced, re-uploaded or
  reused - deleting a release only frees the page, never the number. This is
  why `twine check --strict` runs before the upload and why the `publish` job
  is keyed on asking PyPI rather than on anything inferred locally. A release
  that went out wrong is fixed by releasing again, never by editing it.
- **The first upload needs a pending publisher.** Trusted publishing on a
  project that does not exist yet is configured as a *pending* publisher, which
  becomes a real one the moment the first upload creates the project. Without
  it the `publish` job fails the OIDC exchange, having already tagged - so
  configure it before the first dispatch, not after.
- **Dependency updates do not bump the version.** `build(deps): ...` is
  intentionally inert, so a week of Dependabot merges does not produce
  releases. When a dependency bump does matter - a `garminconnect` release that
  fixes login, say - either write it as `fix(deps): ...` or force the bump with
  `cz bump --increment PATCH`.
- **The changelog starts at 0.1.0.** `changelog_start_rev` is set, because
  history before that tag predates conventional commits and there is nothing to
  generate from.
- **Rewriting history after a release orphans the tags.** Amending or rebasing
  a commit that is already tagged leaves the tag on the old commit, which is
  then on no branch at all. `cz` matches tags to commits by sha, so it stops
  seeing those releases: it falls back to an older tag, re-aggregates commits
  that were released long ago, and proposes a version built from them. The
  changelog it writes looks duplicated because it is. Move the tags to the
  rewritten commits - `git tag -f <version> <new sha>` and a force-push -
  before letting the workflow run again.
- **Do not regenerate the whole changelog.** `cz changelog` rewrites the file
  from the commits, and released sections have been edited since they were
  written - a typo fixed in an entry is a typo still present in the commit it
  came from, so regenerating reintroduces it and `codespell` then fails. `cz
  bump` only prepends the new section, which is why it is safe and this is not.
- **Pull request numbers are not links.** `(#15)` reaches the changelog because
  a squash merge puts the pull request title in the commit message, but GitHub
  only autolinks that form in issues, pull requests, commit messages and
  release bodies - never in a Markdown file rendered from the repository. The
  commit link beside each entry is the way through; the commit page names the
  pull request it came from. Linking the number itself would need a
  `changelog_message_builder_hook`, which is a Python callable on the
  Commitizen class and so means shipping a plugin - `cz_customize` does not
  expose it.
- **Two pins to keep in step.** `rev:` in `.pre-commit-config.yaml` and the
  `commitizen` pin in `pyproject.toml` should name the same version, so the
  rules the hook enforces are the rules `cz bump` reads.
  `pre-commit autoupdate` moves the first; Dependabot moves the second.
- **`cz commit` exists** if you would rather be prompted through the format
  than remember it. It is optional - a hand-written message that passes the
  check is no different.
