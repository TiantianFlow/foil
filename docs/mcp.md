# MCP clients

An MCP client talks to the same master as the page. This is how to add the server on this machine and from another machine, and how a client that only pulls hears that something needs you.

The rules are in [requirements.md](requirements.md). This page is the setup.

## On this machine

The client runs `foil-bridge` with one argument, `mcp`. That is stdio: one JSON-RPC message per line. There is no bearer. The process is you.

A client that starts a local program uses:

- command: `foil-bridge`
- args: `mcp`

`foil-bridge` has to be on `PATH`. When it is not, `foil bridge` prints the install line and does not start the server.

Before `initialize` has been answered, the only requests are `initialize` and `ping`.

## From another machine

`foil bridge install` prints the page address and the `tailscale serve` line. Run that line. The https address it prints, with the path `/mcp`, is the remote server.

The bearer is the desk token, the one line in `<root>/bridge/desk`, or a device token from pairing. Send `Authorization: Bearer` and that token. A query token does not authenticate `/mcp`.

The client sends `Accept` listing both `application/json` and `text/event-stream`, and `Content-Type: application/json`. A missing `Origin` is allowed on this route. The tailnet owner check still applies: a user-owned node, the node's own login, not a node shared with someone else, not a tagged node, and not Funnel. The bearer is still required.

A client that connects to a URL uses that https address and the bearer header. It does not send the desk token as a query parameter.

## What the client is

Give the client [mcp-client.md](mcp-client.md). It speaks as you to the master. It is not the master. `talk_to_master` writes your words into the same mailbox the home reads, and returns the master's reply for you to pass on.

`initialize` says the same in one paragraph. The tools are `talk_to_master`, `brief`, `inbox`, `fleet`, `agent_transcript`, and `answer`. None of them kills, archives, merges, or changes a setting.

## Wake

The server only answers a pull. A webhook is how a client that can be woken hears that something needs you. It stays off until you enable it.

`POST /v1/webhook` on the page address, with the desk token and a matching `Origin`. The body names `enabled`, `urls`, or both:

```json
{"enabled": true, "urls": ["<hook>"]}
```

A hook is an https address, or http on `127.0.0.1` or `localhost`, with no userinfo, at most 2000 characters, and a port from 1 to 65535 when one is written. At most 8. A bad address is refused and nothing is written. `GET /v1/webhook` returns `enabled` and `urls`.

The events are the same as a notification: an open question that is not the master's, a seat that is dead or paused, mail undelivered past its limit, a fleet whose state is `done`, the master down, or this process having been down. The push switches do not gate this. Unread mail from you, a chat reply, a blocked fleet, a paused fleet, and a running turn do not send.

The body is `{"kind": "needs_you", "count": N}` and nothing else. `count` is how many of those events are current. It has no question, no mail, no goal, no path, and no seat. The client calls `inbox` or `brief`, and `talk_to_master` when you should hear it.

At most one round of posts per 60 seconds. A URL that answered 2xx is not posted again while the same events are still current. A URL that did not is tried again after the minute, and it is not removed. A redirect is not followed.

## See that it works

Local: start the client and call `inbox`. The result is the same JSON as `foil inbox --all-projects`.

Remote: from another machine on the tailnet, `POST /mcp` with the bearer and call `inbox` again. The two results match. A message sent with `talk_to_master` shows on the home, because it is the same mailbox.

Wake: enable the webhook with a hook you control, and leave a question open. Within a minute the hook receives `{"kind":"needs_you","count":N}`. The body does not contain the question. Call `inbox` and the question is there.
