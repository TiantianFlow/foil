# Authenticated lead-to-worker exam

This is the release proof for startup delivery and explicit permission
mode. It is not a walking-skeleton step and it is not a CI job.

Run it on a machine that already has authenticated `grok` and `opencode`.
There must be no manual tmux prompt, no manual approval, no fake CLI, and
no pane-state inference.

```sh
export FOIL_E2E_LIVE=1
uv run --no-config --frozen --extra dev pytest tests/e2e/test_live_agents.py::test_live_authenticated_lead_to_worker_roundtrip -q
```

The exam:

1. Start a Grok lead with `--permission auto`.
2. The lead reads `{bootstrap_path}`, its sibling `FOIL.md`, and the
   `role_path` recorded in `bootstrap.json` per shipped `startup.argv`.
3. The lead spawns an OpenCode worker with `--permission auto`.
4. The worker reads its bootstrap the same way.
5. The lead sends a durable mailbox message with `foil send-message --wake`.
6. The worker bounded-polls its mailbox (and again whenever woken), then
   acknowledges after reading.
7. The worker replies through the mailbox with `foil send-message --wake`.
8. The lead bounded-polls for the reply and acknowledges it.
9. Foil stops and removes both seats.
10. No Foil tmux windows remain.

Both directions are durable mailbox messages plus bounded wake/poll. Wakes
carry only the fixed Foil notice; message bodies are never injected into
tmux and panes are never parsed.

Default `--permission supervised` still asks the provider for approvals.
This exam must use `auto`.
