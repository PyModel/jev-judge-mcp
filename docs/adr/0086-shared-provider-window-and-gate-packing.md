# ADR-0086 — Shared provider request budget and gate claim packing

Status: Accepted  
Date: 2026-11-19

The [amendment below](#amendment-2026-11-19-the-byte-budget-packs-the-provider-refuses)
supersedes the byte-based refusals in the original decision.

## Context

The provider documents 32k tokens for state plus its longest question and 64k tokens per
request. Gate checked 30,000 and 60,000 compact UTF-8 bytes respectively, while ask counted
120,000 UTF-16 units and file judgment read up to 100,000 units. The all-patch claim request
also prevented otherwise reviewable file-list and range gates from reaching the provider.

## Decision

Every provider request uses the same compact JSON budget, including keys, criteria, separators
and escaping. The byte ceilings conservatively leave room for provider framing at byte-level
tokenization. Ask preflights before running its optional command and checks again with the
output. Its split suggestions check each proposed request against both ceilings.

File-list gates pack patches first-fit in input order, avoiding reordering by patch size and
keeping group membership deterministic. Every group asks every claim with caller evidence and
tests. A confident grounded contradiction wins. Otherwise a confident verification wins if
present, preferring an independently supported result. Without either, the claim is unsupported.
An invalid answer in any group holds the claim for review. The first qualifying row supplies
the displayed evidence. One group retains the existing result shape. Multi-group results report
the total number of review and verification calls and sum their usage.

A file that cannot fit alone with the shared inputs is held for local review as `oversize`.
The raw aggregate and file-count guards still bound the full change. Shared input that cannot fit
refuses before dispatch. The range tool also holds untracked and binary paths. Git reads share one
execution policy, described in the ADR-0080 amendment.

## Consequences

String gates continue to send the diff and tests twice. Gate inputs are never truncated.
Existing review truncation and file-read admission rules still apply before the shared check.
The TypeScript recordings remain immutable. Python request and result differences are registered
in `docs/reference/divergences.json`.

## Amendment (2026-11-19): the byte budget packs, the provider refuses

Live measurements from `jev_file_judge` on 2026-11-19, through a server without the byte cap:

| Content | UTF-8 bytes | Input tokens | Bytes/token |
| --- | ---: | ---: | ---: |
| LICENSE (baseline) | 1,063 | 524 | n/a |
| tools/gate.py (code) | 23,710 | 6,087 | ~4.0 |
| docs/tools.md (prose) | 24,112 | 6,406 | ~3.9 |
| README.md | 34,322 | 10,060 | ~3.5 |
| tests/unit/test_install.py | 67,899 | 19,655 | ~3.5 |
| Random hex lines | 29,964 | 26,903 | ~1.1 |
| Random CJK | 30,000 | 19,475 | ~1.5 |
| 97,336 bytes of base64 | 97,336 | upstream 400 `max_tokens_exceeded` | n/a |

The byte estimate is close to the observed ratio for random hex. Ordinary code and prose used
about one token per 3.5 to 4 bytes and fit well past the 30,000-byte estimate. ADR-0079 stands.
The byte budget packs, the provider refuses. The 30,000 and 60,000 compact UTF-8 byte estimates
guide first-fit grouping, optional context, and Ask split advice. They do not refuse a request.
The original UTF-16 input caps apply again: 50,000 units per gate document, 100,000 units per
server-read file, and 120,000 units of composed Ask state and questions. Ask command output keeps
its independent 30,000-unit cap.

File-list gates send a patch above the packing estimate in its own claims group. If the provider
returns HTTP 400 with `{"detail":{"error_type":"max_tokens_exceeded"}}` for a group, the gate
holds that group's files as `oversize` and caps its claims at review. This applies regardless of
the estimated size. Other groups still contribute their results. The gate reports partial coverage
and counts the refused calls, and usage includes calls that reported it. A file review that hits
the provider window gets a review row with hunk-split advice. The independent raw gate input caps
still apply. Other tool calls, including string gates and standalone file judgment batches, return
`input_too_large` with split advice on provider context overflow.
