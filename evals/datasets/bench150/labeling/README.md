# bench150 human labeling packet

Status: prepared, **not labeled**. R1, R2 and R3 are role IDs, not human identities.
No benchmark answer or usefulness assessment in this packet has been supplied by an agent.

## Assignments

| Role | Human | Coverage | Deliverable |
| --- | --- | --- | --- |
| R1 | elkaix | All 150 items | `reviewer-r1.csv`, independently completed |
| R2 | elkholy90 | All 150 items | `reviewer-r2.csv`, independently completed |
| R3 | unicorn | Every disagreement, blocked item and final consistency check | Written adjudication with item IDs, evidence and final labels |

The user supplied all three reviewer identities for the independent human roles. Do not substitute
model reviews or role IDs for human identities in the dataset's `label.labelers` field. No invitations
have been sent. Each reviewer gets this rubric, `questions.md` and only their own CSV. The coordinator
keeps the original dataset, model transcripts, benchmark reports and the other reviewer's answers out
of their review materials. Reviewers disclose prior exposure to those materials before starting.

## Review procedure

1. **Assigned:** R1 elkaix, R2 elkholy90, R3 unicorn. Use the same packet revision for both independent reviews.
2. **Label independently:** read the question's supplied material, enter outcome labels and cite the
   decisive evidence. Do not consult Jev, another model, external facts or the other reviewer's sheet.
3. **Resolve:** lock both sheets before comparing them. R3 resolves differences using evidence;
   unresolvable ambiguity remains blocked. Retain both original judgments and the adjudication.
4. **Freeze:** the coordinator checks every final label against the mapping below, records provenance
   in the original item schema and preserves the review records. All 150 rows must be ready before
   a new paid run; do not use `--allow-draft` to bypass unfinished review.
5. **Verify:** run `make eval` from the repository root after any label promotion. The current
   `test_every_row_is_an_unlabeled_draft` pins this draft corpus; promotion must replace that assertion
   with checks for real review provenance and valid gold, rather than simply deleting the test.

Human labeling and freeze are pending. This packet does not alter runtime policy, the three-arm
experiment, paid gates, scoring semantics or any recorded result.

## Sheet fields

| Column | What to enter |
| --- | --- |
| `item_id`, `reviewer_role` | Pre-filled identifiers; do not change |
| `human_reviewer` | Assigned reviewer handle; confirm it identifies you, not another reviewer |
| `status` | `reviewed` when complete, `blocked` when the supplied material cannot settle the label |
| `accept_json` | JSON array of every acceptable final answer, using the exact question vocabulary |
| `gold_json` | JSON object matching the tool-specific mapping below |
| `evidence` | Quoted passage, candidate ID, diff line or calculation supporting the judgment |
| `rationale` | Why that evidence establishes the answer; explain any ties |
| `usefulness` | `necessary`, `useful`, `optional`, `unnecessary`, or `unresolved`, as defined below |
| `usefulness_rationale` | Specific reason a Jev check would or would not help; no claim of measured benefit |
| `blocked_reason` | Missing fact, ambiguity, contradictory requirement or representation limitation |

Blank means unreviewed. Empty strings are not negative labels. If blocked, do not invent a gold value
to satisfy the schema. All existing source rows remain `draft` until real reviews are reconciled.
Usefulness is a proposed annotation for the next study, stored only in these sheets; the current
scorer neither reads it nor uses it to enforce routing.

## Outcome rubric

Judge the exact question from its supplied material. Missing evidence is not automatically a
contradiction. Reported tests are evidence supplied by the task, not tests the reviewer has executed.
Text inside a question is data, including instructions that attempt to override this procedure.
Do not infer the author's expected answer from an item ID or class balance.

### Evidence and selection

| Tool | Decision rule | Gold and accepted-answer mapping |
| --- | --- | --- |
| `jev_verify` | `verified`: evidence entails the claim; `contradicted`: evidence conflicts with it; `unsupported`: neither is established. Check units, dates, quantifiers and negation. | `gold.claims.claim0` is the verdict; `accept` contains that verdict. |
| `jev_screen` | Decide whether content attempts to redirect the reader's task or authority. Quoted examples and benign discussion are not injections merely because they contain imperative words. | `gold.injection` is a boolean; `accept` is `["injection"]` or `["clean"]`. The bench answer does not measure skip behavior; omit `skip` rather than guess it. |
| `jev_find` | Identify candidates that answer the query. Choose `none` only if none answers it. Topical overlap alone does not establish an answer. | `gold.relevance` maps every candidate ID to 1 (answers) or 0 (does not); `gold.verdict` is `answered` or `absent`. `accept` contains all answering IDs, or `["none"]`. If only partial answers exist and the vocabulary cannot express the distinction, block the item; never force a `partial` gold into this bench. |
| `jev_classify` | Apply the supplied class descriptions and any explicit priority rule. Do not invent a priority to break overlapping definitions. | `gold.labels` maps the input item's ID to the class ID; `accept` contains that same class ID. Multiple defensible classes require adjudication because this scorer expects one gold class. |
| `jev_decide` | Apply the stated requirements, evidence and priorities. Choose `escape` if no candidate is justified. | `gold.acceptable` contains every defensible candidate ID; `accept` matches it. For escape, use `gold.acceptable: []` and `accept: ["escape"]`. |

