import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton, QWidget

from model3d_client.views.theme import PALETTE_COLORS, apply_theme


def test_apply_theme_sets_stylesheet_and_palette(qapp):
    apply_theme(qapp)
    sheet = qapp.styleSheet()
    assert sheet != ""
    assert PALETTE_COLORS["background"] in sheet
    assert PALETTE_COLORS["primary"] in sheet
    assert PALETTE_COLORS["surface"] in sheet

    palette = qapp.palette()
    assert palette.color(palette.ColorRole.Window).name().lower() == PALETTE_COLORS["background"].lower()
    assert palette.color(palette.ColorRole.Highlight).name().lower() == PALETTE_COLORS["primary"].lower()


def test_apply_theme_widgets_instantiation(qapp):
    apply_theme(qapp)
    w = QWidget()
    btn = QPushButton("Click Me", w)
    primary_btn = QPushButton("Primary", w)
    primary_btn.setProperty("primary", True)
    line = QLineEdit("text input", w)
    combo = QComboBox(w)
    combo.addItem("Item 1")

    assert btn.text() == "Click Me"
    assert primary_btn.property("primary") is True
    assert line.text() == "text input"
    assert combo.count() == 1
