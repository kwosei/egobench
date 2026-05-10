from __future__ import annotations

from textual.widgets import Static


class ImportanceSlider(Static, can_focus=True):
    def __init__(self, value: float = 0.0, *, id: str | None = None):
        super().__init__("", id=id)
        self.value = min(1.0, max(0.0, value))

    def on_mount(self) -> None:
        self.refresh_display()

    def set_value(self, value: float) -> None:
        self.value = min(1.0, max(0.0, value))
        self.refresh_display()

    def step(self, delta: float) -> None:
        self.set_value(self.value + delta)

    def refresh_display(self) -> None:
        filled = round(self.value * 20)
        bar = "#" * filled + "-" * (20 - filled)
        self.update(f"Importance [{bar}] {self.value:.2f}")

