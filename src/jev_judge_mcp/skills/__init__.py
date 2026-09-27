"""Packaged agent skills a uvx or PyPI install can read (divergence `packaged-jev-skill`).

`jev` is the owner's skill, packaged from https://github.com/PyModel/jev-skill at a596996.
`jev-mcp` routes this server's tools. Files are read only through `importlib.resources`.
"""

from collections.abc import Callable, Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable

from mcp.server.mcpserver.prompts import Prompt
from mcp.server.mcpserver.resources import FunctionResource
from mcp.server.mcpserver.server import MCPServer

SCHEME = "jev-skill"
_FIXED = (
    "jev-mcp/SKILL.md",
    "jev/LICENSE",
    "jev/SKILL.md",
    "jev/api-reference.md",
    "jev/patterns.md",
)


def packaged_rels() -> tuple[str, ...]:
    """The allowlist that may ship and be served. Anything else under this package is a bug."""
    prior: list[str] = []
    art = files("jev_judge_mcp.skills").joinpath("jev").joinpath("prior-art")
    if art.is_dir():
        for child in sorted(art.iterdir(), key=lambda item: item.name):
            if child.is_file() and child.name.endswith(".md"):
                prior.append(f"jev/prior-art/{child.name}")
    return tuple(sorted((*_FIXED, *prior)))


def read_text(rel: str) -> str:
    """UTF-8 text of one packaged skill file. Missing files raise ``FileNotFoundError``."""
    node: Traversable = files("jev_judge_mcp.skills")
    for part in rel.split("/"):
        node = node.joinpath(part)
    return node.read_text(encoding="utf-8")


def uri_for(rel: str) -> str:
    return f"{SCHEME}://{rel}"


def iter_skill_files() -> Iterator[tuple[str, str]]:
    """Relative path and URI for every allowlisted skill file."""
    for rel in packaged_rels():
        yield rel, uri_for(rel)


def _reader(rel: str) -> Callable[[], str]:
    def read_skill_file() -> str:
        return read_text(rel)

    read_skill_file.__name__ = "read_skill_file"
    return read_skill_file


def _mime(rel: str) -> str:
    return "text/markdown" if rel.endswith(".md") else "text/plain"


def _description(rel: str) -> str:
    if rel == "jev/SKILL.md":
        return "Building an app on the Jev API. Not the guide for this server's tools."
    if rel == "jev-mcp/SKILL.md":
        return "Which of this server's tools fits a step, and what to do with action."
    if rel == "jev/LICENSE":
        return "Licence for the packaged jev skill."
    return rel


def jev_prompt() -> str:
    """The packaged `jev` skill."""
    return read_text("jev/SKILL.md")


def jev_mcp_prompt() -> str:
    """The `jev-mcp` skill. This is the guide for this server's tools."""
    return read_text("jev-mcp/SKILL.md")


def attach(server: MCPServer) -> None:
    """Register the allowlisted skill files as resources, and one prompt per skill."""
    for rel, _uri in iter_skill_files():
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
            description=_description("jev/SKILL.md"),
        )
    )
    server.add_prompt(
        Prompt.from_function(
            jev_mcp_prompt,
            name="jev-mcp",
            title="Routing this server's tools",
            description=_description("jev-mcp/SKILL.md"),
        )
    )
