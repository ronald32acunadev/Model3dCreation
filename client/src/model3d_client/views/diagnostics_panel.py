"""Shown in place of the viewer when the engine cannot run: what is missing and what to do."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from model3d_client.services.diagnostics import Check, CheckId


class DiagnosticsPanel(QWidget):
    restart_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.restart_button = QPushButton()
        self.restart_button.clicked.connect(self.restart_requested)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addWidget(self.restart_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self._checks: list[Check] = []
        self.retranslate()

    def set_checks(self, checks: list[Check]) -> None:
        self._checks = checks
        self._render()

    def text(self) -> str:
        return self.title.text() + "\n" + self.body.text()

    def retranslate(self) -> None:
        self.title.setText(self.tr("The engine cannot run"))
        self.restart_button.setText(self.tr("Restart engine"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _render(self) -> None:
        names = {
            CheckId.gpu: self.tr("NVIDIA GPU"),
            CheckId.engine_env: self.tr("Engine environment"),
            CheckId.hunyuan: self.tr("Hunyuan3D 2.1"),
            CheckId.weights: self.tr("Model weights"),
            CheckId.engine: self.tr("Engine"),
        }
        problems = {
            CheckId.gpu: self.tr("No NVIDIA GPU was found, or its driver does not answer (nvidia-smi)."),
            CheckId.engine_env: self.tr("The engine environment is missing. Set it up as described in docs/setup.md."),
            CheckId.hunyuan: self.tr("The Hunyuan3D 2.1 clone is missing from vendor/. See docs/setup.md."),
            CheckId.weights: self.tr("The model weights are missing from data/hf. See docs/setup.md."),
            CheckId.engine: self.tr("The engine is not running."),
        }
        lines = []
        for check in self._checks:
            mark = "✓" if check.ok else "✗"
            line = f"{mark} {names[check.id]}"
            if not check.ok:
                line += f": {problems[check.id]}"
            if check.detail:
                line += f"\n    {check.detail}"
            lines.append(line)
        self.body.setText("\n".join(lines))
