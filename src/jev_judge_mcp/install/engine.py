"""Plan and apply one named MCP entry per agent. A failure on one target does not stop the others."""

import difflib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.documents import (
    json_entry_path,
    read_codex_entry,
    read_entry,
    read_text,
    remove_codex,
    remove_json,
    render_codex,
    render_json,
    server_map,
)
from jev_judge_mcp.install.errors import ConfigChangedError, ConfigParseError, ConfigShapeError, InstallError
from jev_judge_mcp.install.fs import (
    atomic_write,
    entry_hash,
    file_mode,
    load_state,
    mode_is_loose,
    read_bytes,
    state_hash,
    write_backup,
    write_state,
)
from jev_judge_mcp.install.launch import (
    PACKAGE,
    Launch,
    claude_code_entry,
    claude_desktop_entry,
    codex_entry,
    cursor_entry,
    omp_entry,
    opencode_entry,
    pi_entry,
    pythinker_entry,
    supported_spec,
    with_preserved_literal,
)
from jev_judge_mcp.install.layout import (
    PI_ADAPTER_HINT,
    Layout,
    chatgpt_installed,
    pi_adapter_installed,
    pythinker_desktop_installed,
    running_on_macos,
)
from jev_judge_mcp.install.redact import holds_literal, redact_text

TARGETS: tuple[str, ...] = (
    "claude-code",
    "claude-desktop",
    "codex",
    "cursor",
    "opencode",
    "pi",
    "omp",
    "pythinker",
)
_NAME = re.compile(r"[A-Za-z0-9._-]+")


def _empty_bins() -> dict[str, bool]:
    return {}


_CHECKS: dict[str, str] = {
    "claude-code": "claude mcp get {name}",
    "claude-desktop": "restart Claude Desktop and confirm the {name} server",
    "codex": "codex mcp get {name}",
    "cursor": "restart Cursor and confirm the {name} server",
    "opencode": "opencode mcp list",
    "pi": "in Pi, run /mcp",
    "omp": "in omp, run /mcp test {name}",
    "pythinker": "in Pythinker, run /mcp",
}


@dataclass(frozen=True)
class Request:
    layout: Layout
    launch: Launch
    agents: tuple[str, ...] = ()
    select_all: bool = False
    assume_yes: bool = False
    dry_run: bool = False
    remove: bool = False
    force: bool = False
    name: str = "jev"
    desktop_key: str | None = None
    secrets: tuple[str, ...] = ()
    platform: str = "darwin"
    bins: Mapping[str, bool] = field(default_factory=_empty_bins)
    key_in_environment: bool = False
    confirmer: Callable[[str], bool] | None = None
    chooser: Callable[[str], tuple[str, ...]] | None = None
    verify: Callable[[list[str]], None] | None = None
    before_write: Callable[[Path], None] | None = None
    stamp: str | None = None


@dataclass
class RunResult:
    text: str
    code: int


@dataclass
class _Hit:
    target: str
    reason: str
    gui: bool


@dataclass
class _Action:
    target: str
    path: Path | None
    kind: str
    message: str
    overwrite: bool
    ok: bool
    before: bytes | None = None
    after: str | None = None
    entry: dict[str, object] | None = None
    literal: bool = False
    diff: str = ""


def run(request: Request) -> RunResult:
    """Detect, summarize, and write. `--dry-run` prints a redacted diff and writes nothing."""
    _validate(request)
    hits, pi_directory_without_adapter = _survey(request)
    selected, lines, code, pi_failed = _select(request, hits, pi_directory_without_adapter)
    if code is not None:
        return RunResult(redact_text("\n".join(lines) + "\n", request.secrets), code)

    actions = [_plan(request, target, hits) for target in selected]
    lines.extend(_describe(request, hits, actions))
    writing = [action for action in actions if action.after is not None and action.ok]
    if request.dry_run:
        lines.extend(_diffs(actions))
        lines.append("dry-run: wrote nothing")
        return RunResult(_finish(request, lines, actions, applied=False), _exit_code(actions, pi_failed))
    if writing and not request.assume_yes:
        prompt = "\n".join(lines) + "\nProceed with installation? [y/N]"
        if request.confirmer is None:
            lines.append("pass -y to install without a prompt")
            return RunResult(redact_text("\n".join(lines) + "\n", request.secrets), 1)
        if not request.confirmer(redact_text(prompt, request.secrets)):
            lines.append("installation cancelled")
            return RunResult(redact_text("\n".join(lines) + "\n", request.secrets), 1 if pi_failed else 0)
    _apply(request, actions)
    lines.extend(_applied_lines(actions))
    return RunResult(_finish(request, lines, actions, applied=True), _exit_code(actions, pi_failed))


