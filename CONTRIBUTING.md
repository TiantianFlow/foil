# Contributing to Foil

## Read first

- [docs/requirements.md](docs/requirements.md): what Foil must do. Behavior
  changes start here.
- [docs/README.md](docs/README.md): the other documents, including the
  current release plan.

## Set up

You need Python 3.11+, uv, tmux 3.2+, and Git.

```sh
git clone https://github.com/TiantianFlow/foil.git
cd foil
uv sync --frozen --extra dev
```

`tests/test_view_browser.py` runs the `foil view` page in headless Chromium.
Install that browser once:

```sh
uv run --frozen --extra dev playwright install --only-shell chromium
```

## Run the checks

CI runs these. Run them before opening a pull request.

```sh
uv run --frozen --extra dev pytest -m "not e2e_live"
uv run --frozen --extra dev ruff check .
uv build
```

A full local run is one command. It includes the fake-harness scenarios
and skips the live tier:

```sh
uv run --frozen --extra dev pytest
```

pytest runs the tests in parallel, one worker per CPU (pytest-xdist's
`-n auto`). Add `-n 0` to run them in one process, for example to use
`pdb` or to time a single test.

The end-to-end tests start real tmux sessions. The default tier points
templates at the `fake` harness, so it needs no agent CLI and no login.

The live tier runs the same scenarios against the harness CLI that
`foil init` would select on your `PATH`. It needs that CLI installed and
already logged in. CI does not run it. The default `pytest` command
skips it. `FOIL_E2E_HARNESS` is a test-only override: when it is set, those
tests rewrite the harness id in `mapping.toml` after `init`. It is
not a command or a flag.

```sh
FOIL_E2E_LIVE=1 uv run --frozen --extra dev pytest tests/e2e -m e2e_live -n 0
```

## Rules every change keeps

1. **Requirements first.** A change in behavior updates
   docs/requirements.md in the same pull request. The command surface is
   fixed by section 6 there: don't add a command, subcommand, or flag
   that isn't listed.
2. **Stay small.** Foil's package source targets at most 2,500 lines of
   Python (requirement N4). Prefer deleting code to adding it.
3. **Everything you commit is public**, including commit messages and every
   intermediate commit. Never commit secrets, tokens, personal paths,
   private hostnames, or personal email addresses. Commits use
   `177855728+TiantianFlow@users.noreply.github.com`, not a personal
   mailbox. The tests check the tracked files but not history, so review
   your commits before you push.
4. **Keep docs tidy.** List every new document in docs/README.md, name
   release plans `plan-vX.Y.Z.md`, and add notable changes to
   CHANGELOG.md under "Unreleased".
5. **Keep the viewer in step.** Any change a person can see in
   `foil view`, including its info text, updates the page, its tests, and
   docs/requirements.md (F37 to F39) in the same change. That covers a new
   command or state that changes what a fleet, a seat, or a list shows,
   not only the features the page has today.

## Before you ask for review

CI only shows that the tests pass. Run this self-review too, and say in
the pull request or commit message what you ran, against which version,
and what you did not verify.

1. **Check the goal, not only the checkbox.** For each change, write one
   sentence on why it exists, and verify that. A plan's "done when" line
   is the least a change must do, not everything it must do.
2. **Run it as a user would.** Use a fresh folder, and install from your
   branch, not from `main`, into a temporary tool directory so your own
   `foil` is untouched:
   `UV_TOOL_DIR=$TMP/tools UV_TOOL_BIN_DIR=$TMP/bin uv tool install "git+https://github.com/TiantianFlow/foil.git@<branch>"`.
   Use only the documented commands. For onboarding, give the documented
   sentence to a coding agent that has no other context.
3. **Break it.** Give each new code path a missing file, an invalid file,
   none, one, and many; run it twice; interrupt it; send its output to a
   pipe. After every failure, check what is left on disk: branches,
   worktrees, windows, and files.
4. **Finish the change everywhere.** After changing a behavior or a
   message, search the READMEs, `docs/`, `skills/`, `src/`, and `tests/`
   for the old wording, and update every hit in the same change. Then
   open `foil view` on a project that uses the change, and check that the
   page and its info text still say what the feature now does.
5. **Test the tests.** Revert your fix and watch each new test fail.
   Assert on outcomes, such as files, branches, windows, and exit codes,
   before asserting on printed text. A test that pins today's output also
   pins today's bugs.
6. **Read your diff as a stranger would.** Everything is public (rule 3
   above). Keep notes about how the work was done out of documents,
   which describe the product, and keep each addition as short as it can
   be.
7. **Report honestly.** A check that did not exercise your code, for
   example one that ran an older installed version, is "not run", not a
   result.

## Before you tag

Re-check each preset's model ids in `src/foil/defaults/harnesses.toml` against that CLI's current docs, and change the packaged list when the docs have changed. Do not guess an id. The docs are:

- `claude`: <https://code.claude.com/docs/en/model-config>
- `codex`: <https://developers.openai.com/codex/models>
- `gemini`: <https://geminicli.com/docs/cli/model/>
- `grok`: <https://docs.x.ai/developers/models> and <https://docs.x.ai/build/cli/reference>
- `opencode`: <https://opencode.ai/docs/cli/> and <https://opencode.ai/docs/models/>. Its model id is `provider_id/model_id`, and its docs say the built-in provider and model names come from <https://models.dev/>. The packaged list, `anthropic/claude-sonnet-4-5` and `openai/gpt-5.5`, was checked there on 2026-10-21 and both ids were current.

`fake` has no vendor model list.

## Release

Pushing a `vX.Y.Z` tag runs `.github/workflows/release.yml`. It checks that the tag matches the version in `pyproject.toml`, builds the sdist and wheel, checks that the wheel's `foil --version` runs, and publishes both to PyPI as `foil-orchestrator`. To publish a tag that was pushed before that file existed, run the workflow by hand from `main` and give it the tag.

1. Set the version in `pyproject.toml` and `uv.lock`, and move the "Unreleased" notes in CHANGELOG.md under that version.
2. Merge, then tag the merge commit on `main` and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.

PyPI does not accept the same version twice, so a broken release is fixed with a new version.

Publishing uses PyPI trusted publishing, so the repository stores no PyPI token. It needs a one-time setup by the maintainer: on PyPI, add a trusted publisher for the project `foil-orchestrator` with owner `TiantianFlow`, repository `foil`, workflow `release.yml`, and environment `pypi`. On GitHub, create the environment `pypi` under the repository's Settings > Environments.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
