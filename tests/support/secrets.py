"""Every secret Settings field, derived from the schema (ADR-0017).

The redaction tests' oracle, independent of `Settings.secret_values()` on purpose: a credential
field the production derivation skips still gets a marker here, so these tests fail instead of
mirroring the gap.
"""

import re
from typing import get_args

from pydantic import SecretStr
from pydantic.aliases import AliasChoices
from pydantic.fields import FieldInfo

from jev_judge_mcp.settings import Settings

_URL_SHAPED = re.compile(r"_BASE_URL$")


def is_secret_field(field: FieldInfo) -> bool:
    """The field is (optionally) a `SecretStr`."""
    annotation = field.annotation
    return annotation is SecretStr or SecretStr in get_args(annotation)


def _env_name(name: str, field: FieldInfo) -> str:
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias
    if isinstance(alias, AliasChoices):
        for choice in alias.choices:
            if isinstance(choice, str):
                return choice
    return name.upper()


def secret_env() -> dict[str, str]:
    """Env var → marker value for every `SecretStr` field; URL-shaped for base URLs (userinfo)."""
    env: dict[str, str] = {}
    for name, field in Settings.model_fields.items():
        if not is_secret_field(field):
            continue
        var = _env_name(name, field)
        marker = f"marker-{var.lower().replace('_', '-')}"
        env[var] = f"https://user:{marker}@example.invalid" if _URL_SHAPED.search(var) else marker
    return env