def _validate(request: Request) -> None:
    if not _NAME.fullmatch(request.name):
        raise InstallError("server name must be letters, digits, '.', '_' or '-'")
    unknown = [agent for agent in request.agents if agent not in TARGETS]
    if unknown:
        raise InstallError(f"unknown agent {unknown[0]!r}; choose from {', '.join(TARGETS)}")
    if request.launch.args()[-1] != PACKAGE:
        raise InstallError("refusing to write a package name other than jev-judge-mcp")
    if not supported_spec(request.launch.spec):
        raise InstallError(
            "unsupported launch spec; use the version-pinned PyPI spec "
            "'jev-judge-mcp[typesafe]==<version>' or a checkout spec '<absolute path>[typesafe]'"
        )


def _survey(request: Request) -> tuple[list[_Hit], bool]:
    layout = request.layout
    bins = request.bins
    hits: list[_Hit] = []
    claude_code: list[str] = []
    if layout.claude_code_dir().exists():
        claude_code.append(f"{layout.claude_code_dir()} exists")
    if bins.get("claude"):
        claude_code.append("claude is on PATH")
    if claude_code:
        hits.append(_Hit("claude-code", "; ".join(claude_code), False))

    if running_on_macos(request.platform):
        desktop: list[str] = []
        support = layout.application_support / "Claude"
        app = layout.applications / "Claude.app"
        if support.exists():
            desktop.append(f"{support} exists")
        if app.exists():
            desktop.append(f"{app} exists")
        if desktop:
            hits.append(_Hit("claude-desktop", "; ".join(desktop), True))

    codex: list[str] = []
    if layout.codex_dir().exists():
        codex.append(f"{layout.codex_dir()} exists")
    if bins.get("codex"):
        codex.append("codex is on PATH")
    if codex:
        gui = chatgpt_installed(layout.applications)
        reason = "; ".join(codex)
        if gui:
            reason += "; ChatGPT.app shares this config"
        hits.append(_Hit("codex", reason, gui))

    cursor: list[str] = []
    if layout.cursor_dir().exists():
        cursor.append(f"{layout.cursor_dir()} exists")
    if bins.get("cursor"):
        cursor.append("cursor is on PATH")
    if cursor:
        hits.append(_Hit("cursor", "; ".join(cursor), False))

    opencode: list[str] = []
    if layout.opencode_dir().exists():
        opencode.append(f"{layout.opencode_dir()} exists")
    if bins.get("opencode"):
        opencode.append("opencode is on PATH")
    if opencode:
        hits.append(_Hit("opencode", "; ".join(opencode), False))

    pi_dir = layout.pi_dir()
    adapter = pi_adapter_installed(layout.pi_settings())
    pi_directory_without_adapter = pi_dir.exists() and not adapter
    if pi_dir.exists() and adapter:
        hits.append(_Hit("pi", f"{pi_dir} exists and pi-mcp-adapter is installed", False))

    omp: list[str] = []
    omp_root = layout.home / ".omp"
    if omp_root.exists():
        omp.append(f"{omp_root} exists")
    if bins.get("omp"):
        omp.append("omp is on PATH")
    if omp:
        hits.append(_Hit("omp", "; ".join(omp), False))

    pythinker: list[str] = []
    if layout.pythinker_dir().exists():
        pythinker.append(f"{layout.pythinker_dir()} exists")
    if bins.get("pythinker"):
        pythinker.append("pythinker is on PATH")
    if pythinker:
        gui = pythinker_desktop_installed(layout.applications)
        reason = "; ".join(pythinker)
        if gui:
            reason += "; com.pythinker.desktop shares this config"
        hits.append(_Hit("pythinker", reason, gui))
    return hits, pi_directory_without_adapter


