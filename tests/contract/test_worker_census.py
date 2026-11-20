"""Guard against host-wide command-line disclosure and unscoped extract-worker counts."""

import ast
import shlex
import subprocess
from collections import defaultdict
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER_SUPPORT = REPO / "tests/support/workers.py"
PROCESS_COMMANDS = {"ps", "pgrep"}
SHELL_RUNNERS = {"getoutput", "getstatusoutput"}
SUBPROCESS_RUNNERS = {"call", "check_call", "check_output", "Popen", "run"} | SHELL_RUNNERS


def owned_test_py_files(root: Path = REPO) -> list[Path]:
    """Repo-owned test modules, including untracked ones and excluding gitignored trees.

    An empty listing is an error: scanning nothing would pass vacuously."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "tests"],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )
    if listing.returncode != 0:
        raise RuntimeError(f"git could not list test files: {listing.stderr.strip()}")
    files = [root / line for line in listing.stdout.split("\0") if line.endswith(".py") and (root / line).is_file()]
    if not files:
        raise RuntimeError("git listed no test python files; refusing to scan nothing")
    return files


def _possible_commands(
    expr: ast.expr, bindings: dict[str, list[ast.expr]], visited: frozenset[str] = frozenset()
) -> Iterator[str]:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        yield expr.value
    elif isinstance(expr, (ast.List, ast.Tuple)) and expr.elts:
        yield from _possible_commands(expr.elts[0], bindings, visited)
    elif isinstance(expr, ast.Starred):
        yield from _possible_commands(expr.value, bindings, visited)
    elif isinstance(expr, ast.Name) and expr.id not in visited:
        for value in bindings.get(expr.id, ()):
            yield from _possible_commands(value, bindings, visited | {expr.id})
    elif isinstance(expr, ast.IfExp):
        yield from _possible_commands(expr.body, bindings, visited)
        yield from _possible_commands(expr.orelse, bindings, visited)


def _process_command(command: str, *, shell: bool) -> bool:
    tokens = shlex.shlex(command, posix=True, punctuation_chars=True) if shell else [command]
    if isinstance(tokens, shlex.shlex):
        tokens.whitespace_split = True
    return any(Path(token).name in PROCESS_COMMANDS for token in tokens)


def unsafe_process_listing(source: str) -> bool:
    """Find subprocess arguments that could invoke a process listing."""
    nodes = list(ast.walk(ast.parse(source)))
    bindings: dict[str, list[ast.expr]] = defaultdict(list)
    modules = {"subprocess"}
    imports: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            imports.update((alias.asname or alias.name, alias.name) for alias in node.names)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id].append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name):
            if node.value is not None:
                bindings[node.target.id].append(node.value)

    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        runner = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules:
            runner = func.attr
        elif isinstance(func, ast.Name):
            runner = imports.get(func.id)
        if runner not in SUBPROCESS_RUNNERS:
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        shell = kwargs.get("shell")
        uses_shell = runner in SHELL_RUNNERS or (isinstance(shell, ast.Constant) and shell.value is True)
        command = node.args[0] if node.args else kwargs.get("args", kwargs.get("cmd"))
        if command is not None and any(
            _process_command(value, shell=uses_shell) for value in _possible_commands(command, bindings)
        ):
            return True
        executable = kwargs.get("executable")
        if executable is not None and any(
            _process_command(value, shell=False) for value in _possible_commands(executable, bindings)
        ):
            return True
    return False


def unsafe_process_listings(files: Sequence[Path]) -> list[str]:
    if not files:
        raise ValueError("no test files to scan")
    return [
        str(path)
        for path in files
        if path.resolve() != WORKER_SUPPORT and unsafe_process_listing(path.read_text(encoding="utf-8"))
    ]


def test_process_listings_keep_host_arguments_private_and_worker_counts_scoped() -> None:
    assert unsafe_process_listings(owned_test_py_files()) == []


@pytest.mark.parametrize(
    "source",
    [
        'subprocess.run(["ps", "-ax", "-o", "pid=,command="])',
        'subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="])',
        'subprocess.run(["ps", "-A"])',
        'subprocess.run(["ps", "-ef"])',
        'subprocess.run(["ps", "auxww"])',
        'subprocess.run(["ps", "-ax", "-o", fields])',
        'subprocess.run(["ps", "-ef", "-p", str(pid)])',
        pytest.param('subprocess.run(["/bin/ps", "-ax"])', id="absolute-path"),
        pytest.param('subprocess.run("ps ax", shell=True)', id="shell-string"),
        pytest.param('argv = ["ps", "ax"]\nsubprocess.run(argv)', id="argv-variable"),
        pytest.param('subprocess.run(["pgrep", "-fl", "python"])', id="pgrep"),
        pytest.param('subprocess.run(["ps", "-p", str(pid)])', id="pid-scoped-outside-helper"),
        pytest.param(
            'from subprocess import run as execute\ncmd: str = "true; /bin/ps ax"\nexecute(args=cmd, shell=True)',
            id="import-alias-and-command-variable",
        ),
    ],
)
def test_guard_detects_process_queries(source: str) -> None:
    assert unsafe_process_listing(source)


def test_guard_ignores_non_process_commands_and_refuses_an_empty_scan(tmp_path: Path) -> None:
    unrelated = tmp_path / "test_unrelated.py"
    unrelated.write_text('subprocess.run(["docker", "ps"])\nrecord(["ps", "-A"])')
    assert unsafe_process_listings([unrelated]) == []
    with pytest.raises(ValueError, match="no test files"):
        unsafe_process_listings([])
