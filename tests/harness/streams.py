import json

ASSISTANT = "foil-fake: turn done"
SESSION_KEYS = {"agy": "conversation_id", "codex": "thread_id", "opencode": "sessionID"}


def _emit(event):
    print(json.dumps(event), flush=True)


def started(name: str, session: str, first: bool) -> None:
    if name == "agy":
        if first:
            _emit({"event": "init", "conversation_id": session})
        _emit({"event": "step_update", "step_update": {
            "conversation_id": session, "step_index": 0, "state": "DONE",
            "step_type": "user_input",
        }})
    elif name == "codex":
        if first:
            _emit({"type": "thread.started", "thread_id": session})
        _emit({"type": "turn.started"})
    elif name == "opencode" and first:
        _emit({"sessionID": session})


def completed(name: str, text: str) -> None:
    if name == "codex":
        _emit({"type": "item.completed", "item": {
            "id": "item_0", "type": "agent_message", "text": text,
        }})
    else:
        print(text, flush=True)
