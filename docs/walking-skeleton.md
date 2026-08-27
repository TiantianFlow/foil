# T1–T6 walking skeleton

This is the canonical executable verification contract. It is not the
user-facing product page. Run the snippets in order in the same shell
after the prerequisites. They install `foil`, scaffold an isolated
project, spawn a lead and complementary seats, deliver a durable message,
prove native resume after a deliberate tmux kill, and remove the demo
root only after `foil seat stop` reports the seats exited.

Foil ships no MCP server or runtime dependency. Public mechanisms are
CLI commands, tmux, and versioned files. Authenticate `grok` and
`opencode` themselves. There is no Foil login or credential setup. The
commands below intentionally never print environment variables,
credential stores, or a terminal buffer.

## Prerequisites

This walking skeleton requires:

| Tool | Required | Safe check |
|---|---|---|
| Python | **Python >=3.11** | `python3 --version` |
| uv | Current uv | `uv --version` |
| tmux | **tmux >=3.2** | `tmux -V` |
| Git | Current Git | `git --version` |
| Grok CLI | locally authenticated `grok` | `grok --version` |
| OpenCode | locally authenticated `opencode` | `opencode --version` |

tmux 3.2 is the conservative supported floor for the user options, stable
session/window format IDs, literal `send-keys`, and working-directory launch
operations used here; older versions are not tested.

Every fleet starts with one lead. Complementary workers are spawned next.
Known CLI pairings:

| Seat | CLI | Model |
|---|---|---|
| `lead` | `grok` | bare model ID `grok-4.6` |
| `implementer` | `grok` | bare model ID `grok-4.6` |
| `reviewer-challenger` | `opencode` | provider-qualified model ID `xai/grok-4.6` |

## T1–T6 walking skeleton

Run the T1–T6 snippets below in order in the same shell. They refuse to reuse an
existing demo directory, keep project and state together under one deterministic
temporary root, parse the state root emitted by `foil init`, and define every
shell variable they use.

### T1 — Install from a clean checkout

From the root of a clean checkout, install the `foil` command:

```sh
uv tool install .
foil --help
```

If uv reports that `foil-orchestrator` is already installed, refresh it safely
from the current checkout instead of uninstalling files by hand:

```sh
uv tool install --reinstall .
foil --help
```

### T2 — Initialize and scaffold an isolated project

`foil init .` must run before Git initialization because it accepts only an
empty project. It writes a role library and an empty live fleet. Git is
initialized immediately afterward with `git init -b foil-demo`.

```sh
set -eu

SOURCE_CHECKOUT="$(pwd -P)"
test -f "$SOURCE_CHECKOUT/pyproject.toml"

TMP_BASE="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
DEMO_ROOT="$TMP_BASE/foil-walking-skeleton-demo"
PROJECT_DIR="$DEMO_ROOT/project"
FOIL_STATE_DIR="$DEMO_ROOT/state"
export FOIL_STATE_DIR

if [ -e "$DEMO_ROOT" ]; then
  printf 'Refusing to reuse existing demo root: %s\n' "$DEMO_ROOT" >&2
  exit 1
fi
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

INIT_JSON="$(foil init .)"
printf '%s\n' "$INIT_JSON" | python3 -m json.tool
STATE_DIR="$(printf '%s\n' "$INIT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state_root"])')"
FLEET_ID="$(printf '%s\n' "$INIT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fleet_id"])')"
ROLES_PATH="$(printf '%s\n' "$INIT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["roles_path"])')"
LEAD="$(printf '%s\n' "$INIT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["lead_seat_id"] or "")')"
test "$STATE_DIR" = "$FOIL_STATE_DIR"
test "$ROLES_PATH" = "$PROJECT_DIR/.foil/roles"
test -z "$LEAD"
git init -b foil-demo
```

### T3 — Spawn a lead and complementary seats

The first seat must be the lead. The operator then spawns
`implementer` and `reviewer-challenger` workers. Workers isolate by
default. Spawn is `--permission supervised` unless you pass `auto`.
Lifecycle commands use `--state-dir`, `--fleet`, and machine-readable
`--json`. `working` means the recorded tmux process is alive. The
unattended Grok-to-OpenCode exam lives in
[authenticated-lead-worker-exam.md](authenticated-lead-worker-exam.md).

