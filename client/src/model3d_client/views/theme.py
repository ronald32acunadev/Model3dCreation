"""Dark Fusion palette and QSS theme for the model3d desktop client."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

PALETTE_COLORS: dict[str, str] = {
    "background": "#141518",
    "surface": "#1e1f24",
    "surface_elevated": "#282a30",
    "border": "#383b44",
    "border_focus": "#6366f1",
    "primary": "#6366f1",
    "primary_hover": "#4f46e5",
    "primary_pressed": "#4338ca",
    "text": "#f1f2f6",
    "text_secondary": "#9aa0a6",
    "error": "#ef4444",
    "warning": "#f59e0b",
    "success": "#10b981",
    "placeholder": "#6b7280",
}


def _build_palette() -> QPalette:
    palette = QPalette()
    c = PALETTE_COLORS

    palette.setColor(QPalette.ColorRole.Window, QColor(c["background"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(c["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["surface_elevated"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["surface_elevated"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["placeholder"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(c["surface_elevated"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(Qt.GlobalColor.white))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(c["primary"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(Qt.GlobalColor.white))
    palette.setColor(QPalette.ColorRole.Link, QColor(c["primary"]))

    disabled_text = QColor(c["placeholder"])
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, disabled_text)

    return palette


def _build_stylesheet() -> str:
    c = PALETTE_COLORS
    return f"""
QWidget {{
    background-color: {c["background"]};
    color: {c["text"]};
}}

QPushButton {{
    border-radius: 6px;
    padding: 6px 14px;
    background-color: {c["surface_elevated"]};
    border: 1px solid {c["border"]};
    color: {c["text"]};
}}

QPushButton:hover {{
    background-color: {c["border"]};
}}

QPushButton:pressed {{
    background-color: {c["surface"]};
}}

QPushButton:disabled {{
    color: {c["placeholder"]};
}}

QPushButton[primary="true"] {{
    background-color: {c["primary"]};
    border: none;
    color: #ffffff;
    font-weight: bold;
}}

QPushButton[primary="true"]:hover {{
    background-color: {c["primary_hover"]};
}}

QPushButton[primary="true"]:pressed {{
    background-color: {c["primary_pressed"]};
}}

QPushButton[primary="true"]:disabled {{
    background-color: {c["border"]};
    color: {c["placeholder"]};
}}

QLineEdit,
QTextEdit,
QPlainTextEdit,
QSpinBox {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 6px;
    color: {c["text"]};
}}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QSpinBox:focus {{
    border: 1px solid {c["border_focus"]};
}}

QComboBox {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 4px 8px;
    color: {c["text"]};
}}

QComboBox:focus {{
    border: 1px solid {c["border_focus"]};
}}

QComboBox::drop-down {{
    border: none;
}}

QComboBox QAbstractItemView {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    selection-background-color: {c["surface_elevated"]};
    selection-color: {c["primary"]};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {c["border"]};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background: {c["primary"]};
}}

QScrollBar::sub-line:vertical,
QScrollBar::add-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0px;
}}

QScrollBar::handle:horizontal {{
    background: {c["border"]};
    border-radius: 4px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {c["primary"]};
}}

QScrollBar::sub-line:horizontal,
QScrollBar::add-line:horizontal {{
    width: 0px;
}}

QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: none;
}}

QSplitter::handle {{
    background-color: {c["surface_elevated"]};
}}

QSplitter::handle:hover {{
    background-color: {c["primary"]};
}}

QProgressBar {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {c["primary"]};
    border-radius: 3px;
}}

QGroupBox {{
    border: 1px solid {c["border"]};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 12px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {c["text_secondary"]};
}}

QTreeWidget,
QTreeView,
QListWidget,
QListView {{
    background-color: {c["surface"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}

QTreeWidget::item,
QTreeView::item,
QListWidget::item,
QListView::item {{
    padding: 6px 8px;
    border-radius: 4px;
    color: {c["text"]};
}}

QTreeWidget::item:hover,
QTreeView::item:hover,
QListWidget::item:hover,
QListView::item:hover {{
    background-color: {c["surface_elevated"]};
}}

QTreeWidget::item:selected,
QTreeView::item:selected,
QListWidget::item:selected,
QListView::item:selected {{
    background-color: {c["surface_elevated"]};
    border-left: 3px solid {c["primary"]};
    color: #ffffff;
}}

QMenu {{
    background-color: {c["surface_elevated"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 4px 0px;
}}

QMenu::item {{
    padding: 6px 24px;
    color: {c["text"]};
}}

QMenu::item:selected {{
    background-color: {c["primary"]};
    color: #ffffff;
}}

QCheckBox,
QRadioButton {{
    spacing: 8px;
    color: {c["text"]};
}}

QCheckBox::indicator,
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c["border"]};
    border-radius: 4px;
    background-color: {c["surface"]};
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:hover,
QRadioButton::indicator:hover {{
    border-color: {c["primary"]};
}}

QCheckBox::indicator:checked,
QRadioButton::indicator:checked {{
    background-color: {c["primary"]};
    border-color: {c["primary"]};
}}

QTabWidget::pane {{
    border: 1px solid {c["border"]};
    border-radius: 6px;
    background-color: {c["surface"]};
    top: -1px;
}}

QTabBar::tab {{
    background-color: {c["background"]};
    border: 1px solid {c["border"]};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 14px;
    margin-right: 2px;
    color: {c["text_secondary"]};
}}

QTabBar::tab:selected {{
    background-color: {c["surface"]};
    border-color: {c["border"]};
    color: {c["text"]};
    font-weight: bold;
}}

QTabBar::tab:hover:!selected {{
    background-color: {c["surface_elevated"]};
}}

#dropArea {{
    border: 2px dashed {c["border"]};
    border-radius: 8px;
    background-color: {c["surface"]};
    color: {c["text_secondary"]};
    padding: 12px;
}}

#dropArea:hover {{
    border-color: {c["primary"]};
    background-color: {c["surface_elevated"]};
    color: {c["text"]};
}}

QToolTip {{
    background-color: {c["surface_elevated"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 4px;
    padding: 4px;
}}

#topBar {{
    background-color: {c["surface"]};
    border-bottom: 1px solid {c["border"]};
}}

#topBar QPushButton#tabButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 14px;
    color: {c["text_secondary"]};
    font-weight: 500;
}}

#topBar QPushButton#tabButton:hover {{
    background-color: {c["surface_elevated"]};
    color: {c["text"]};
}}

#topBar QPushButton#tabButton:checked {{
    background-color: {c["primary"]};
    color: #ffffff;
    font-weight: bold;
    border: none;
}}

#metaBadge {{
    color: {c["text_secondary"]};
    font-size: 11px;
}}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the dark Fusion palette and QSS stylesheet to the application."""
    app.setStyle("Fusion")
    app.setPalette(_build_palette())
    app.setStyleSheet(_build_stylesheet())
