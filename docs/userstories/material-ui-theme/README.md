# Material UI Theme for PySide6 Client

Status: US-01..US-03 approved 2026-11-17 ("Dale las apruebo subamos todas"), execution mode: all stories. US-01, US-02, and US-03 completed in commit `13d94e8`. Branch: `feat/material-ui`.

## Context

Request (verbatim, 2026-11-17): "Trabajamos en un nuevo branch , quiero mejorar la interfaz se ve un poco simple muy generica me gustaria implementar un framework visual tipo materialize"

The desktop client is a native Python desktop application using PySide6 (Qt6 Widgets). It runs on Linux/Ubuntu as a Debian package whose runtime uses system `apt` dependencies (`python3-pyside6.*`, `python3-pydantic`). Web CSS frameworks like Materialize cannot be injected directly into Qt desktop widgets, so this feature provides a native Material Design system in Qt using QSS (Qt Style Sheets) and custom palettes.

Goals:
- Replace generic unstyled Qt widgets with a modern, cohesive Material Design dark aesthetic (elevated surfaces, cards, crisp inputs, modern buttons, slim scrollbars, accent colors).
- Keep zero extra runtime package dependencies (pure PySide6) to preserve Debian packaging and system Python compatibility.
- Harmonize scattered inline styles with the centralized design system.
- Preserve 100% of existing functionality, accessibility, and test suite green.

## US-01: Centralized Material Design System & QSS Architecture

As a user, I want the client application to load with a unified Material Design dark theme so that all desktop windows look modern, elevated, and visually consistent.

- US-01 / AC-01: A centralized theme module `model3d_client/views/theme.py` defines the Material Dark design tokens (surfaces `#141518`, `#1e1f24`, `#282a30`, borders `#383b44`, accent primary `#6366f1` / `#7c4dff`, text `#f1f2f6` / `#9aa0a6`, states: hover, active, disabled).
- US-01 / AC-02: `apply_theme(app: QApplication)` applies a compatible `QPalette` and the generated Material QSS stylesheet covering base widgets (`QWidget`, `QPushButton`, `QLineEdit`, `QTextEdit`, `QSpinBox`, `QComboBox`, `QListWidget`, `QScrollArea`, `QScrollBar`, `QSplitter`, `QProgressBar`, `QDialog`, `QMessageBox`).
- US-01 / AC-03: `build_app()` and `build_setup()` in `model3d_client/app.py` call `apply_theme(app)` during startup, ensuring both the main app and the setup wizard receive the theme.
- US-01 / AC-04: Unit tests in `tests/test_theme.py` verify that `apply_theme` configures the palette and stylesheet without throwing exceptions or leaving unstyled core controls.

## US-02: Modern TopBar, Section Navigation, and Status Badges

As a user, I want the top navigation bar and window headers to look like a polished Material app bar with clear section tabs and status indicators.

- US-02 / AC-01: `TopBar` displays elevated surface styling (`#1e1f24`), subtle bottom border, and clean spacing between title, sections, and engine status.
- US-02 / AC-02: Section navigation buttons ("3D Models", "Voice Studio") use distinct active/inactive tab styling with accent indicator or pill background.
- US-02 / AC-03: Engine state indicator uses a rounded pill badge with semantic color coding (ready: green `#10b981`, busy/warning: amber `#f59e0b`, error: red `#ef4444`, offline: gray `#6b7280`) matching Material badge standards.
- US-02 / AC-04: Existing top bar signals and tests in `tests/test_main_window.py` continue to pass without regression.

## US-03: Card Layouts, Drop Zones, and Inline Style Harmonization

As a user, I want the side panels, drop zones, library cards, and dialogs to have cohesive Material card surfaces and clean interactive feedback.

- US-03 / AC-01: The image drop zone in `GenerationPanel` is styled as an elevated Material dashed card with smooth hover states and clear placeholder typography.
- US-03 / AC-02: Scattered hardcoded inline `setStyleSheet` calls in `generation_panel.py`, `voice_panel.py`, `new_voice_dialog.py`, and `setup_wizard.py` are harmonized with semantic theme classes or properties (e.g. `error`, `warning`, `muted`).
- US-03 / AC-03: Dialogs (`CharacterDialog`, `NewVoiceDialog`, `SettingsDialog`, `SetupWizard`) present cohesive padded card layouts, clean inputs, and standard Material primary/secondary button styling.
- US-03 / AC-04: The full client test suite passes with all 426+ tests green.

## QA Evidence and Traceability

| Story / criterion or change-related regression risk | Test file and test name, or reproducible manual procedure | Observed result and evidence |
| --- | --- | --- |
| `US-01 / AC-01..AC-04` | `client/tests/test_theme.py`, `client/tests/test_app.py` | Passed (14 tests passed, verified palette and QSS tokens) |
| `US-02 / AC-01..AC-04` | `client/tests/test_main_window.py`, `client/tests/test_voice_panel.py` | Passed (62 tests passed, top bar render and section switches verified) |
| `US-03 / AC-01..AC-04` | `cd client && uv run pytest` | Passed (428 passed, 0 failures, verified drop area, buttons, dialogs, and full client suite) |