```sh
LEAD_JSON="$(foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --lead --seat lead --cli grok --role manager)"
printf '%s\n' "$LEAD_JSON" | python3 -m json.tool
IMPL_JSON="$(foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat implementer --cli grok --role implementer)"
printf '%s\n' "$IMPL_JSON" | python3 -m json.tool
REVIEW_JSON="$(foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat reviewer-challenger --cli opencode --role reviewer-challenger)"
printf '%s\n' "$REVIEW_JSON" | python3 -m json.tool
printf '%s\n' "$REVIEW_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
seats = {seat["seat_id"]: seat for seat in payload["seats"]}
assert set(seats) == {"reviewer-challenger"}
assert seats["reviewer-challenger"]["state"] == "working"
assert seats["reviewer-challenger"]["registry"]["agent_kind"] == "opencode"
'

STATUS_JSON="$(foil status --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json)"
printf '%s\n' "$STATUS_JSON" | python3 -m json.tool
printf '%s\n' "$STATUS_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
seats = {seat["seat_id"]: seat for seat in payload["seats"]}
assert set(seats) == {"lead", "implementer", "reviewer-challenger"}
assert payload["lead_seat_id"] == "lead"
assert all(seat["state"] == "working" for seat in seats.values())
assert all(seat["registry"]["tmux"]["session_name"].startswith("foil-") for seat in seats.values())
'
```

### T4 — Deliver a file-backed message and wake the reviewer

`foil send-message` commits the fixed body to the durable file mailbox.
Wake is opt-in (`--wake` or `foil seat wake`). The wake injects only a
fixed, bounded Foil notice—not the message body—and `foil message-status`
proves the message remains `queued`.
OpenCode creates its native session ID after this first interaction, so the
bounded loop calls `foil status` until both exact `native_session_id` values
have been reconciled from structured sources.

```sh
SENDER_ID="quickstart-controller"
TASK_ID="quickstart-review"
MESSAGE_ID="quickstart-review-001"
MESSAGE_BODY="Review the walking-skeleton runtime and report risks."
DELIVERY_JSON="$(foil send-message --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --seat reviewer-challenger --sender "$SENDER_ID" --message-id "$MESSAGE_ID" --task "$TASK_ID" --body "$MESSAGE_BODY" --wake)"
printf '%s\n' "$DELIVERY_JSON" | python3 -m json.tool
printf '%s\n' "$DELIVERY_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert payload["message"]["recipient_seat_id"] == "reviewer-challenger"
assert payload["message"]["message_id"] == "quickstart-review-001"
assert payload["message"]["body"] == "Review the walking-skeleton runtime and report risks."
assert payload["duplicate"] is False
assert payload["delivery"] == {"state": "queued", "wake": {"state": "sent"}}
'

MESSAGE_STATUS_JSON="$(foil message-status --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --seat reviewer-challenger --message "$MESSAGE_ID")"
printf '%s\n' "$MESSAGE_STATUS_JSON" | python3 -m json.tool
printf '%s\n' "$MESSAGE_STATUS_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert payload["state"] == "queued"
assert payload["message"]["message_id"] == "quickstart-review-001"
'

ATTEMPT=0
MAX_ATTEMPTS=30
while :; do
  ATTEMPT=$((ATTEMPT + 1))
  STATUS_JSON="$(foil status --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json)"
  if printf '%s\n' "$STATUS_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
seats = {seat["seat_id"]: seat for seat in payload["seats"]}
assert set(seats) == {"lead", "implementer", "reviewer-challenger"}
assert all(isinstance(seat["registry"]["native_session_id"], str) and seat["registry"]["native_session_id"] for seat in seats.values())
'; then
    break
  fi
  if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
    printf 'Native session IDs were not both captured after %s seconds.\n' "$MAX_ATTEMPTS" >&2
    exit 1
  fi
  sleep 1
done

BEFORE_STATUS_JSON="$STATUS_JSON"
BEFORE_STATUS_PATH="$DEMO_ROOT/before-status.json"
printf '%s\n' "$BEFORE_STATUS_JSON" > "$BEFORE_STATUS_PATH"
printf '%s\n' "$BEFORE_STATUS_JSON" | python3 -m json.tool
TMUX_SESSION_BEFORE="$(python3 -c '
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
names = {seat["registry"]["tmux"]["session_name"] for seat in payload["seats"]}
assert len(names) == 1
name = names.pop()
assert name.startswith("foil-")
print(name)
' "$BEFORE_STATUS_PATH")"
```

