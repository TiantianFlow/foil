# ADR-0004: No MCP runtime

Status: Accepted  
Date: 2026-08-23  
Requirements: CAP-001, CAP-010, CAP-011, CAP-020, CAP-028

## Context

The product must remain usable from controller and worker CLIs with uneven tool support. An MCP transport adds lifecycle, discovery, authentication, and reliability dependencies that are unnecessary when local CLI commands and files are available.

## Decision

Foil ships no MCP server, MCP transport, MCP manifest, or runtime MCP dependency. Its public mechanisms are:

- a documented `foil` CLI;
- versioned machine-readable files;
- portable Markdown skills with CLI/file fallbacks;
- tmux and ordinary subprocess boundaries;
- an optional browser bridge that is not an MCP server.

Adapters may launch external CLIs that independently support MCP, but Foil does not configure, require, proxy, or depend on that support.

## Consequences

Tool discovery is less automatic, so CLI help, schemas, examples, and skills must be strong. The architecture works in shells and agents without MCP and has fewer long-lived transports. Features that exist only as an MCP tool need a CLI/file equivalent before Foil can adopt them.

## Rejected alternatives

- MCP as the primary control plane: violates portability and reliability goals.
- MCP plus a nominal CLI wrapper: still leaves MCP as a runtime dependency.
- Per-provider RPC plugins: recreates bespoke integration code.
