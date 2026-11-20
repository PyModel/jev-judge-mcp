"""Answer validators as tools call them: each call is a span (ROADMAP P9).

`jev_judge_mcp.validation` stays pure (ADR-0002); the spans are opened here, in the
tools layer. A validator call is a `jev.validate` span labelled with its kind and whether the answer
was valid — a rejected answer is a fail-closed. Tools import pure policy functions directly.

The boundary `tests/unit/test_telemetry.py` enforces: every name in `TRACED` is imported by tools
from here, never from the pure packages — an import of a traced name straight from `validation`
fails the suite. Untraced names are the documented exception: pure metrics that never
validate an answer and never decide an Action (`margin`, `top_probability`) may be imported from
`jev_judge_mcp.validation` directly. Adding a validator means wrapping it here
and adding it to `TRACED`, or its span — and the fail-closed signal — goes dark.
"""

from collections.abc import Callable
from functools import wraps

from jev_judge_mcp import validation
from jev_judge_mcp.telemetry import span

TRACED: set[str] = set()
"""Every traced name; tools must not import these from `jev_judge_mcp.validation`."""


def _validator[**P, R](kind: str, validate: Callable[P, R | None]) -> Callable[P, R | None]:
    TRACED.add(validate.__name__)

    @wraps(validate)
    def traced(*args: P.args, **kwargs: P.kwargs) -> R | None:
        with span("jev.validate", kind=kind) as current:
            answer = validate(*args, **kwargs)
            current.attributes["valid"] = answer is not None
            return answer

    return traced


validate_choice = _validator("choice", validation.validate_choice)
validate_extract_choice = _validator("extract_choice", validation.validate_extract_choice)
validate_noul = _validator("noul", validation.validate_noul)
validate_score = _validator("score", validation.validate_score)
validate_rubric_answer = _validator("score", validation.validate_rubric_answer)