### T5 — Kill tmux and verify resume precedence and registry continuity

Normal precedence is **matching live tmux → `revive_tmux`; recorded native
session → `resume_native`; otherwise logged `start_fresh`**. The deliberate
`tmux kill-session` below is an acceptance-test fault injection: it removes the
live branch only after recording both native IDs, forcing the native branch.
The before/after registry check proves native IDs and incarnation IDs remain
stable while tmux IDs change. Do not use this kill command for normal cleanup.

```sh
tmux kill-session -t "$TMUX_SESSION_BEFORE"
DEAD_STATUS_JSON="$(foil status --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json)"
printf '%s\n' "$DEAD_STATUS_JSON" | python3 -m json.tool
printf '%s\n' "$DEAD_STATUS_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert len(payload["seats"]) == 3
assert all(seat["state"] == "exited" for seat in payload["seats"])
'

RESUME_JSON="$(foil resume --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json)"
printf '%s\n' "$RESUME_JSON" | python3 -m json.tool
printf '%s\n' "$RESUME_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
seats = {seat["seat_id"]: seat for seat in payload["seats"]}
assert set(seats) == {"lead", "implementer", "reviewer-challenger"}
assert all(seat["action"] == "resume_native" for seat in seats.values())
assert all(seat["state"] == "working" for seat in seats.values())
'

AFTER_STATUS_JSON="$(foil status --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json)"
AFTER_STATUS_PATH="$DEMO_ROOT/after-status.json"
printf '%s\n' "$AFTER_STATUS_JSON" > "$AFTER_STATUS_PATH"
printf '%s\n' "$AFTER_STATUS_JSON" | python3 -m json.tool
python3 - "$BEFORE_STATUS_PATH" "$AFTER_STATUS_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    before = {seat["seat_id"]: seat["registry"] for seat in json.load(handle)["seats"]}
with open(sys.argv[2], encoding="utf-8") as handle:
    after = {seat["seat_id"]: seat["registry"] for seat in json.load(handle)["seats"]}

assert set(before) == set(after) == {"lead", "implementer", "reviewer-challenger"}
for seat_id in sorted(before):
    assert after[seat_id]["native_session_id"] == before[seat_id]["native_session_id"]
    assert after[seat_id]["incarnation_id"] == before[seat_id]["incarnation_id"]
    assert after[seat_id]["tmux"]["session_name"] == before[seat_id]["tmux"]["session_name"]
    assert after[seat_id]["tmux"]["session_id"] != before[seat_id]["tmux"]["session_id"]
    assert after[seat_id]["tmux"]["window_id"] != before[seat_id]["tmux"]["window_id"]
PY

POLL_JSON="$(foil poll-status --state-dir "$STATE_DIR" --fleet "$FLEET_ID")"
printf '%s\n' "$POLL_JSON" | python3 -m json.tool
printf '%s\n' "$POLL_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert {seat["seat_id"] for seat in payload["seats"]} == {"lead", "implementer", "reviewer-challenger"}
assert all(seat["state"] == "working" for seat in payload["seats"])
'
```

### T6 — Stop safely and remove the verified demo root

T6 is distinct from T5. `foil seat stop` verifies stable tmux markers before
stopping windows. Files are removed only after its JSON reports the seats
exited and path guards prove that project and state are inside the intended
demo root.

```sh
STOP_JSON="$(foil seat stop --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --all)"
printf '%s\n' "$STOP_JSON" | python3 -m json.tool
printf '%s\n' "$STOP_JSON" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert len(payload["seats"]) == 3
assert all(seat["state"] == "exited" for seat in payload["seats"])
'

cd "$SOURCE_CHECKOUT"
test "$PROJECT_DIR" = "$DEMO_ROOT/project"
test "$STATE_DIR" = "$DEMO_ROOT/state"
test "$DEMO_ROOT" = "$TMP_BASE/foil-walking-skeleton-demo"
rm -rf -- "$DEMO_ROOT"
```

`foil status --json` above shows live tmux evidence and the full durable
registry for each seat. `foil poll-status` independently reads the versioned
status files; it does not probe agents or inspect terminal buffers. A queued
message remains queued until the seat writes an acknowledgement, regardless of
whether the advisory wake succeeded.