def _select(
    request: Request,
    hits: list[_Hit],
    pi_directory_without_adapter: bool,
) -> tuple[list[str], list[str], int | None, bool]:
    lines: list[str] = []
    detected = [hit.target for hit in hits]
    if request.agents and request.select_all:
        selected = list(dict.fromkeys([*detected, *request.agents]))
    elif request.agents:
        selected = list(request.agents)
    else:
        selected = detected
        if not selected:
            listed = ", ".join(TARGETS)
            lines.append(f"No agents detected. Pass --agent for one of: {listed}")
            if request.assume_yes or request.chooser is None:
                return [], lines, 1, False
            chosen = request.chooser(lines[0])
            if not chosen:
                lines.append("installation cancelled")
                return [], lines, 0, False
            unknown = [agent for agent in chosen if agent not in TARGETS]
            if unknown:
                raise InstallError(f"unknown agent {unknown[0]!r}; choose from {listed}")
            selected = list(chosen)

    pi_failed = False
    automatic = not request.agents or request.select_all
    pi_requested = "pi" in selected or (automatic and pi_directory_without_adapter)
    if pi_requested and not pi_adapter_installed(request.layout.pi_settings()):
        lines.append(
            "Pi needs the MCP adapter before it can run this server. "
            f"Install it with `{PI_ADAPTER_HINT}`, then re-run. The Pi config was not changed."
        )
        selected = [target for target in selected if target != "pi"]
        pi_failed = True
        if not selected:
            return [], lines, 1, True
    return selected, lines, None, pi_failed


def _plan(request: Request, target: str, hits: list[_Hit]) -> _Action:
    if target == "claude-desktop" and not running_on_macos(request.platform):
        return _Action(target, None, "fail", "Claude Desktop install is macOS only", False, False)
    path = request.layout.target_path(target)
    if path.is_symlink() and not path.exists():
        return _Action(target, path, "fail", "broken symlink; left unchanged", False, False)
    hit = next((item for item in hits if item.target == target), None)
    gui = bool(hit and hit.gui) or _gui_for_unlisted(request, target)
    literal_key = _literal_key(target, gui, request.desktop_key)
    if literal_key == "":
        return _Action(
            target,
            path,
            "fail",
            "no TYPESAFE_API_KEY to store; set it in the environment or run in a terminal so it can be prompted",
            False,
            False,
        )
    if target == "claude-desktop" and literal_key is None:
        return _Action(target, path, "skip", "needs --desktop-key before the key is written", False, True)
    try:
        current, servers, text, before = _load(target, path, request.name)
        note = _other_servers(servers, request.name)
        desired = _desired(target, request.launch, literal_key)
        recorded = state_hash(load_state(request.layout.state_file()), target)
        if current is not None and literal_key is None and recorded == entry_hash(current):
            desired = with_preserved_literal(desired, current)
        action = _classify(request, target, path, current, desired, recorded, text, before)
    except (ConfigParseError, ConfigShapeError, UnicodeError) as exc:
        reason = exc.args[0] if exc.args else "unreadable config"
        return _Action(target, path, "fail", f"{reason}; left unchanged", False, False)
    action.message = (action.message + note).strip()
    action.literal = holds_literal(action.entry) or holds_literal(current)
    if gui and literal_key is None and target in {"codex", "pythinker"} and action.kind != "fail":
        who = "ChatGPT" if target == "codex" else "Pythinker desktop"
        action.message = (action.message + f" {who} was not given the key; re-run with --desktop-key.").strip()
    warning = _mode_warning(path, action.literal)
    if warning:
        action.message = (action.message + " " + warning).strip()
    return action


def _gui_for_unlisted(request: Request, target: str) -> bool:
    """GUI sharing still applies when the user named an agent that detection missed."""
    if target == "claude-desktop":
        return True
    if target == "codex":
        return chatgpt_installed(request.layout.applications)
    if target == "pythinker":
        return pythinker_desktop_installed(request.layout.applications)
    return False


def _literal_key(target: str, gui: bool, desktop_key: str | None) -> str | None:
    """None: do not store a literal. Empty string: consent without a key. Otherwise the key."""
    wants_literal = target == "claude-desktop" or (gui and target in {"codex", "pythinker"})
    if not wants_literal:
        return None
    if desktop_key is None:
        return None
    return desktop_key


def _desired(target: str, launch: Launch, literal_key: str | None) -> dict[str, object]:
    if target == "claude-code":
        return claude_code_entry(launch)
    if target == "claude-desktop":
        return claude_desktop_entry(launch, literal_key or "")
    if target == "cursor":
        return cursor_entry(launch)
    if target == "opencode":
        return opencode_entry(launch)
    if target == "pi":
        return pi_entry(launch)
    if target == "omp":
        return omp_entry(launch)
    if target == "pythinker":
        return pythinker_entry(launch, literal_key)
    if target == "codex":
        return codex_entry(launch, literal_key)
    raise InstallError(f"unknown agent {target!r}")


