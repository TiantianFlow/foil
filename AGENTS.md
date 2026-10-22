# Agent instructions

This file is for coding agents working on Foil itself.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) and
  [docs/requirements.md](docs/requirements.md) before you change anything.
  Behavior changes start in the requirements.
- Keep the rules in CONTRIBUTING.md under "Rules every change keeps".
  Everything you commit is public.
- Before you ask for review, work through "Before you ask for review" in
  CONTRIBUTING.md, and report what you verified, against which version,
  and what you did not.
- Write no compatibility path for a state that was never released: no
  migration step, uninstall note, or old-name fallback for something that
  only ever existed on a branch, in a git install, or between releases.
  A released version on PyPI is the only baseline.
- Before merging, run one independent review that covers the change,
  privacy, and publish safety. Repeat only when that review finds
  something worth fixing. A round that finds nothing worth fixing is
  done. Do not keep tuning.
