# Official TypeSafe / Jev documentation (crawled)

Verbatim copies of the official TypeSafe documentation, fetched 2026-09-27 from the site index
<https://docs.typesafe.ai/llms.txt> with Firecrawl (live fetch, `maxAge: 0`). Every file starts with its
source URL and fetch date, and each host gets its own directory under `.cache/jev-docs/` that mirrors
the site's URL paths. The live site is the source of truth; when a copy and <https://docs.typesafe.ai>
disagree, the live site wins.

> **Local copies only.** TypeSafe's Terms of Use (<https://typesafe.ai/legal/terms> §3(b)(ii), §4)
> prohibit reproducing or distributing Site materials, and the Site includes subdomains such as
> docs.typesafe.ai. The Master Customer Agreement (<https://typesafe.ai/legal/mca> §11, §14.1) keeps
> TypeSafe's rights in the Documentation and names it TypeSafe Confidential Information. So the pages
> live in the gitignored reference cache `.cache/jev-docs/`, on the owner's machine only, and are not
> packaged; a fresh clone has this index and no pages. [`corpus.yaml`](corpus.yaml) records the
> sources, retrieval date and page counts. Tracked here: this index, `corpus.yaml`, and the short
> repo-authored summaries [`models.md`](models.md) and [`primitives.md`](primitives.md). See
> [`docs/research/jev-best-use.md`](../research/jev-best-use.md) § 1.

Analysis built on these pages: [`docs/research/jev-best-use.md`](../research/jev-best-use.md).

## docs.typesafe.ai (112 pages)

### Introduction

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Introduction | `.cache/jev-docs/docs.typesafe.ai/introduction.md` | <https://docs.typesafe.ai/introduction.md> |
| Quick start | `.cache/jev-docs/docs.typesafe.ai/introduction/quickstart.md` | <https://docs.typesafe.ai/introduction/quickstart.md> |
| Jev with coding agents | `.cache/jev-docs/docs.typesafe.ai/introduction/coding-agents.md` | <https://docs.typesafe.ai/introduction/coding-agents.md> |
| AI primer | `.cache/jev-docs/docs.typesafe.ai/introduction/machine-learning-primer.md` | <https://docs.typesafe.ai/introduction/machine-learning-primer.md> |

### Concepts

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Example use cases | `.cache/jev-docs/docs.typesafe.ai/concepts/use-case-map.md` | <https://docs.typesafe.ai/concepts/use-case-map.md> |
| System One | `.cache/jev-docs/docs.typesafe.ai/concepts/system-one.md` | <https://docs.typesafe.ai/concepts/system-one.md> |
| State | `.cache/jev-docs/docs.typesafe.ai/concepts/state.md` | <https://docs.typesafe.ai/concepts/state.md> |
| Confidence | `.cache/jev-docs/docs.typesafe.ai/confidence.md` | <https://docs.typesafe.ai/confidence.md> |
| How to build with TypeSafe | `.cache/jev-docs/docs.typesafe.ai/concepts/how-to-build-with-system-one.md` | <https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md> |

### Primitives

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Primitives (Questions) | `.cache/jev-docs/docs.typesafe.ai/primitives.md` | <https://docs.typesafe.ai/primitives.md> |
| Choice | `.cache/jev-docs/docs.typesafe.ai/primitives/choice.md` | <https://docs.typesafe.ai/primitives/choice.md> |
| Score | `.cache/jev-docs/docs.typesafe.ai/primitives/score.md` | <https://docs.typesafe.ai/primitives/score.md> |
| Noul | `.cache/jev-docs/docs.typesafe.ai/primitives/noul.md` | <https://docs.typesafe.ai/primitives/noul.md> |
| Advanced: structure | `.cache/jev-docs/docs.typesafe.ai/primitives/advanced.md` | <https://docs.typesafe.ai/primitives/advanced.md> |

### Patterns

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Patterns | `.cache/jev-docs/docs.typesafe.ai/patterns.md` | <https://docs.typesafe.ai/patterns.md> |
| Speculative fan-out | `.cache/jev-docs/docs.typesafe.ai/patterns/fan-out.md` | <https://docs.typesafe.ai/patterns/fan-out.md> |
| Confidence-gated routing | `.cache/jev-docs/docs.typesafe.ai/patterns/confidence-routing.md` | <https://docs.typesafe.ai/patterns/confidence-routing.md> |
| Composite scoring | `.cache/jev-docs/docs.typesafe.ai/patterns/composite-scoring.md` | <https://docs.typesafe.ai/patterns/composite-scoring.md> |
| Intent routing | `.cache/jev-docs/docs.typesafe.ai/patterns/intent-routing.md` | <https://docs.typesafe.ai/patterns/intent-routing.md> |

### Cookbooks

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Cookbooks | `.cache/jev-docs/docs.typesafe.ai/cookbooks.md` | <https://docs.typesafe.ai/cookbooks.md> |
| Self-consistency: nouls | `.cache/jev-docs/docs.typesafe.ai/cookbooks/consistency_noul_cookbook.md` | <https://docs.typesafe.ai/cookbooks/consistency_noul_cookbook.md> |
| Self-consistency: choices | `.cache/jev-docs/docs.typesafe.ai/cookbooks/consistency_choice_cookbook.md` | <https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook.md> |
| Parallel questions | `.cache/jev-docs/docs.typesafe.ai/cookbooks/parallel_questions.md` | <https://docs.typesafe.ai/cookbooks/parallel_questions.md> |
| Re-ranking | `.cache/jev-docs/docs.typesafe.ai/cookbooks/rerank_typesafe.md` | <https://docs.typesafe.ai/cookbooks/rerank_typesafe.md> |
| Line-by-line search | `.cache/jev-docs/docs.typesafe.ai/cookbooks/semantic_find.md` | <https://docs.typesafe.ai/cookbooks/semantic_find.md> |
| Structure recovery | `.cache/jev-docs/docs.typesafe.ai/cookbooks/autoformat.md` | <https://docs.typesafe.ai/cookbooks/autoformat.md> |
| Function calling | `.cache/jev-docs/docs.typesafe.ai/cookbooks/function_calling.md` | <https://docs.typesafe.ai/cookbooks/function_calling.md> |
| Skill suggestion | `.cache/jev-docs/docs.typesafe.ai/cookbooks/skill_suggestion.md` | <https://docs.typesafe.ai/cookbooks/skill_suggestion.md> |
| Knowledge graph entity alignment | `.cache/jev-docs/docs.typesafe.ai/cookbooks/entity_alignment.md` | <https://docs.typesafe.ai/cookbooks/entity_alignment.md> |
| Classifying RAG passages | `.cache/jev-docs/docs.typesafe.ai/cookbooks/classifying_rag_passages.md` | <https://docs.typesafe.ai/cookbooks/classifying_rag_passages.md> |
| Double-checking citations | `.cache/jev-docs/docs.typesafe.ai/cookbooks/citation_check.md` | <https://docs.typesafe.ai/cookbooks/citation_check.md> |
| Guardrails for LLMs | `.cache/jev-docs/docs.typesafe.ai/cookbooks/llm_guardrails.md` | <https://docs.typesafe.ai/cookbooks/llm_guardrails.md> |
| SDE cascade | `.cache/jev-docs/docs.typesafe.ai/cookbooks/sde_cascade.md` | <https://docs.typesafe.ai/cookbooks/sde_cascade.md> |
| Date extraction | `.cache/jev-docs/docs.typesafe.ai/cookbooks/date_extraction_cookbook.md` | <https://docs.typesafe.ai/cookbooks/date_extraction_cookbook.md> |
| Pre-parsed value extraction | `.cache/jev-docs/docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md` | <https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook.md> |
| Hierarchical classification | `.cache/jev-docs/docs.typesafe.ai/cookbooks/hierarchical_classification.md` | <https://docs.typesafe.ai/cookbooks/hierarchical_classification.md> |
| Autoresearch feature discovery | `.cache/jev-docs/docs.typesafe.ai/cookbooks/autoresearch_feature_discovery.md` | <https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery.md> |
| Classification using confidence | `.cache/jev-docs/docs.typesafe.ai/cookbooks/classification_using_confidence.md` | <https://docs.typesafe.ai/cookbooks/classification_using_confidence.md> |

### Demos

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Demos | `.cache/jev-docs/docs.typesafe.ai/demos.md` | <https://docs.typesafe.ai/demos.md> |
| Smart home assistant demo | `.cache/jev-docs/docs.typesafe.ai/demos/smart-home.md` | <https://docs.typesafe.ai/demos/smart-home.md> |

### Models and limits

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Models | `.cache/jev-docs/docs.typesafe.ai/models.md` | <https://docs.typesafe.ai/models.md> |
| Jev 1.13 jaggedness | `.cache/jev-docs/docs.typesafe.ai/model-jaggedness/jev-1.13.md` | <https://docs.typesafe.ai/model-jaggedness/jev-1.13.md> |

### API reference

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| API reference | `.cache/jev-docs/docs.typesafe.ai/api.md` | <https://docs.typesafe.ai/api.md> |

### Agent integration

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Agent skill | `.cache/jev-docs/docs.typesafe.ai/agent-skill.md` | <https://docs.typesafe.ai/agent-skill.md> |
| MCP | `.cache/jev-docs/docs.typesafe.ai/mcp.md` | <https://docs.typesafe.ai/mcp> |

### SDKs

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Client SDKs | `.cache/jev-docs/docs.typesafe.ai/sdk.md` | <https://docs.typesafe.ai/sdk.md> |
| TypeSafe Python SDK | `.cache/jev-docs/docs.typesafe.ai/sdk/python.md` | <https://docs.typesafe.ai/sdk/python.md> |
| Usage | `.cache/jev-docs/docs.typesafe.ai/sdk/python/usage.md` | <https://docs.typesafe.ai/sdk/python/usage.md> |
| Changelog | `.cache/jev-docs/docs.typesafe.ai/sdk/python/changelog.md` | <https://docs.typesafe.ai/sdk/python/changelog.md> |
| API reference | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api.md` | <https://docs.typesafe.ai/sdk/python/api.md> |
| Asynchronous client | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/clients/async.md` | <https://docs.typesafe.ai/sdk/python/api/clients/async.md> |
| Synchronous client | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/clients/sync.md` | <https://docs.typesafe.ai/sdk/python/api/clients/sync.md> |
| Questions | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/types/questions.md` | <https://docs.typesafe.ai/sdk/python/api/types/questions.md> |
| Answers and responses | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/types/responses.md` | <https://docs.typesafe.ai/sdk/python/api/types/responses.md> |
| Retries | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/retries.md` | <https://docs.typesafe.ai/sdk/python/api/retries.md> |
| Common types | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/types/common.md` | <https://docs.typesafe.ai/sdk/python/api/types/common.md> |
| Exceptions | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/exceptions.md` | <https://docs.typesafe.ai/sdk/python/api/exceptions.md> |
| Constants | `.cache/jev-docs/docs.typesafe.ai/sdk/python/api/constants.md` | <https://docs.typesafe.ai/sdk/python/api/constants.md> |
| JavaScript SDK | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript.md` | <https://docs.typesafe.ai/sdk/javascript.md> |
| Changelog | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/changelog.md` | <https://docs.typesafe.ai/sdk/javascript/changelog.md> |
| API reference | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api.md` | <https://docs.typesafe.ai/sdk/javascript/api.md> |
| Class: APIConnectionError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/APIConnectionError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/APIConnectionError.md> |
| Class: APIError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/APIError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/APIError.md> |
| Class: APIPromise&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/APIPromise.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/APIPromise.md> |
| Class: APITimeoutError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/APITimeoutError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/APITimeoutError.md> |
| Class: APIUserAbortError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/APIUserAbortError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/APIUserAbortError.md> |
| Class: AuthenticationError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/AuthenticationError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/AuthenticationError.md> |
| Class: BadRequestError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/BadRequestError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/BadRequestError.md> |
| Class: InternalServerError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/InternalServerError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/InternalServerError.md> |
| Class: NotFoundError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/NotFoundError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/NotFoundError.md> |
| Class: PermissionDeniedError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/PermissionDeniedError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/PermissionDeniedError.md> |
| Class: RateLimitError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/RateLimitError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/RateLimitError.md> |
| Class: TypeSafeClient | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/TypeSafeClient.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/TypeSafeClient.md> |
| Class: TypeSafeError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/TypeSafeError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/TypeSafeError.md> |
| Class: UnprocessableEntityError | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/classes/UnprocessableEntityError.md` | <https://docs.typesafe.ai/sdk/javascript/api/classes/UnprocessableEntityError.md> |
| Interface: ChoiceQuestion&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceQuestion.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceQuestion.md> |
| Interface: ChoiceResponse&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceResponse.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/ChoiceResponse.md> |
| Interface: Logger | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/Logger.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/Logger.md> |
| Interface: ModelCard | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/ModelCard.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/ModelCard.md> |
| Interface: Models | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/Models.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/Models.md> |
| Interface: NoulQuestion | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/NoulQuestion.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/NoulQuestion.md> |
| Interface: NoulResponse | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/NoulResponse.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/NoulResponse.md> |
| Interface: Questions | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/Questions.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/Questions.md> |
| Interface: RequestOptions | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/RequestOptions.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/RequestOptions.md> |
| Interface: RetryPolicy | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/RetryPolicy.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/RetryPolicy.md> |
| Interface: ScoreQuestion&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/ScoreQuestion.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/ScoreQuestion.md> |
| Interface: ScoreResponse&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/ScoreResponse.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/ScoreResponse.md> |
| Interface: SystemOneRequest&lt;Q&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/SystemOneRequest.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/SystemOneRequest.md> |
| Interface: SystemOneRequestPayload | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/SystemOneRequestPayload.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/SystemOneRequestPayload.md> |
| Interface: SystemOneResult&lt;Q&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/SystemOneResult.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/SystemOneResult.md> |
| Interface: TypeSafeClientConfig | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/TypeSafeClientConfig.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/TypeSafeClientConfig.md> |
| Interface: Usage | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/Usage.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/Usage.md> |
| Interface: WithResponse&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/interfaces/WithResponse.md` | <https://docs.typesafe.ai/sdk/javascript/api/interfaces/WithResponse.md> |
| Type Alias: ChoiceCriteria | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/ChoiceCriteria.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/ChoiceCriteria.md> |
| Type Alias: Description | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/Description.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/Description.md> |
| Type Alias: EntryType | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/EntryType.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/EntryType.md> |
| Type Alias: EnvVar | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/EnvVar.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/EnvVar.md> |
| Type Alias: Fetch | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/Fetch.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/Fetch.md> |
| Type Alias: JsonValue | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/JsonValue.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/JsonValue.md> |
| Type Alias: LogLevel | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/LogLevel.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/LogLevel.md> |
| Type Alias: Question | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/Question.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/Question.md> |
| Type Alias: ResultFor&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/ResultFor.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/ResultFor.md> |
| Type Alias: ScoreCriteria | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/ScoreCriteria.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/ScoreCriteria.md> |
| Type Alias: ScoreLegend&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/ScoreLegend.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/ScoreLegend.md> |
| Type Alias: ScoreOf&lt;T&gt; | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/type-aliases/ScoreOf.md` | <https://docs.typesafe.ai/sdk/javascript/api/type-aliases/ScoreOf.md> |
| Variable: ENV | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/variables/ENV.md` | <https://docs.typesafe.ai/sdk/javascript/api/variables/ENV.md> |
| Variable: LOG_LEVELS | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/variables/LOG_LEVELS.md` | <https://docs.typesafe.ai/sdk/javascript/api/variables/LOG_LEVELS.md> |
| Variable: VERSION | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/variables/VERSION.md` | <https://docs.typesafe.ai/sdk/javascript/api/variables/VERSION.md> |
| Function: choice() | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/functions/choice.md` | <https://docs.typesafe.ai/sdk/javascript/api/functions/choice.md> |
| Function: noul() | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/functions/noul.md` | <https://docs.typesafe.ai/sdk/javascript/api/functions/noul.md> |
| Function: score() | `.cache/jev-docs/docs.typesafe.ai/sdk/javascript/api/functions/score.md` | <https://docs.typesafe.ai/sdk/javascript/api/functions/score.md> |

### Legal

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| Legal | `.cache/jev-docs/docs.typesafe.ai/legal.md` | <https://docs.typesafe.ai/legal.md> |

## Other official sources

| Page | Local copy (owner's machine; not a link) | Source URL |
| --- | --- | --- |
| TypeSafe agent skill (SKILL.md, MIT) | `.cache/jev-docs/github.com/typesafe-ai/skills/skills/typesafe-ai/SKILL.md` | <https://raw.githubusercontent.com/typesafe-ai/skills/main/skills/typesafe-ai/SKILL.md> |
| Skills repository README | `.cache/jev-docs/github.com/typesafe-ai/skills/README.md` | <https://github.com/typesafe-ai/skills> |
| Skills repository licence (MIT) | `.cache/jev-docs/github.com/typesafe-ai/skills/LICENSE.md` | <https://raw.githubusercontent.com/typesafe-ai/skills/main/LICENSE> |
| Terms of Use | `.cache/jev-docs/typesafe.ai/legal/terms.md` | <https://typesafe.ai/legal/terms> |
| Master Customer Agreement | `.cache/jev-docs/typesafe.ai/legal/mca.md` | <https://typesafe.ai/legal/mca> |
| Acceptable Use Policy | `.cache/jev-docs/typesafe.ai/legal/acceptable-use-policy.md` | <https://typesafe.ai/legal/acceptable-use-policy> |
| Privacy Policy | `.cache/jev-docs/typesafe.ai/legal/privacy-policy.md` | <https://typesafe.ai/legal/privacy-policy> |
| Data Processing Agreement | `.cache/jev-docs/typesafe.ai/legal/data-processing.md` | <https://typesafe.ai/legal/data-processing> |

## Not saved

- `https://docs.typesafe.ai/llms-full.txt` — the whole site in one file; duplicates the pages above.
- `https://docs.typesafe.ai/migrating-to-v1.md` — linked from the official SKILL.md, returned *Page Not Found* on 2026-09-27.
- SDK and cookbook images (`mintcdn.com`) — referenced by URL inside the pages, not downloaded.
