"""Start a command on macOS without forking the server (ADR-0088).

`subprocess.Popen` forks on macOS with the GIL held: about 1.3 ms of stalled event loop per command,
paid by every concurrent call. This binds libc's `posix_spawn` through ctypes, which releases the GIL
for the call, with `POSIX_SPAWN_CLOEXEC_DEFAULT`: the child receives only the descriptors the file
actions name, the guarantee `Popen(close_fds=True)` gives. `os.posix_spawn` cannot request that flag,
so it hands the child any descriptor another thread created in the instant before the spawn.
"""

import ctypes
import errno
import os
import shutil
import signal
from collections.abc import Callable, Iterable, Mapping, Sequence

_SETSIGDEF = 0x0004
_SETSIGMASK = 0x0008
_SETSID = 0x0400
_CLOEXEC_DEFAULT = 0x4000
"""`<sys/spawn.h>` flags: restore default signal handlers, set the signal mask, start a new session,
and close every descriptor the file actions do not name."""

_RESTORED_SIGNALS = (signal.SIGPIPE, signal.SIGXFSZ)
"""The dispositions Python ignores for itself and `Popen(restore_signals=True)` resets in the child."""

_libc = ctypes.CDLL(None, use_errno=True)
_Handle = ctypes.POINTER(ctypes.c_void_p)  # posix_spawnattr_t and posix_spawn_file_actions_t are void *
_SigSet = ctypes.c_uint32  # sigset_t is __uint32_t on Darwin
_Strings = ctypes.POINTER(ctypes.c_char_p)


def _bind(name: str, *argtypes: type) -> Callable[..., int]:
    function = getattr(_libc, name)
    function.argtypes = argtypes
    function.restype = ctypes.c_int
    return function


_attr_init = _bind("posix_spawnattr_init", _Handle)
_attr_destroy = _bind("posix_spawnattr_destroy", _Handle)
_attr_setflags = _bind("posix_spawnattr_setflags", _Handle, ctypes.c_short)
_attr_setsigdefault = _bind("posix_spawnattr_setsigdefault", _Handle, ctypes.POINTER(_SigSet))
_attr_setsigmask = _bind("posix_spawnattr_setsigmask", _Handle, ctypes.POINTER(_SigSet))
_actions_init = _bind("posix_spawn_file_actions_init", _Handle)
_actions_destroy = _bind("posix_spawn_file_actions_destroy", _Handle)
_actions_addopen = _bind(
    "posix_spawn_file_actions_addopen", _Handle, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint16
)
_actions_adddup2 = _bind("posix_spawn_file_actions_adddup2", _Handle, ctypes.c_int, ctypes.c_int)
_sigemptyset = _bind("sigemptyset", ctypes.POINTER(_SigSet))
_sigaddset = _bind("sigaddset", ctypes.POINTER(_SigSet), ctypes.c_int)
_posix_spawn = _bind("posix_spawn", ctypes.POINTER(ctypes.c_int), ctypes.c_char_p, _Handle, _Handle, _Strings, _Strings)


def spawn(argv: Sequence[str], env: Mapping[str, str], stdout: int, stderr: int) -> int:
    """Start argv in a new session with stdin at /dev/null, stdout and stderr on the given
    descriptors, and no other descriptor; return its pid. Raises what `Popen` raises: `OSError` for
    a program that cannot run and `ValueError` for an argument or variable it cannot pass."""
    program = _executable(argv[0], env)
    args = _strings(os.fsencode(arg) for arg in argv)
    variables = _strings(_variable(name, value) for name, value in env.items())
    attr, actions = ctypes.c_void_p(), ctypes.c_void_p()
    _check(_attr_init(ctypes.byref(attr)))
    try:
        _check(_actions_init(ctypes.byref(actions)))
        try:
            _configure(attr, actions, stdout, stderr)
            pid = ctypes.c_int()
            code = _posix_spawn(ctypes.byref(pid), program, ctypes.byref(actions), ctypes.byref(attr), args, variables)
            _check(code, argv[0])
            return pid.value
        finally:
            _actions_destroy(ctypes.byref(actions))
    finally:
        _attr_destroy(ctypes.byref(attr))


def _configure(attr: ctypes.c_void_p, actions: ctypes.c_void_p, stdout: int, stderr: int) -> None:
    _check(_attr_setflags(ctypes.byref(attr), _SETSID | _SETSIGDEF | _SETSIGMASK | _CLOEXEC_DEFAULT))
    restored, empty = _SigSet(), _SigSet()
    _check(_sigemptyset(ctypes.byref(restored)))
    for signum in _RESTORED_SIGNALS:
        _check(_sigaddset(ctypes.byref(restored), signum))
    _check(_sigemptyset(ctypes.byref(empty)))
    _check(_attr_setsigdefault(ctypes.byref(attr), ctypes.byref(restored)))
    _check(_attr_setsigmask(ctypes.byref(attr), ctypes.byref(empty)))
    _check(_actions_addopen(ctypes.byref(actions), 0, os.fsencode(os.devnull), os.O_RDONLY, 0))
    _check(_actions_adddup2(ctypes.byref(actions), stdout, 1))
    _check(_actions_adddup2(ctypes.byref(actions), stderr, 2))


def _executable(name: str, env: Mapping[str, str]) -> bytes:
    """Resolve a bare name against the child's PATH, as `Popen` does; a name with a slash is a path."""
    if os.path.dirname(name):
        return _nul_free(os.fsencode(name))
    found = shutil.which(name, path=os.pathsep.join(os.get_exec_path(env)))
    if found is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), name)
    return _nul_free(os.fsencode(found))


def _variable(name: str, value: str) -> bytes:
    key = os.fsencode(name)
    if b"=" in key:  # Popen's rule, so both platforms refuse the same names
        raise ValueError("illegal environment variable name")
    return _nul_free(key + b"=" + os.fsencode(value))


def _strings(items: Iterable[bytes]) -> "ctypes.Array[ctypes.c_char_p]":
    values = [_nul_free(item) for item in items]
    return (ctypes.c_char_p * (len(values) + 1))(*values, None)


def _nul_free(value: bytes) -> bytes:
    if b"\0" in value:
        raise ValueError("embedded null byte")
    return value


def _check(code: int, filename: str | None = None) -> None:
    """posix_spawn and its helpers return an error number; sigemptyset and sigaddset return -1."""
    if code == -1:
        code = ctypes.get_errno()
    if code:
        raise OSError(code, os.strerror(code), *(() if filename is None else (filename,)))
