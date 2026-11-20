"""Fail when a pull request title is not a Conventional Commit subject.

    PR_TITLE='fix: report oversized git ranges' check_pr_title.py

Pull requests are squash-merged with the title as the commit subject on `main`, and
release-please reads those subjects for the changelog and the next release. A title it
cannot parse ships without a changelog entry.
Exit 0 when the title parses, 1 when it does not.
"""

import os
import re
import sys

TYPES = ("build", "chore", "ci", "deps", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test")
TITLE = re.compile(rf"(?:{'|'.join(TYPES)})(?:\([\w./-]+\))?!?: \S.*", re.ASCII)


def main() -> int:
    title = os.environ.get("PR_TITLE", "")
    if TITLE.fullmatch(title):
        return 0
    print(
        f"pull request title is not a Conventional Commit: {title!r}\n"
        f"expected `<type>[(scope)][!]: <subject>` with type one of: {', '.join(TYPES)}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
