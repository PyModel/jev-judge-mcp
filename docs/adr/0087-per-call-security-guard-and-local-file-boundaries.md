# ADR-0087 — Per-call security guard and local file boundaries

Status: Accepted  
Date: 2026-11-19

## Context

The file tools, ask, and hooks assembled secret paths and redactors independently.
Defaults could silently leave a reader or State builder without either protection.
Local readers and path checks also differed across entry points.

## Decision

Runtime and hook preparation build a required guard from current Settings and the
stored key. The guard carries the configured key-store path and scrubs provider-bound
State by replacing exact values first, then credential literals. The shared
`resolve_scoped` check resolves through symlinks and compares path components. It also
handles the filesystem root as a scope.

The bounded filesystem reader opens with nonblocking semantics and checks the opened
descriptor is regular before reading. Its callers select whether to follow symlinks,
read relative to a pinned directory, or read from the tail. The stored-key reader
intentionally follows symlinks and validates owner and write permissions on the opened
file. File evidence rejects symlinks at the descriptor-pinned read. The CLI and MCP
file reader share decoding and refusal rules while retaining their own unit limits
(ADR-0064 and ADR-0086).

The private atomic writer is shared by setup, cache, and installer state. It creates
private directories as needed and refuses an existing directory with group or other
permissions, or one owned by another user. It writes mode 0600, syncs the file,
replaces the destination atomically, and syncs the parent directory. Existing loose
directories require correction by their owner.

## Consequences

A later call observes a newly stored key. Key resolution validates and registers the
exact snapshot used to construct the provider (ADR-0050). Evidence errors retain their
typed codes and hook and ask payloads share the same secret inventory.
