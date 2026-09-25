---
status: accepted
---
# A file list is reviewed per file, not silently cut

A string `diff` over the document cap is truncated and cannot be `auto`. That inherited rule stays for a string. A caller who has the bytes per file should not have to hope the join fits.

## Decision

- `diff` accepts the string it accepts today, or `[{path, patch}]`.
- The array is split by file. Each file is reviewed under the per-document cap. The call's action is the worst file action.
- A file or hunk that does not fit is listed in `unreviewed_files`. The payload sets `partial` true. The call never returns `auto` while any file is unreviewed.
- The joined array is refused past the 200,000-unit evidence budget, the same budget as gate evidence. That refusal is an error, not a silent cut.
- A string that is a unified diff is what `gate` the CLI passes as this array. A string argument to the MCP tool still follows the inherited truncation rule.

## Consequences

- Two files under the cap can be `auto` when each review is `auto`, even if joining them would have been truncated.
- One bad file escalates the whole call.
