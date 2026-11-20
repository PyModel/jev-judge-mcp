"""Trusted unittest collector and its isolated, scalar-only agent worker."""

import builtins
import ctypes
import importlib
import importlib.abc
import importlib.util
import json
import os
import platform
import select
import signal
import subprocess
import sys
import time
import unittest
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any, cast

CALL_TIMEOUT_S = 2.0
MAX_FRAME = 256 * 1024
ISOLATED_GRADER = "JEV_CONFINED_GRADER"


class AgentError(Exception):
    """An unsupported agent exception, failed worker, or invalid reply."""


def protect_parent() -> None:
    if sys.platform == "linux":
        getattr(ctypes.CDLL(None), "prctl", lambda *_: None)(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE


def _confine_worker(tree: Path, *, read_only_inputs: bool) -> None:
    """Keep confined workers out of harness files, including the writable grade output mount."""
    if not os.environ.get(ISOLATED_GRADER):
        return
    if sys.platform != "linux" or platform.machine() not in {"aarch64", "x86_64"}:
        raise OSError("the confined grader requires Linux Landlock")
    libc = ctypes.CDLL(None, use_errno=True)

    def checked(result: int) -> int:
        if result < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        return result

    # Linux Landlock syscall numbers on the supported architectures. ABI 3 mediates truncate.
    create, add, restrict = 444, 445, 446
    if libc.syscall(create, None, 0, 1) < 3:
        raise OSError("the confined grader requires Landlock ABI 3")
    read = 0b1101  # execute, read file, read directory
    write = ((1 << 15) - 1) ^ read
    rules = ctypes.c_uint64(write | (read if read_only_inputs else 0))
    ruleset = checked(libc.syscall(create, ctypes.byref(rules), ctypes.sizeof(rules), 0))

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    def allow(path: Path, rights: int) -> None:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = PathRule(rights, fd)
            checked(libc.syscall(add, ruleset, 1, ctypes.byref(rule), 0))
        finally:
            os.close(fd)

    try:
        allow(tree, rules.value)
        if read_only_inputs:
            for directory in (
                Path(sys.base_prefix),
                Path("/usr/lib"),
                Path("/usr/lib64"),
                Path("/lib"),
                Path("/lib64"),
            ):
                if directory.is_dir():
                    allow(directory, read)
            cache = Path("/etc/ld.so.cache")
            if cache.is_file():
                allow(cache, 1 << 2)
        checked(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        checked(libc.syscall(restrict, ruleset, 0))
    finally:
        os.close(ruleset)


def _encode(value: object) -> list[Any]:
    kind = type(value)
    if value is None:
        return ["none", None]
    if kind is bool or kind is int or kind is str:
        return [kind.__name__, value]
    if kind is float or kind is Decimal:
        return [kind.__name__, str(value)]
    if kind is list or kind is tuple:
        return [kind.__name__, [_encode(item) for item in cast(Sequence[object], value)]]
    if kind is dict:
        items = cast(dict[object, object], value)
        if all(type(key) is str for key in items):
            return ["dict", {key: _encode(item) for key, item in items.items()}]
    if type(value) is date:
        return ["date", value.isoformat()]
    if type(value) is datetime:
        return ["datetime", [value.isoformat(), value.fold]]
    raise AgentError("unsupported value type")


def _decode(value: object) -> Any:
    if not isinstance(value, list):
        raise AgentError("invalid tagged value")
    fields = cast(list[object], value)
    if len(fields) != 2:
        raise AgentError("invalid tagged value")
    tag, item = fields
    match tag, item:
        case "none", None:
            return None
        case ("bool", bool()) | ("str", str()):
            return item
        case "int", int() if type(item) is int:
            return item
        case "float", str() as raw:
            return float(raw)
        case "Decimal", str() as raw:
            return Decimal(raw)
        case "list", list():
            return [_decode(child) for child in cast(list[object], item)]
        case "tuple", list():
            return tuple(_decode(child) for child in cast(list[object], item))
        case "dict", dict():
            return {key: _decode(child) for key, child in cast(dict[str, object], item).items()}
        case "date", str() as raw:
            return date.fromisoformat(raw)
        case "datetime", [str() as raw, int() as fold] if type(fold) is int and fold in (0, 1):
            return datetime.fromisoformat(raw).replace(fold=fold)
        case _:
            raise AgentError("invalid tagged value")


# Capture exact builtin identities before importing agent code. Exception names are not identities.
_EXCEPTIONS = {
    name: cls for name, cls in vars(builtins).items() if isinstance(cls, type) and issubclass(cls, BaseException)
}
_EXCEPTION_IDS = {id(cls): cls.__name__ for cls in _EXCEPTIONS.values()}


def _frame(value: object) -> bytes:
    frame = (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(frame) > MAX_FRAME:
        raise AgentError("agent frame exceeds the limit")
    return frame


def _worker(module_name: str, output_fd: int) -> None:
    tree = Path.cwd()
    _confine_worker(tree, read_only_inputs=True)
    sys.path.append(str(tree))  # stdlib takes precedence over files in the agent tree
    requests = sys.stdin.buffer
    module: ModuleType | None = None
    with os.fdopen(output_fd, "wb") as replies:
        while request := requests.readline(MAX_FRAME + 1):
            try:
                match json.loads(request):
                    case ["import"]:
                        module = importlib.import_module(module_name)
                        reply: object = ["ok"]
                    case ["get", str() as name] if module is not None:
                        attr: object = getattr(module, name)
                        reply = ["function"] if callable(attr) else ["value", _encode(attr)]
                    case ["call", str() as name, args, kwargs] if module is not None:
                        attr: object = getattr(module, name)
                        if not callable(attr):
                            raise AgentError("agent attribute is not callable")
                        result: object = attr(*_decode(args), **_decode(kwargs))
                        try:
                            reply = ["value", _encode(result)]
                        except (AgentError, RecursionError):
                            reply = ["invalid"]
                    case _:
                        reply = ["invalid"]
            except AgentError:
                reply = ["invalid"]
            except BaseException as exc:
                reply = ["raise", _EXCEPTION_IDS.get(id(type(exc))), type(exc).__name__, str(exc)]
            replies.write(_frame(reply))
            replies.flush()


def _ready(fd: int, deadline: float, *, write: bool = False) -> None:
    timeout = deadline - time.monotonic()
    if timeout <= 0 or not any(select.select([] if write else [fd], [fd] if write else [], [], timeout)[:2]):
        raise AgentError("agent call timed out")


def _exchange(request_fd: int, reply_fd: int, request: object) -> Any:
    deadline = time.monotonic() + CALL_TIMEOUT_S
    pending = memoryview(_frame(request))
    while pending:
        _ready(request_fd, deadline, write=True)
        pending = pending[os.write(request_fd, pending[:4096]) :]
    frame = bytearray()
    while len(frame) < MAX_FRAME:
        _ready(reply_fd, deadline)
        chunk = os.read(reply_fd, min(4096, MAX_FRAME - len(frame)))
        if not chunk:
            raise AgentError("agent worker exited without a result")
        frame.extend(chunk)
        if b"\n" in chunk:
            if frame.count(b"\n") != 1 or not frame.endswith(b"\n"):
                raise AgentError("agent sent multiple or partial replies")
            return json.loads(frame)
    raise AgentError("agent frame exceeds the limit")


def _raised(builtin_name: str | None, name: str, message: str) -> BaseException:
    cls = _EXCEPTIONS.get(builtin_name or "")
    if cls is None or builtin_name != name:
        return AgentError(f"{name}: {message}")
    if cls is UnicodeDecodeError:
        return UnicodeDecodeError("utf-8", b"", 0, 0, message)
    if cls is UnicodeEncodeError:
        return UnicodeEncodeError("utf-8", "", 0, 0, message)
    if cls is UnicodeTranslateError:
        return UnicodeTranslateError("", 0, 0, message)
    if cls is ExceptionGroup:
        return ExceptionGroup(message, [AgentError(message)])
    if cls is BaseExceptionGroup:
        return BaseExceptionGroup(message, [BaseException(message)])
    return cls(message)


class _Worker:
    def __init__(self, module: str) -> None:
        self.reply_fd, output = os.pipe()
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", __file__, "worker", module, str(output)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(output,),
                start_new_session=True,
            )
        except BaseException:
            os.close(self.reply_fd)
            raise
        finally:
            os.close(output)
        assert self.process.stdin is not None
        self.request_fd = self.process.stdin.fileno()
        os.set_blocking(self.request_fd, False)
        self.closed = False
        self.invalid = 0

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        assert self.process.stdin is not None
        self.process.stdin.close()
        os.close(self.reply_fd)
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=CALL_TIMEOUT_S)

    def request(self, request: list[Any]) -> Any:
        if self.closed:
            self.invalid += 1
            raise AgentError("agent worker is unavailable")
        try:
            reply = _exchange(self.request_fd, self.reply_fd, request)
            match reply:
                case ["ok"] if request[0] == "import":
                    return None
                case ["function"] if request[0] == "get":
                    return self._function(request[1])
                case ["value", value] if request[0] in ("get", "call"):
                    return _decode(value)
                case ["raise", (str() | None) as builtin_name, str() as name, str() as message]:
                    error = _raised(builtin_name, name, message)
                case _:
                    raise AgentError("invalid agent reply")
        except Exception as exc:
            self.invalid += 1
            self.close()
            raise AgentError("agent worker failed: " + str(exc)) from exc
        raise error

    def _function(self, name: str) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            try:
                request = ["call", name, _encode(args), _encode(kwargs)]
            except (AgentError, RecursionError):
                self.invalid += 1
                raise
            return self.request(request)

        return invoke


class _ProxyImports(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, modules: Sequence[str], lifetime: ExitStack) -> None:
        self.modules = frozenset(modules)
        self.lifetime = lifetime
        self.workers: dict[str, _Worker] = {}

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if fullname in self.modules:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        module_name = module.__name__
        worker = self.workers.get(module_name)
        if worker is None:
            worker = self.workers[module_name] = _Worker(module_name)
            self.lifetime.callback(worker.close)
        worker.request(["import"])

        def get(name: str) -> Any:
            if name.startswith("__"):
                raise AttributeError(name)
            return worker.request(["get", name])

        module.__getattr__ = get


def _collect(directory: str, top: str, proxies: _ProxyImports | None = None) -> None:
    out: dict[str, str] = {}

    def invalid() -> int:
        return sum(worker.invalid for worker in proxies.workers.values()) if proxies is not None else 0

    class Result(unittest.TestResult):
        def startTest(self, test: unittest.TestCase) -> None:
            self.invalid_at_start = invalid()

        def addSuccess(self, test: unittest.TestCase) -> None:
            out.setdefault(test.id(), "pass" if invalid() == self.invalid_at_start else "error")

        def addFailure(self, test: unittest.TestCase, err: Any) -> None:
            out[test.id()] = "fail"

        def addError(self, test: unittest.TestCase, err: Any) -> None:
            out[test.id()] = "error"

        def addSubTest(self, test: unittest.TestCase, subtest: unittest.TestCase, err: Any) -> None:
            if err is not None:
                out[test.id()] = "fail"

        def addSkip(self, test: unittest.TestCase, reason: str) -> None:
            out[test.id()] = "skip"

    unittest.defaultTestLoader.discover(directory, top_level_dir=top).run(Result())
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["worker", module, fd]:
            _worker(module, int(fd))
        case ["collect", directory, *modules]:
            protect_parent()
            with ExitStack() as lifetime:
                proxies = _ProxyImports(modules, lifetime)
                sys.meta_path.insert(0, proxies)
                _collect(directory, str(Path(directory).parent), proxies)
        case ["added", kind, *arguments]:
            _confine_worker(Path.cwd(), read_only_inputs=False)
            if kind == "pytest":
                import runpy

                sys.argv[1:] = arguments
                runpy.run_module("pytest", run_name="__main__", alter_sys=True)
            else:
                sys.path.append(str(Path.cwd()))
                _collect("tests", ".")
        case _:
            raise SystemExit("invalid grading runner arguments")
