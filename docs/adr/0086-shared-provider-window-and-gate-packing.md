# ADR-0086 — Shared provider request budget and gate claim packing

Status: Accepted  
Date: 2026-11-19

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
