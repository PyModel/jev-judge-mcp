"""`jev-judge-mcp install`. No arguments to the console script stay on the stdio server."""

import argparse
import getpass
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from shutil import which

from jev_judge_mcp.install.engine import TARGETS, Request, run
from jev_judge_mcp.install.errors import InstallError
from jev_judge_mcp.install.launch import checkout_launch, local_install_warning, pypi_launch
from jev_judge_mcp.install.layout import Layout, layout_from_env
from jev_judge_mcp.install.verify import verify_command


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="jev-judge-mcp install",
        description="Add the jev MCP server to local agent configs. Writes one named entry per target.",
    )
    parser.add_argument(
        "--agent",
        "-a",
        action="append",
        default=[],
        choices=TARGETS,
        help="agent to configure; repeatable",
    )
    parser.add_argument("--all", action="store_true", help="configure every detected agent")
    parser.add_argument("-y", "--yes", action="store_true", help="install without a confirmation prompt")
    parser.add_argument("--dry-run", action="store_true", help="print the redacted plan and write nothing")
    parser.add_argument("--remove", action="store_true", help="remove entries this installer recorded")
    parser.add_argument(
        "--from-checkout",
        action="store_true",
        help="launch this source checkout instead of the version-pinned PyPI package (ADR-0051)",
    )
    parser.add_argument("--force", action="store_true", help="replace an entry this installer does not own")
    parser.add_argument("--name", default="jev", help=argparse.SUPPRESS)
    parser.add_argument(
        "--desktop-key",
        action="store_true",
        help="after this consent, store TYPESAFE_API_KEY in GUI config files",
    )
    parsed = parser.parse_args(arguments)
    environ = os.environ
    try:
        uvx = which("uvx")
        if uvx is None:
            raise InstallError("uvx is not on PATH. Install uv from https://docs.astral.sh/uv/ and re-run.")
        launch = (
            checkout_launch(str(Path(uvx).resolve())) if parsed.from_checkout else pypi_launch(str(Path(uvx).resolve()))
        )
        layout = layout_from_env(environ, home=Path.home())
        key, secrets = _desktop_key(parsed.desktop_key, environ)
        confirmer = _confirmer(parsed.yes, parsed.dry_run)
        result = run(
            Request(
                layout=layout,
                launch=launch,
                agents=tuple(parsed.agent),
                select_all=parsed.all,
                assume_yes=parsed.yes,
                dry_run=parsed.dry_run,
                remove=parsed.remove,
                force=parsed.force,
                name=parsed.name,
                desktop_key=key,
                secrets=secrets,
                platform=sys.platform,
                bins=_bins(),
                key_in_environment=bool(environ.get("TYPESAFE_API_KEY")),
                confirmer=confirmer,
                chooser=_chooser(parsed.yes, parsed.dry_run),
                verify=None if parsed.dry_run or parsed.remove else verify_command,
            )
        )
    except InstallError as exc:
        sys.stdout.write(f"{exc}\n")
        return 1
    if not parsed.from_checkout:
        note = local_install_warning()
        if note is not None:
            sys.stdout.write(note + "\n\n")
    sys.stdout.write(result.text)
    return result.code


def _desktop_key(consent: bool, environ: Mapping[str, str]) -> tuple[str | None, tuple[str, ...]]:
    """Read the key from the environment or a hidden prompt. Never from argv, never printed."""
    existing = environ.get("TYPESAFE_API_KEY", "")
    secrets = (existing,) if existing else ()
    if not consent:
        return None, secrets
    if existing:
        return existing, secrets
    if not sys.stdin.isatty():
        return "", secrets
    prompted = getpass.getpass("TypeSafe API key (stored only in GUI config files): ")
    return prompted, (prompted,) if prompted else ()


def _confirmer(assume_yes: bool, dry_run: bool):
    if assume_yes or dry_run or not sys.stdin.isatty():
        return None

    def confirm(prompt: str) -> bool:
        sys.stdout.write(prompt + " ")
        sys.stdout.flush()
        answer = sys.stdin.readline().strip().lower()
        return answer in {"y", "yes"}

    return confirm


def _chooser(assume_yes: bool, dry_run: bool):
    if assume_yes or dry_run or not sys.stdin.isatty():
        return None

    def choose(prompt: str) -> tuple[str, ...]:
        sys.stdout.write(prompt + "\nAgents (comma-separated, empty cancels): ")
        sys.stdout.flush()
        raw = sys.stdin.readline().strip()
        if not raw:
            return ()
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    return choose


def _bins() -> dict[str, bool]:
    return {name: which(name) is not None for name in ("claude", "codex", "opencode", "pi", "omp", "pythinker")}


def layout_for_tests(environ: Mapping[str, str], home: Path) -> Layout:
    """Test helper: build a layout without consulting the real home directory."""
    return layout_from_env(
        environ,
        home=home,
        applications=home / "Applications",
        application_support=home / "Library" / "Application Support",
    )
