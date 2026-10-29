"""Engine state, run mode, and GPU use, with the settings button."""

from model3d_contracts.health import Health
from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from model3d_client.services.supervisor import EngineState


class TopBar(QWidget):
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.state_label = QLabel()
        self.mode_label = QLabel()
        self.gpu_label = QLabel()
        self.settings_button = QPushButton()
        self.settings_button.clicked.connect(self.settings_requested)
        layout = QHBoxLayout(self)
        layout.addWidget(self.state_label)
        layout.addWidget(self.mode_label)
        layout.addWidget(self.gpu_label)
        layout.addStretch(1)
        layout.addWidget(self.settings_button)
        self._state = EngineState.stopped
        self._health: Health | None = None
        self.retranslate()

    def set_state(self, state: EngineState) -> None:
        self._state = state
        self._render()

    def set_health(self, health: Health | None) -> None:
        self._health = health
        self._render()

    def retranslate(self) -> None:
        self.settings_button.setText(self.tr("Settings"))
        self._render()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _render(self) -> None:
        texts = {
            EngineState.stopped: self.tr("● Engine stopped"),
            EngineState.starting: self.tr("● Starting engine…"),
            EngineState.loading: self.tr("● Loading models…"),
            EngineState.ready: self.tr("● Engine ready"),
            EngineState.error: self.tr("● Engine error"),
            EngineState.crashed: self.tr("● Engine stopped unexpectedly"),
        }
        colors = {EngineState.ready: "#2e9d4f", EngineState.error: "#c0392b", EngineState.crashed: "#c0392b"}
        self.state_label.setText(texts[self._state])
        self.state_label.setStyleSheet(f"color: {colors.get(self._state, '#d4a017')}; font-weight: bold;")
        health = self._health
        if health is None:
            self.mode_label.setText("")
            self.gpu_label.setText("")
            return
        self.mode_label.setText(self.tr("Mode: %1").replace("%1", health.mode.name))
        gpus = []
        for index, gpu in enumerate(health.gpus):
            if gpu.stage is None:
                continue
            text = self.tr("GPU %1: %2").replace("%1", str(index)).replace("%2", gpu.stage)
            if gpu.memory_free_mib is not None:
                text += " · " + self.tr("%1 MiB free").replace("%1", str(gpu.memory_free_mib))
            gpus.append(text)
        self.gpu_label.setText("   ".join(gpus))
