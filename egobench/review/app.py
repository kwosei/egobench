from __future__ import annotations

import json
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, ListItem, ListView, Static, TextArea

from egobench.config import EgoBenchConfig
from egobench.db import DB
from egobench.paths import WorkspacePaths
from egobench.pipeline.phase8_lock import run as lock_benchmark
from egobench.pipeline.schema import Benchmark
from egobench.review.widgets import ImportanceSlider


class ReviewApp(App):
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #tasks { width: 34; }
    #editor { width: 1fr; padding: 1; }
    #prompt { height: 1fr; }
    #checklist { height: 1fr; }
    Input { margin: 1 0; }
    Button { margin-right: 1; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "save", "Save"),
        ("left", "importance_down", "Lower"),
        ("right", "importance_up", "Raise"),
    ]

    def __init__(self, paths: WorkspacePaths, db: DB, cfg: EgoBenchConfig):
        super().__init__()
        self.paths = paths
        self.db = db
        self.cfg = cfg
        self.benchmark = Benchmark.model_validate_json(paths.benchmark.read_text(encoding="utf-8"))
        self.current_index = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield ListView(*[ListItem(Static(f"{task.id} · {task.category}")) for task in self.benchmark.tasks], id="tasks")
            with Vertical(id="editor"):
                yield TextArea("", id="prompt", read_only=True)
                yield ImportanceSlider(id="importance")
                yield TextArea(id="checklist")
                with Horizontal():
                    yield Button("Save", id="save", variant="primary")
                    yield Button("Quit", id="quit")
                yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(ListView).index = 0
        self._load_task(0)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._load_task(event.list_view.index or 0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        if event.button.id == "quit":
            self.exit()

    def action_importance_down(self) -> None:
        self.query_one("#importance", ImportanceSlider).step(-0.05)

    def action_importance_up(self) -> None:
        self.query_one("#importance", ImportanceSlider).step(0.05)

    def action_save(self) -> None:
        task = self.benchmark.tasks[self.current_index]
        checklist_text = self.query_one("#checklist", TextArea).text
        importance = self.query_one("#importance", ImportanceSlider).value
        checklist = [line.strip("- ").strip() for line in checklist_text.splitlines() if line.strip("- ").strip()]
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE task_candidates
                SET checklist_json = ?, importance = ?, updated_at = CURRENT_TIMESTAMP
                WHERE conversation_id = ?
                """,
                (json.dumps(checklist, sort_keys=True), importance, task.conversation_id),
            )
        result = lock_benchmark(self.db, self.cfg, self.paths)
        self.query_one("#status", Static).update(f"Saved benchmark v{result['version']} ({result['benchmark_hash'][:12]})")

    def _load_task(self, index: int) -> None:
        self.current_index = index
        task = self.benchmark.tasks[index]
        prompt = "\n".join(f"{turn.role.upper()}: {turn.text}" for turn in task.turns)
        self.query_one("#prompt", TextArea).load_text(prompt)
        self.query_one("#importance", ImportanceSlider).set_value(task.importance)
        self.query_one("#checklist", TextArea).text = "\n".join(f"- {item}" for item in task.checklist)


def run_review(paths: WorkspacePaths, db: DB, cfg: EgoBenchConfig) -> None:
    if not paths.benchmark.exists():
        raise RuntimeError("No benchmark.json found. Run `egobench build` first.")
    ReviewApp(paths, db, cfg).run()
