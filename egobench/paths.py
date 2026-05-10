from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


WORKSPACE_DIRNAME = "egobench-workspace"


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path

    @property
    def config(self) -> Path:
        return self.root / "egobench.toml"

    @property
    def db(self) -> Path:
        return self.root / "egobench.db"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def benchmark(self) -> Path:
        return self.root / "benchmark.json"

    @property
    def report_html(self) -> Path:
        return self.root / "report.html"

    @property
    def report_md(self) -> Path:
        return self.root / "report.md"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)


def workspace_from_cwd(cwd: Path | None = None) -> WorkspacePaths:
    base = cwd or Path.cwd()
    return WorkspacePaths(base / WORKSPACE_DIRNAME)

