"""Failures that name a config problem without including a secret."""


class ConfigParseError(Exception):
    """The config file is not valid for its format. The installer leaves it untouched."""


class ConfigShapeError(Exception):
    """The file parsed, but not into a shape this installer knows how to edit."""


class InstallError(Exception):
    """A problem with the install request itself, before any file is written."""


class ConfigChangedError(Exception):
    """The config changed after it was read. Nothing was written over it."""
