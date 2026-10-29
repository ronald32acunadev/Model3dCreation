from datetime import UTC, datetime

import pytest
from model3d_contracts.jobs import JobOptions

from model3d_client.services.library import Entry, Library, Outcome
from model3d_client.views.library_panel import LibraryPanel


@pytest.fixture
def panel(qtbot, tmp_path):
    widget = LibraryPanel(Library(tmp_path / "library"))
    qtbot.addWidget(widget)
    return widget


def rigged_entry() -> Entry:
    return Entry(
        entry_id="20261029T120000-abcdef",
        name="Robot",
        created_at=datetime.now(UTC),
        options=JobOptions(rig=True),
        outcome=Outcome.succeeded,
        input_file="input.png",
    )


def action(menu, name: str):
    return next(a for a in menu.actions() if a.objectName() == name)


def test_menu_lists_add_walk_after_add_skeleton(panel):
    names = [a.objectName() for a in panel.build_menu(rigged_entry()).actions()]
    assert names == ["rename", "delete", "add_skeleton", "add_walk"]


def test_add_walk_follows_can_add_walk(panel):
    entry = rigged_entry()
    panel.can_add_walk = lambda _entry: True
    assert action(panel.build_menu(entry), "add_walk").isEnabled()
    panel.can_add_walk = lambda _entry: False
    assert not action(panel.build_menu(entry), "add_walk").isEnabled()


def test_add_walk_disabled_by_default(panel):
    assert not action(panel.build_menu(rigged_entry()), "add_walk").isEnabled()
