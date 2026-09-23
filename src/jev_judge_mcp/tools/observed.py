"""Answer validators and policy functions as tools call them: each call is a span (ROADMAP P9).

`jev_judge_mcp.validation` and `jev_judge_mcp.policy` stay pure (ADR-0002); the spans are opened here, in the
tools layer. A validator call is a `jev.validate` span labelled with its kind and whether the answer
was valid — a rejected answer is a fail-closed. A policy call is a `jev.policy` span labelled with
the function and, when it returns one, the action.

The boundary `tests/unit/test_telemetry.py` enforces: every name in `TRACED` is imported by tools
from here, never from the pure packages — an import of a traced name straight from `policy` or
`validation` fails the suite. Untraced names are the documented exception: pure metrics that never
validate an answer and never decide an Action (`margin`, `top_probability`) may be imported from
`jev_judge_mcp.validation` directly. Adding a validator or Action-returning decider means wrapping it here
and adding it to `TRACED`, or its spans — and the fail-closed signal — go dark.
"""

from collections.abc import Callable
from functools import wraps

from jev_judge_mcp import policy, validation
from jev_judge_mcp.telemetry import ACTIONS, span

TRACED: set[str] = set()
"""Every traced name; tools must not import these from `jev_judge_mcp.policy` or `jev_judge_mcp.validation`."""


def _validator[**P, R](kind: str, validate: Callable[P, R | None]) -> Callable[P, R | None]:
    TRACED.add(validate.__name__)

    @wraps(validate)
    def traced(*args: P.args, **kwargs: P.kwargs) -> R | None:
        with span("jev.validate", kind=kind) as current:
            answer = validate(*args, **kwargs)
            current.attributes["valid"] = answer is not None
            return answer

    return traced


def _policy[**P, R](decide: Callable[P, R]) -> Callable[P, R]:
    TRACED.add(decide.__name__)

    @wraps(decide)
    def traced(*args: P.args, **kwargs: P.kwargs) -> R:
        with span("jev.policy", decision=decide.__name__) as current:
            result = decide(*args, **kwargs)
            if isinstance(result, str) and result in ACTIONS:
                current.attributes["action"] = result
            return result

    return traced


validate_choice = _validator("choice", validation.validate_choice)
validate_extract_choice = _validator("extract_choice", validation.validate_extract_choice)
validate_noul = _validator("noul", validation.validate_noul)
validate_score = _validator("score", validation.validate_score)
validate_rubric_answer = _validator("score", validation.validate_rubric_answer)

claim_action = _policy(policy.claim_action)
classification_decision = _policy(policy.classification_decision)
contradicts_recommendation = _policy(policy.contradicts_recommendation)
decide_extract_field = _policy(policy.decide_extract_field)
exists_verdict = _policy(policy.exists_verdict)
fail_closed = _policy(policy.fail_closed)
gate_reason_codes = _policy(policy.gate_reason_codes)
min_confidence = _policy(policy.min_confidence)
rank_candidates = _policy(policy.rank_candidates)
require_complete_context = _policy(policy.require_complete_context)
rerank_by_score = _policy(policy.rerank_by_score)
resolve_policy_thresholds = _policy(policy.resolve_policy_thresholds)
review_action = _policy(policy.review_action)
review_composite = _policy(policy.review_composite)
screen_fail_closed = _policy(policy.screen_fail_closed)
screen_recommendation = _policy(policy.screen_recommendation)
validate_policy_thresholds = _policy(policy.validate_policy_thresholds)
verify_action = _policy(policy.verify_action)
worst_action = _policy(policy.worst_action)