def _classify(
    request: Request,
    target: str,
    path: Path,
    current: dict[str, object] | None,
    desired: dict[str, object],
    recorded: str | None,
    text: str,
    before: bytes | None,
) -> _Action:
    if request.remove:
        if current is None:
            return _Action(target, path, "skip", "no entry to remove", False, True, before)
        if recorded is not None and recorded == entry_hash(current):
            after = _render_remove(target, text, request.name)
            return _Action(
                target,
                path,
                "remove",
                "",
                False,
                True,
                before,
                after,
                None,
                False,
                _diff(path, text, after, request.secrets),
            )
        return _Action(target, path, "skip", "left foreign entry", False, True, before)

    if current is None:
        after = _render_set(target, text, request.name, desired)
        return _Action(
            target,
            path,
            "add",
            "",
            False,
            True,
            before,
            after,
            desired,
            False,
            _diff(path, text, after, request.secrets),
        )
    if _same(current, desired):
        return _Action(target, path, "up to date", "", False, True, before, None, current)
    if recorded is not None and recorded == entry_hash(current):
        after = _render_set(target, text, request.name, desired)
        return _Action(
            target,
            path,
            "update",
            "",
            True,
            True,
            before,
            after,
            desired,
            False,
            _diff(path, text, after, request.secrets),
        )
    if request.force:
        after = _render_set(target, text, request.name, desired)
        return _Action(
            target,
            path,
            "update",
            "replacing foreign entry",
            True,
            True,
            before,
            after,
            desired,
            False,
            _diff(path, text, after, request.secrets),
        )
    return _Action(target, path, "skip", "foreign entry (use --force)", False, True, before, None, current)


def _load(target: str, path: Path, name: str) -> tuple[dict[str, object] | None, dict[str, object], str, bytes | None]:
    before = read_bytes(path)
    if before is None:
        return None, {}, "", None
    # utf-8-sig drops a leading BOM (files authored on, or copied from, Windows carry one); the
    # rewritten file simply no longer has it.
    text = before.decode("utf-8-sig")
    if target == "codex":
        entry, servers = read_codex_entry(text, name)
        return entry, servers, text, before
    document = read_text(text)
    path_keys = json_entry_path(target, document, name)
    return read_entry(document, path_keys), server_map(document, path_keys), text, before


def _render_set(target: str, text: str, name: str, entry: Mapping[str, object]) -> str:
    if target == "codex":
        return render_codex(text, name, entry)
    return render_json(target, text, name, entry)


def _render_remove(target: str, text: str, name: str) -> str:
    if target == "codex":
        return remove_codex(text, name)
    return remove_json(target, text, name)


