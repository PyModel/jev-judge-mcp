"""Packaged agent skills a uvx or PyPI install can read (divergence `packaged-jev-skill`).

`jev` is the renamed upstream skill for building an app on the Jev API.
`jev-mcp` is this server's routing skill. Neither file is read from the git
checkout at runtime: `importlib.resources` is the only path, so a wheel that
drops the files fails closed instead of falling back to a source tree.
"""

from collections.abc import Callable, Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable

from mcp.server.mcpserver.prompts import Prompt
from mcp.server.mcpserver.resources import FunctionResource
from mcp.server.mcpserver.server import MCPServer

SCHEME = "jev-skill"
_SKIP = {"__init__.py", "__pycache__"}
ON_DEMAND = (
    "Jev is on demand only; call it when an independent judgment materially improves the decision; "
    "never route every judgment through it. "
    "High-value calls: before a done claim, jev_gate; before reading fetched or pasted external text, jev_screen; "
    "checking another agent's report or research claims, jev_verify. "
    "Skip it when the answer is already determined by a test, type-check, or the code itself; "
    "when the choice is trivial or cheap to reverse; "
    "when the question cannot be enumerated into bounded options; "
    "or when the same unchanged decision was already asked."
)

JEV_URI = "jev-skill://jev/SKILL.md"
JEV_MCP_URI = "jev-skill://jev-mcp/SKILL.md"
PROVENANCE_URI = "jev-skill://jev/PROVENANCE.md"


def iter_skill_files(root: Traversable | None = None, prefix: str = "") -> Iterator[tuple[str, Traversable]]:
    """Relative path and node for every file shipped under this package, sorted."""
    node = files("jev_judge_mcp.skills") if root is None else root
    children = sorted(node.iterdir(), key=lambda item: item.name)
    for child in children:
        if child.name in _SKIP or child.name.endswith(".pyc"):
            continue
        rel = child.name if not prefix else f"{prefix}/{child.name}"
        if child.is_dir():
            yield from iter_skill_files(child, rel)
        elif child.is_file() and rel.startswith(("jev/", "jev-mcp/")):
            yield rel, child


def read_text(rel: str) -> str:
    """UTF-8 text of one packaged skill file. Missing files raise ``FileNotFoundError``."""
    node: Traversable = files("jev_judge_mcp.skills")
    for part in rel.split("/"):
        node = node.joinpath(part)
    return node.read_text(encoding="utf-8")


def uri_for(rel: str) -> str:
    return f"{SCHEME}://{rel}"


def _reader(rel: str) -> Callable[[], str]:
    def read_skill_file() -> str:
        return read_text(rel)

    read_skill_file.__name__ = "read_skill_file"
    return read_skill_file


def _mime(rel: str) -> str:
    return "text/markdown" if rel.endswith(".md") else "text/plain"


def _description(rel: str) -> str:
    if rel == "jev/SKILL.md":
        return f"Building an app on the Jev API. Not the guide for this server's tools. {ON_DEMAND}"
    if rel == "jev-mcp/SKILL.md":
        return f"Which of this server's tools fits a step, and what to do with action. {ON_DEMAND}"
    if rel == "jev/PROVENANCE.md":
        return "Upstream commit, and contradictions with the live docs and this server."
    return rel


def jev_prompt() -> str:
    """Verbatim `jev` skill. Sibling files are resources under ``jev-skill://jev/``."""
    return read_text("jev/SKILL.md")


def jev_mcp_prompt() -> str:
    """Verbatim `jev-mcp` skill. This is the guide for this server's tools."""
    return read_text("jev-mcp/SKILL.md")


def attach(server: MCPServer) -> None:
    """Register every packaged skill file as a resource, and one prompt per skill."""
    for rel, _node in iter_skill_files():
        server.add_resource(
            FunctionResource.from_function(
                fn=_reader(rel),
                uri=uri_for(rel),
                name=rel,
                description=_description(rel),
                mime_type=_mime(rel),
            )
        )
    server.add_prompt(
        Prompt.from_function(
            jev_prompt,
            name="jev",
            title="Building with Jev",
            description=_description("jev/SKILL.md") + " Sibling files are resources under jev-skill://jev/.",
        )
    )
    server.add_prompt(
        Prompt.from_function(
            jev_mcp_prompt,
            name="jev-mcp",
            title="Routing this server's tools",
            description=_description("jev-mcp/SKILL.md") + " Not the Jev API skill.",
        )
    )
