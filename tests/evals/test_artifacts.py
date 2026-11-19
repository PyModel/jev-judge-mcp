"""Result files must qualify for bounded reading before restart analysis accepts them."""

import os
import stat
from pathlib import Path

import pytest

from evals.artifacts import read_records


@pytest.mark.parametrize("issue", ["nonregular", "oversized"])
def test_unsuitable_record_is_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, issue: str) -> None:
    path = tmp_path / "run" / "result.json"
    path.parent.mkdir()
    path.write_text('{"run_id":"run"}')
    real_stat = os.fstat

    def unsuitable(fd: int) -> os.stat_result:
        fields = list(real_stat(fd))
        if issue == "nonregular":
            fields[0] = stat.S_IFIFO | 0o600
        else:
            fields[6] = 1 << 30
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", unsuitable)
    assert read_records([path], required=("run_id",), valid=lambda _: True) == ({}, [path])


def test_artifact_read_checks_the_actual_bytes_as_well_as_the_initial_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.artifacts import read_artifact

    path = tmp_path / "record"
    path.write_bytes(b"abcd")
    assert read_artifact(path, max_bytes=4) == b"abcd"
    initial = path.stat()
    path.write_bytes(b"abcde")
    monkeypatch.setattr(os, "fstat", lambda _fd: initial)
    with pytest.raises(OSError, match="exceeds 4 bytes"):
        read_artifact(path, max_bytes=4)
