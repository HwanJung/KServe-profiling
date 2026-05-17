from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def run_subprocess(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\ncommand timed out after {timeout}s",
        )


def check_call(cmd: list[str], timeout: int) -> None:
    proc = run_subprocess(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )


def kubectl_json(cmd: list[str], timeout: int) -> dict[str, Any]:
    proc = run_subprocess(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return json.loads(proc.stdout)
