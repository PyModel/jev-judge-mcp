"""The third-party `regex` module stays out: stdlib `re` is the ADR-0004 engine (ADR-0018)."""

import ast
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from graphlib import TopologicalSorter
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9._-]+", requirement)
    assert match, requirement
    return match.group().lower().replace("_", "-")


def test_regex_is_not_a_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    assert "regex" not in {requirement_name(requirement) for requirement in requirements}


def test_src_does_not_import_regex() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name.split(".")[0] == "regex" for name in names):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_roadmap_mcp_row_quotes_the_pyproject_pin() -> None:
    """The ROADMAP dependency row states the pin pyproject enforces; the mcp row once said `<3`.

    pyproject caps `mcp` at the tested minor for the server's private-internals use, so a ROADMAP
    row promising the whole `<3` range invites an upgrade the pin refuses.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pin = next(requirement for requirement in project["dependencies"] if requirement_name(requirement) == "mcp")
    row = next(
        line
        for line in (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| MCP |")
    )
    assert f"`{pin}`" in row


def test_confinement_image_stages_a_fresh_wheel_and_locked_runtime(tmp_path: Path) -> None:
    shutil.copy(ROOT / "Makefile", tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    stale = dist / "jev_judge_mcp-old.whl"
    stale.touch()
    uv = tmp_path / "uv"
    uv.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        "  build) shift; out=dist; while [[ $# -gt 0 ]]; do\n"
        '    if [[ "$1" == --out-dir ]]; then out=$2; shift; fi; shift; done\n'
        '    touch "$out/jev_judge_mcp-current.whl" ;;\n'
        '  export) printf "%s\\n" "$*" > export-args; printf "httpx==1 --hash=sha256:abc\\n" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/bash\nfor context in "$@"; do :; done\necho "$context" > context\n'
        'find "$context" -maxdepth 1 -type f -exec basename {} \\; > inputs\n'
        'if [[ -f "$context/requirements.txt" ]]; then cat "$context/requirements.txt" > requirements; fi\n'
    )
    for stub in (uv, docker):
        stub.chmod(0o700)
    result = subprocess.run(
        ["make", "confinement-image"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert set((tmp_path / "inputs").read_text().splitlines()) == {"jev_judge_mcp-current.whl", "requirements.txt"}
    assert (tmp_path / "export-args").read_text().split() == [
        "export",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
    ]
    assert (tmp_path / "requirements").read_text() == "httpx==1 --hash=sha256:abc\n"
    assert stale.exists()
    assert not Path((tmp_path / "context").read_text().strip()).exists()


@pytest.mark.parametrize("count", [1, 2])
def test_confinement_install_consumes_one_wheel_and_locked_requirements(tmp_path: Path, count: int) -> None:
    dockerfile = (ROOT / "docker/eval-agent.Dockerfile").read_text()
    before, command = dockerfile.rsplit("\nRUN ", 1)
    copy = next(line for line in reversed(before.splitlines()) if line.startswith("COPY "))
    destination = shlex.split(copy)[-1].rstrip("/")
    (tmp_path / "requirements.txt").write_text("httpx==1 --hash=sha256:abc\n")
    for i in range(count):
        (tmp_path / f"jev_judge_mcp-0.{i}-py3-none-any.whl").touch()
    result = subprocess.run(
        [
            "sh",
            "-ec",
            'pip() { printf "pip %s\\n" "$*"; }; python3() { printf "python3 %s\\n" "$*"; };\n'
            + command.replace(destination, str(tmp_path)),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if count != 1:
        assert result.returncode != 0
        assert not result.stdout
        return
    assert result.returncode == 0, result.stderr
    runtime, package, check = result.stdout.splitlines()
    assert "--require-hashes" in runtime.split() and runtime.endswith(f"-r {tmp_path / 'requirements.txt'}")
    assert "--no-deps" in package.split() and package.endswith("jev_judge_mcp-0.0-py3-none-any.whl")
    assert check.startswith("python3 -c import jev_judge_mcp")


@pytest.fixture(scope="module")
def module_imports() -> dict[str, set[str]]:
    """Include function-local imports and resolve from-imports of package children."""
    src = ROOT / "src"
    modules = {
        ".".join(path.relative_to(src).with_suffix("").parts).removesuffix(".__init__"): path
        for path in (src / "jev_judge_mcp").rglob("*.py")
    }
    graph: dict[str, set[str]] = {}
    for module, path in modules.items():
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        imports: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = resolve_name("." * node.level + (node.module or ""), package)
                for alias in node.names:
                    child = f"{base}.{alias.name}"
                    imports.add(child if child in modules else base)
        graph[module] = imports.intersection(modules)
    return graph


def test_module_imports_are_acyclic(module_imports: dict[str, set[str]]) -> None:
    TopologicalSorter(module_imports).prepare()


def test_tools_do_not_import_hooks(module_imports: dict[str, set[str]]) -> None:
    assert not {
        (module, dep)
        for module, deps in module_imports.items()
        if module == "jev_judge_mcp.tools" or module.startswith("jev_judge_mcp.tools.")
        for dep in deps
        if dep.startswith("jev_judge_mcp.hook")
    }
