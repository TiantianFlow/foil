# ADR-0006: Capability-bounded browser bridge

Status: Accepted; implementation deferred until canary contract tests are specified  
Date: 2026-08-23  
Requirements: CAP-022, CAP-023, CAP-034

## Context

Browser automation protocols such as raw CDP and Playwright can inspect cookies, authorization-bearing traffic, storage, and broad page state. Giving an untrusted worker direct protocol access cannot satisfy the requirement that credentials never be exposed. The bridge is useful but optional and must not weaken core orchestration.

## Decision

- Never give controller or worker seats a raw CDP endpoint, Playwright object, browser profile path, cookie API, unrestricted JavaScript evaluator, or unrestricted DOM/network dump.
- Run the bridge as a separate, more-trusted local capability broker.
- Require an explicit, expiring operator grant bound to origins, tab identity, allowed high-level actions, and output limits.
- Expose only narrow actions such as navigate to an allowlisted origin, locate/click/fill an approved target, wait for a condition, and return a bounded redacted result.
- Omit cookie, authorization-header, storage-export, profile-filesystem, arbitrary-script, and raw-protocol capabilities.
- Audit requests and decisions after structural redaction; seed canary credentials in security tests.
- Prefer a dedicated automation profile or a browser-side component without cookie permission for the first implementation. Attaching to a general human profile requires a later security ADR with evidence that CAP-023 remains enforceable.
- Keep the bridge optional and independently stopped; core Foil has no browser dependency.

The local transport may be a permissioned Unix-domain socket or one-shot subprocess protocol. It is not MCP and must not listen on a network interface.

Default-profile remote debugging is restricted, and raw CDP/Playwright surfaces expose forbidden credential classes. The first implementation therefore uses a dedicated profile and must land with canary non-disclosure contract tests that seed secrets in cookies, headers, storage, and page content and assert they never appear in Foil files, stdout, stderr, or audit events.

## Consequences

The bridge supports fewer actions than raw browser tooling and cannot promise compatibility with every site. The trusted broker remains security-sensitive even when its output is redacted. A compromised raw browser-control process could access authenticated state; process isolation, grants, and minimal surface reduce but do not erase that fact.

## Rejected alternatives

- Direct CDP/Playwright access for seats: inherently over-privileged.
- Copying a logged-in profile: duplicates secrets and risks profile corruption.
- Returning full DOM/network traces and redacting afterward: secret detection is incomplete.
- Making browser control a core daemon: violates optionality and expands the attack surface.
