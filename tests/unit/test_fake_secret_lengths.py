"""Catch short fake credentials before they collide with unrelated text in tests.

The static rule checks literal credential-named assignments/kwargs, string-keyed env/config
mappings, `setenv` calls, and positional secrets for the known redaction APIs — in `test_*.py`
files and in `conftest.py` (one AST scan). JSON and JSONL fixtures under tests/ are scanned for
credential-named keys with short string values (ADR-0059): recorded fixture content is output
text, so a short fake there collides exactly like one in a test file. No YAML fixtures exist
under tests/; a format that lands joins the JSON scan. Empty strings mean "unset"; only the
exact exemptions below are allowed.
"""

import ast
import json
import re
from pathlib import Path

from jev_judge_mcp.server import MIN_SECRET_LENGTH

ROOT = Path(__file__).resolve().parents[2]
POSITIONAL_SECRET_PARAMETERS = {"Redactor": (0,), "configure_logging": (1,)}
ALLOWED_SHORT_SECRET_USES = {
    (
        "tests/integration/test_http_auth.py",
        "test_a_short_configured_secret_refuses_startup_naming_the_variable",
        "TYPESAFE_API_KEY",
        "abc",
    ),
    (
        "tests/unit/test_http_auth.py",
        "test_a_short_configured_secret_refuses_startup_naming_the_variable",
        "TYPESAFE_API_KEY",
        "abc",
    ),
    (
        "tests/unit/test_http_auth.py",
        "test_an_http_token_under_the_redaction_floor_is_refused_too",
        "JEV_MCP_HTTP_TOKEN",
        "tok",
    ),
    # Recorded agent output: Claude's stream metadata names where a key would come from; it is an
    # enum, never a credential (ADR-0059).
    (
        "tests/evals/data/claude-stream-json-sample.jsonl",
        "<json>",
        "apiKeySource",
        "none",
    ),
}


def is_credential_name(name: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return re.search(r"(?:^|_)(?:key|keys|token|secret|secrets)(?:_|$)", normalized) is not None


def literal_string(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def short_fake_violation(path: str, function: str, name: str, value: object, line: int | None) -> str | None:
    """The one rule every scanned surface shares (ADR-0059): a non-empty credential-named string
    shorter than `MIN_SECRET_LENGTH`, outside the exact floor-testing exemptions."""
    if not isinstance(value, str) or not is_credential_name(name) or not value or len(value) >= MIN_SECRET_LENGTH:
        return None
    if (path, function, name, value) in ALLOWED_SHORT_SECRET_USES:
        return None
    at = f"{path}:{line}" if line is not None else path
    return f"{at}: short fake {name} ({len(value)} chars)"


class ShortSecretVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.function = "<module>"
        self.violations: list[str] = []

    def check(self, name: str, node: ast.expr) -> None:
        value = literal_string(node)
        violation = short_fake_violation(self.path, self.function, name, value, node.lineno)
        if violation is not None:
            self.violations.append(violation)

    def check_values(self, name: str, node: ast.expr) -> None:
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self.check_values(name, item)
        else:
            self.check(name, node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_function(node)

    def visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = previous

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg is not None:
                self.check_values(keyword.arg, keyword.value)
        callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for index in POSITIONAL_SECRET_PARAMETERS.get(callee, ()):
            if index < len(node.args):
                self.check_values("secrets", node.args[index])
        if getattr(node.func, "attr", None) == "setenv" and len(node.args) >= 2:
            name = literal_string(node.args[0])
            if name is not None:
                self.check(name, node.args[1])
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            name = literal_string(key) if key is not None else None
            if name is not None:
                self.check(name, value)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.check(target.id, node.value)
            elif isinstance(target, ast.Subscript):
                name = literal_string(target.slice)
                if name is not None:
                    self.check(name, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            self.check(node.target.id, node.value)
        elif isinstance(node.target, ast.Subscript) and node.value is not None:
            name = literal_string(node.target.slice)
            if name is not None:
                self.check(name, node.value)
        self.generic_visit(node)


def scan(source: str, path: str) -> list[str]:
    visitor = ShortSecretVisitor(path)
    visitor.visit(ast.parse(source))
    return visitor.violations


def scan_json(source: str, path: str) -> list[str]:
    """Credential-named keys with short string values in JSON fixtures; `*.jsonl` one document per
    line. A malformed document is the fixture tests' failure to report, not this guard's."""
    violations: list[str] = []

    def walk(node: object, line: int | None) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                violation = short_fake_violation(path, "<json>", str(key), value, line)
                if violation is not None:
                    violations.append(violation)
                walk(value, line)
        elif isinstance(node, list):
            for item in node:
                walk(item, line)

    if path.endswith(".jsonl"):
        for number, text in enumerate(source.splitlines(), start=1):
            try:
                walk(json.loads(text), number)
            except ValueError:
                continue
    else:
        try:
            walk(json.loads(source), None)
        except ValueError:
            pass
    return violations


def scanned_test_files() -> list[Path]:
    tests = ROOT / "tests"
    return sorted({*tests.rglob("test_*.py"), *tests.rglob("conftest.py")})


def scanned_fixture_files() -> list[Path]:
    tests = ROOT / "tests"
    return sorted([*tests.rglob("*.json"), *tests.rglob("*.jsonl")])


def test_short_fake_credentials_are_detected() -> None:
    source = (
        'configure(api_key="abc"); env = {"SERVICE_TOKEN": "x"}; '
        'monkeypatch.setenv("CLIENT_SECRET", "z"); Redactor(["tiny"])'
    )
    assert len(scan(source, "tests/example.py")) == 4


def test_short_fake_credentials_in_json_fixtures_are_detected() -> None:
    source = '{"api_key": "abc", "nested": {"CLIENT_SECRET": "z"}, "usage": {"input_tokens": 3}}'
    assert len(scan_json(source, "tests/example.json")) == 2
    assert len(scan_json('{"CLIENT_SECRET": "z"}\n', "tests/example.jsonl")) == 1
    assert scan_json('{"api_key": "marker-api-key"}', "tests/example.json") == []
    assert scan_json("not json", "tests/example.json") == []
    assert scan_json("not json", "tests/example.jsonl") == []


def test_all_test_files_use_redactable_fake_credentials() -> None:
    violations = [
        violation
        for path in scanned_test_files()
        for violation in scan(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    ] + [
        violation
        for path in scanned_fixture_files()
        for violation in scan_json(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    ]
    assert not violations, "\n".join(violations)
