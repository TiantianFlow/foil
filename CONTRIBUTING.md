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

## Run the checks

CI runs these. Run them before opening a pull request.

```sh
uv run --frozen --extra dev pytest --ignore=tests/e2e
uv run --frozen --extra dev pytest tests/e2e -m "not e2e_live"
uv run --frozen --extra dev ruff check .
uv build
```

A full local run is one command. It includes the fake-harness scenarios
and skips the live tier:

```sh
uv run --frozen --extra dev pytest
```

The end-to-end tests start real tmux sessions. The default tier points
templates at the `fake` harness, so it needs no agent CLI and no login.

The live tier runs the same scenarios against the harness CLI that
`foil init` would select on your `PATH`. It needs that CLI installed and
already logged in. CI does not run it. The default `pytest` command
skips it.

```sh
FOIL_E2E_LIVE=1 uv run --frozen --extra dev pytest tests/e2e -m e2e_live
```

## Rules every change keeps

1. **Requirements first.** A change in behavior updates
   docs/requirements.md in the same pull request. The command surface is
   fixed by section 6 there: don't add a command, subcommand, or flag
   that isn't listed.
2. **Stay small.** Foil's package source must stay within 2,000 lines of
   Python (requirement N4). Prefer deleting code to adding it.
3. **Everything you commit is public**, including commit messages and every
   intermediate commit. Never commit secrets, tokens, personal paths,
   private hostnames, or personal email addresses. The tests check the
   tracked files but not history, so review your commits before you push.
4. **Keep docs tidy.** List every new document in docs/README.md, name
   release plans `plan-vX.Y.Z.md`, and add notable changes to
   CHANGELOG.md under "Unreleased".

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
