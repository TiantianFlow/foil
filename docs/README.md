# Foil documentation

| Document | What it is |
|---|---|
| [requirements.md](requirements.md) | What Foil must do. The only requirements document. |
| [plan-v0.2.0.md](plan-v0.2.0.md) | The plan for 0.2.0: the gaps between the previous code and the requirements, and the action items that close them. |
| [plan-v0.2.1.md](plan-v0.2.1.md) | The plan for 0.2.1: AI-native onboarding, the harness report, candidate roles, and the gaps 0.2.0 carried forward. |
| [plan-v0.2.2.md](plan-v0.2.2.md) | The plan for 0.2.2: a progress checklist in the lead's status file, and the operator's report from it (issue #11). |
| [plan-v0.3.0.md](plan-v0.3.0.md) | The plan for 0.3.0: mail and board reads through `foil` commands (issue #12). |
| [plan-v0.3.1.md](plan-v0.3.1.md) | The plan for 0.3.1: `foil roster` commands to view and change role templates (issue #10). |
| [architecture.md](architecture.md) | Components, data flow, and module boundaries of the implementation. |
| [demo.md](demo.md) | A real run, from `foil init` to a merged fix, replayed from the end-to-end suite. Linked from the English README. |
| [demo.zh-CN.md](demo.zh-CN.md) | The same demo in Chinese. Linked from the Chinese README. |

Outside this folder: [README.md](../README.md) for users,
[CONTRIBUTING.md](../CONTRIBUTING.md) for contributors,
[CHANGELOG.md](../CHANGELOG.md) for release history, and
[AGENTS.md](../AGENTS.md) for coding agents working on Foil.

## Conventions

- **One requirements document.** Behavior changes start in
  requirements.md. Other documents describe design, plans, or how to
  contribute; they never add requirements.
- **One plan per release**, named `plan-vX.Y.Z.md`.
- **Every document in this folder is listed above.** A test checks this.
- **No archives.** Documents describe the current state. Earlier versions
  live in git history and at release tags.
