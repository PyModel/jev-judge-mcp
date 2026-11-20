"""Environment names shared by the in-process and stdio test harnesses."""

from jev_judge_mcp.settings import Settings, _env_var_name  # pyright: ignore[reportPrivateUsage]

SETTINGS_ENV = frozenset(_env_var_name(name, field) for name, field in Settings.model_fields.items())