def _same(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _other_servers(servers: Mapping[str, object], name: str) -> str:
    found: list[str] = []
    for key, value in servers.items():
        if key == name:
            continue
        try:
            blob = json.dumps(value)
        except TypeError:
            blob = ""
        if key == "evaluate" or "@jkudish/jev-mcp" in blob:
            found.append(key)
    if not found:
        return ""
    return " other Jev-related servers found: " + ", ".join(found)


def _diff(path: Path, before: str, after: str, secrets: tuple[str, ...]) -> str:
    lines = difflib.unified_diff(
        redact_text(before, secrets).splitlines(),
        redact_text(after, secrets).splitlines(),
        fromfile=str(path),
        tofile=str(path),
        lineterm="",
    )
    return "\n".join(lines)


def _mode_warning(path: Path, literal: bool) -> str:
    if not literal:
        return ""
    mode = file_mode(path)
    if mode is None or not mode_is_loose(mode):
        return ""
    return (
        f"mode {mode:04o} is looser than 0600. GUI apps rewrite this file, "
        "so the installer cannot keep the mode at 0600."
    )


def _describe(request: Request, hits: list[_Hit], actions: list[_Action]) -> list[str]:
    lines = [f"detected {hit.target}: {hit.reason}" for hit in hits]
    for action in actions:
        where = f" {action.path}" if action.path is not None else ""
        overwrite = f" overwrites: {request.name}" if action.overwrite else ""
        detail = f" {action.message}" if action.message else ""
        lines.append(f"{action.target}: {action.kind}{where}{overwrite}{detail}")
    return lines


def _diffs(actions: list[_Action]) -> list[str]:
    return [action.diff for action in actions if action.diff]


def _apply(request: Request, actions: list[_Action]) -> None:
    state = load_state(request.layout.state_file())
    targets = _mutable_targets(state)
    stamp = request.stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    changed = False
    for action in actions:
        if action.after is None or action.path is None or not action.ok:
            continue
        try:
            _write_one(request, action, stamp)
        except ConfigChangedError as exc:
            action.kind = "fail"
            action.ok = False
            action.message = str(exc)
            action.after = None
            continue
        except OSError as exc:
            action.kind = "fail"
            action.ok = False
            action.message = f"could not write {action.path}: {exc.strerror}"
            action.after = None
            continue
        changed = True
        record: dict[str, object] | None = None
        if action.kind == "remove":
            targets.pop(action.target, None)
        elif action.entry is not None:
            record = {
                "path": str(action.path),
                "name": request.name,
                "installer_version": _version(),
                "entry_sha256": entry_hash(action.entry),
            }
        warning = _mode_warning(action.path, holds_literal(action.entry) or action.literal)
        if warning and warning not in action.message:
            action.message = (action.message + " " + warning).strip()
        verified: bool | None = None
        if request.verify is not None and action.kind in {"add", "update"}:
            try:
                request.verify(request.launch.invoke())
            except Exception as exc:
                action.ok = False
                action.message = (action.message + f" verify failed: {exc}").strip()
                verified = False
            else:
                verified = True
        if record is not None:
            # A failed check stays recorded as `verified: false` so `--remove` still owns the
            # entry, while the summary and exit code report the failure (ADR-0051).
            if verified is not None:
                record["verified"] = verified
            targets[action.target] = record
    if changed:
        payload: dict[str, object] = {"targets": targets}
        _refuse_secret(payload, request.secrets)
        write_state(request.layout.state_file(), payload)


def _write_one(request: Request, action: _Action, stamp: str) -> None:
    path = action.path
    after = action.after
    if path is None or after is None:
        return
    current = read_bytes(path)
    if current != action.before:
        raise ConfigChangedError(f"{path}: changed during install, re-run")
    if current is not None:
        write_backup(request.layout.state_dir(), stamp, action.target, path.suffix, current)
    if request.before_write is not None:
        request.before_write(path)
    atomic_write(path, after, _write_mode(path, action.literal), action.before)


def _write_mode(path: Path, literal: bool) -> int:
    """A file that receives the literal key is tightened to 0600. A reference-only edit keeps its mode.

    Tightening masks off every bit outside owner read and write, so a 0644 file becomes 0600 and a
    mode that is already stricter stays stricter. A new file is 0600 either way.
    """
    existing = file_mode(path)
    if existing is None:
        return 0o600
    if literal:
        return existing & 0o600
    return existing


def _mutable_targets(state: Mapping[str, object]) -> dict[str, dict[str, object]]:
    raw = state.get("targets")
    if not is_json_object(raw):
        return {}
    cleaned: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if is_json_object(value):
            cleaned[key] = dict(value)
    return cleaned


def _refuse_secret(payload: Mapping[str, object], secrets: tuple[str, ...]) -> None:
    blob = json.dumps(payload)
    for secret in secrets:
        if secret and secret in blob:
            raise InstallError("refusing to record the API key in the state file")


def _applied_lines(actions: list[_Action]) -> list[str]:
    lines: list[str] = []
    for action in actions:
        if action.kind == "fail":
            lines.append(f"{action.target}: fail {action.message}")
        elif not action.ok:
            # The write succeeded but the post-write check failed; say so (ADR-0051).
            lines.append(f"{action.target}: {action.kind} {action.message}".strip())
        elif action.message and "mode " in action.message:
            lines.append(f"{action.target}: {action.message}")
    return lines


def _finish(request: Request, lines: list[str], actions: list[_Action], *, applied: bool) -> str:
    if applied and not request.remove:
        for action in actions:
            if action.ok and action.kind in {"add", "update", "up to date"}:
                lines.append("check: " + _CHECKS[action.target].format(name=request.name))
        if not request.key_in_environment:
            lines.append(
                "TYPESAFE_API_KEY is unset in this environment. "
                "Terminal agents start without a key until you export it."
            )
    text = "\n".join(lines).rstrip() + "\n"
    return redact_text(text, request.secrets)


def _exit_code(actions: list[_Action], pi_failed: bool) -> int:
    if pi_failed or any(not action.ok for action in actions):
        return 1
    return 0


def _version() -> str:
    try:
        return version(PACKAGE)
    except PackageNotFoundError:
        return "0.0.0"
