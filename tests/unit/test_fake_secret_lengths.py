"""Catch short fake credentials before they collide with unrelated text in tests.

The static rule checks literal credential-named assignments/kwargs, string-keyed env/config
mappings, `setenv` calls, and positional secrets for the known redaction APIs. Empty strings mean
"unset"; only the exact floor-testing fixtures below are exempt.
"""

import ast
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
}


def is_credential_name(name: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    return re.search(r"(?:^|_)(?:key|keys|token|secret|secrets)(?:_|$)", normalized) is not None


def literal_string(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class ShortSecretVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.function = "<module>"
        self.violations: list[str] = []

    def check(self, name: str, node: ast.expr) -> None:
        value = literal_string(node)
        if not is_credential_name(name) or not value or len(value) >= MIN_SECRET_LENGTH:
            return
        if (self.path, self.function, name, value) not in ALLOWED_SHORT_SECRET_USES:
            self.violations.append(f"{self.path}:{node.lineno}: short fake {name} ({len(value)} chars)")

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


def test_short_fake_credentials_are_detected() -> None:
    source = (
        'configure(api_key="abc"); env = {"SERVICE_TOKEN": "x"}; '
        'monkeypatch.setenv("CLIENT_SECRET", "z"); Redactor(["tiny"])'
    )
    assert len(scan(source, "tests/example.py")) == 4


def test_all_test_files_use_redactable_fake_credentials() -> None:
    test_files = sorted((ROOT / "tests").rglob("test_*.py"))
    violations = [
        violation
        for path in test_files
        for violation in scan(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    ]
    assert not violations, "\n".join(violations)
