from model3d_contracts.health import Health
from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

from model3d_client.services.supervisor import EngineState
from model3d_client.views.theme import PALETTE_COLORS


class TopBar(QWidget):
    settings_requested = Signal()
    section_requested = Signal(int)  # 0: 3D models, 1: Voice

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.state_label = QLabel()
        self.state_label.setObjectName("stateBadge")
        self.mode_label = QLabel()
        self.mode_label.setObjectName("metaBadge")
        self.gpu_label = QLabel()
        self.gpu_label.setObjectName("metaBadge")
        self.models_button = QPushButton()
        self.models_button.setObjectName("tabButton")
        self.voice_button = QPushButton()
        self.voice_button.setObjectName("tabButton")
        self.sections = QButtonGroup(self)
        for index, button in enumerate((self.models_button, self.voice_button)):
            button.setCheckable(True)
            self.sections.addButton(button, index)
        self.models_button.setChecked(True)
        self.sections.idClicked.connect(self.section_requested)
        self.settings_button = QPushButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.clicked.connect(self.settings_requested)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)
        layout.addWidget(self.models_button)
        layout.addWidget(self.voice_button)
        layout.addWidget(self.state_label)
        layout.addWidget(self.mode_label)
        layout.addWidget(self.gpu_label)
        layout.addStretch(1)
        layout.addWidget(self.settings_button)
        self._state = EngineState.stopped
        self._health: Health | None = None
        self.show_sections(False)
        self.retranslate()

    def show_sections(self, shown: bool) -> None:
        self.models_button.setVisible(shown)
        self.voice_button.setVisible(shown)

    def set_state(self, state: EngineState) -> None:
        self._state = state
        self._render()

    def set_health(self, health: Health | None) -> None:
        self._health = health
        self._render()

    def retranslate(self) -> None:
        self.models_button.setText(self.tr("3D models"))
        self.voice_button.setText(self.tr("Voice"))
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
        badge_styles = {
            EngineState.ready: (PALETTE_COLORS["success"], "rgba(16, 185, 129, 0.15)", "rgba(16, 185, 129, 0.3)"),
            EngineState.starting: (PALETTE_COLORS["warning"], "rgba(245, 158, 11, 0.15)", "rgba(245, 158, 11, 0.3)"),
            EngineState.loading: (PALETTE_COLORS["warning"], "rgba(245, 158, 11, 0.15)", "rgba(245, 158, 11, 0.3)"),
            EngineState.error: (PALETTE_COLORS["error"], "rgba(239, 68, 68, 0.15)", "rgba(239, 68, 68, 0.3)"),
            EngineState.crashed: (PALETTE_COLORS["error"], "rgba(239, 68, 68, 0.15)", "rgba(239, 68, 68, 0.3)"),
            EngineState.stopped: (PALETTE_COLORS["placeholder"], "rgba(107, 114, 128, 0.15)", "rgba(107, 114, 128, 0.3)"),
        }
        fg, bg, border = badge_styles.get(self._state, (PALETTE_COLORS["warning"], "rgba(245, 158, 11, 0.15)", "rgba(245, 158, 11, 0.3)"))
        self.state_label.setText(texts[self._state])
        self.state_label.setStyleSheet(
            f"color: {fg}; background-color: {bg}; border: 1px solid {border}; border-radius: 12px; padding: 3px 10px; font-weight: bold;"
        )
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
