"""Pattern redaction for a gated action, ported from jev-use ``src/redact.ts``.

This is not the ADR-0017 error redactor (``secret_values()`` / ``Redactor``).
It does not scrub caller-written tool state. The command hook is the caller.
"""

import re

from jev_judge_mcp.redaction.literals import _mixed_case_with_digit  # pyright: ignore[reportPrivateUsage]
from jev_judge_mcp.redaction.values import REDACTED

# `$NAME`, `${NAME}`, `%NAME%`, or `$(...)`. The judge still sees the name.
_REFERENCE = re.compile(r"^(?:\$\{?\w+\}?|%\w+%|\$\(.*\))$", re.ASCII)


def _compile(pattern: str, *, ignore_case: bool = False) -> re.Pattern[str]:
    flags = re.ASCII
    if ignore_case:
        flags |= re.IGNORECASE
    return re.compile(pattern, flags)


_SK_TOKEN = _compile(r"(?<![A-Za-z0-9_-])(sk-(?:proj-|ant-|live-|test-)?)([A-Za-z0-9_-]{16,})")

# Group 1 is kept. Group 2 is the secret. A secret stops at a quote or a
# backslash so the `\` of a JSON `\"` stays in the text.
_RULES: tuple[re.Pattern[str], ...] = (
    # Visit each scheme-like run once. Every candidate start in the run has the same suffix.
    _compile(r"((?<![\w+.-])(?>[\w+.-]*?\b[a-z])[\w+.-]*://[^\s:/@]+:)([^\s@/]+)(?=@)", ignore_case=True),
    _compile(
        r"((?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth-token)"
        r""""?\s*:\s*"?(?:(?:bearer|basic|token|digest)\s+)?)([^"'\n\\]+)""",
        ignore_case=True,
    ),
    _compile(
        r"((?:^|\s)--?[\w-]*(?:pass(?:wd|word)?|token|api-?key|secret|auth)[\w-]*[ =]+"
        r""""?)([^\s"'\\]+)""",
        ignore_case=True,
    ),
    _compile(
        r"""((?:^|\s)(?:-u|--user)[ =]+"?[^\s:"']*:)([^\s"'\\]+)""",
    ),
    _compile(
        # The lookbehind anchors the scan at a token boundary, so a long run of word characters
        # costs one pass instead of one pass per offset (see credential_literal._ASSIGNMENT).
        r"""((?<![\w.-])[\w.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"""
        r"""private[_-]?key|credentials?)["']?\s*[:=]\s*"?)([^\s"',;}\\]+)""",
        ignore_case=True,
    ),
    _SK_TOKEN,
    _compile(r"(?<![A-Za-z0-9_-])(gh[pousr]_|github_pat_)([A-Za-z0-9_]{20,})"),
    _compile(r"(xox[abdeporsu]-)([A-Za-z0-9-]{10,})"),
    _compile(r"(AKIA)([0-9A-Z]{16})\b"),
    _compile(r"(?<![A-Za-z0-9_-])(AIza)([\w-]{30,})"),
    _compile(r"(?<![A-Za-z0-9_-])(eyJ)([\w-]{8,}\.[\w-]{8,}\.[\w-]+)"),
)


def _is_reference(value: str) -> bool:
    return _REFERENCE.match(value) is not None


def _replace(match: re.Match[str]) -> str:
    keep = match.group(1)
    secret = match.group(2)
    assert keep is not None and secret is not None
    if _is_reference(secret) or (match.re is _SK_TOKEN and not _mixed_case_with_digit(match)):
        return match.group(0)
    return keep + REDACTED


def redact_action(text: str) -> str:
    """Return ``text`` with credential values replaced by ``[redacted]``.

    Port of jev-use ``src/redact.ts``. Not the ADR-0017 error redactor, and not
    a scrub of caller-written tool state. The command hook is the caller.
    """
    for rule in _RULES:
        text = rule.sub(_replace, text)
    return text