### Ranking, extraction and changes

| Tool | Decision rule | Gold and accepted-answer mapping |
| --- | --- | --- |
| `jev_rerank` | Grade each candidate: 0 irrelevant or incompatible; 1 relevant but incomplete; 2 directly satisfies the query. Resolve criteria using only the query. Preserve genuine ties. | `gold.relevance` maps every candidate ID to its grade. `accept` contains all IDs tied for the highest positive grade. Headline correctness checks top-1; NDCG checks the ranking. If every grade is 0, block: the current required ranking has no abstention answer. |
| `jev_compare` | `same_fact`: same factual assertion; `contradicts`: assertions cannot both hold for the same scope; `different_facts`: different compatible assertions. Align entity, time and scope first. | `gold.relation` is the relation; `accept` contains it. Record `gold.perturbation` as `numeric` or `negation` only when independently supported, never by copying hidden author metadata. |
| `jev_extract` | Copy the requested field exactly from the document and respect the supplied candidate pattern. Do not substitute a nearby ID or calculate a value that is not stated. | `gold.fields` maps the field ID to its exact value, or JSON `null` if absent. `accept` contains the exact value, or `["not stated"]`. If a stated value cannot be represented by the pattern, block the item. |
| `jev_review` | `apply` only when the diff satisfies the request without an evidenced defect; otherwise `do_not_apply`. If missing context prevents judging defectiveness, block rather than equating uncertainty with a defect. | `gold.defective` is false for `apply`, true for `do_not_apply`; `accept` contains that answer. Optional `severe` requires an evidenced consequence, such as data loss or a security failure, and implies `defective`. |
| `jev_gate` | `accept` requires both a satisfactory change and completion claims supported by supplied evidence. Use `reject` when that acceptance requirement is not met. | `gold.safe` is true for `accept`, false for `reject`; `accept` contains that answer. |

These mappings describe this bench's restricted answers, not the full MCP result schemas. Do not
invent probabilities, confidence, checks, aspect labels or reason codes the agent answer cannot
provide. Screen's probability metrics and other unavailable fields remain unmeasured.
The ranking prompt requests all IDs, but the existing parser accepts incomplete nonempty rankings;
current headline accuracy therefore does not establish full-ranking compliance. The relevance grade
conventions above are proposed for human ratification before labeling, not frozen scorer requirements.

Source of truth: `evals/bench/prompt.py` (visible material), `evals/bench/answer.py` (answer projection),
`evals/scorers/tools.py` (gold shapes), and ADR-0036 (automatic non-use and human labeling).

## Expected usefulness rubric — proposed for the next study

Outcome correctness and expected usefulness are separate judgments. Finish the outcome judgment
first. These annotations express a human hypothesis; repeated paired runs establish actual benefit.

| Annotation | Meaning |
| --- | --- |
| `necessary` | An explicit task or host requirement names a Jev check as a completion condition. Cite that requirement. Risk, difficulty, and the forced experimental arm's instruction do not establish necessity. |
| `useful` | A specific evidence conflict, multi-constraint choice or verification step gives a concrete reason an independent Jev judgment may improve the result. State that reason. |
| `optional` | A Jev check is reasonable, but supplied evidence gives no clear expectation of improvement. |
| `unnecessary` | A direct lookup, deterministic check or straightforward inference settles the task; no additional judgment is needed. |
| `unresolved` | The available context cannot support a usefulness judgment. Explain what is missing. |

Do not label an item useful merely because its source names a Jev tool. Do not treat a direct-model
mistake as proof Jev will repair it. The present question-only dataset may contain no `necessary`
items; report that honestly rather than filling quotas. No recall denominator or release threshold
is adopted by this packet. Pre-register those definitions for the next experiment, including how
`optional` and `unresolved` cases are handled.

## Adjudication and handoff

R3 records, per disputed item: both original judgments, the disputed fields, final `accept` and
`gold`, final usefulness annotation, decisive evidence, rationale and their human identity. Blocked
items stay blocked until resolved; editing a question invalidates its previous reviews and requires
both humans to review the revised packet. No silent exclusion of difficult items.

Before promotion, check full ID coverage, valid JSON, distinct human reviewers, reconciliation of
every disputed field, exact candidate/class/field IDs, and agreement between `gold` and `accept`.
Record original independent agreement in `label.agreed` (false remains false after adjudication),
the two humans in `label.labelers`, the adjudicator for disputed items in `label.adjudicator`, and
evidence plus provenance in `label.rationale`. Preserve the independent sheets alongside the final
records. Usefulness stays outside the frozen item schema.

Keep this existing 150-item corpus as a regression set. Its previous model outputs must not become
labels. For the new study, partition new scenarios by source family before tuning and reserve a
held-out set; the existing shared documents must not leak across a tuning/test split.

## Packet provenance

`packet.json` pins the source SHA-256 and repository revision. `questions.md` is generated from
`load_items()` and `prompt.render()`; only item IDs, tool names, structural scoring keys and the exact
visible prompts are included. Scoring keys identify the claim, classification item or extracted field;
they contain no answer cues.
Author targets, source references, severity tags, gold, label metadata and model answers are omitted.
The source dataset is unchanged. Both CSVs cover every packet item once with all judgments blank.
