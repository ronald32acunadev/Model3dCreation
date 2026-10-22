from model3d_contracts.jobs import Quality
from PySide6.QtCore import QCoreApplication

from model3d_client.i18n import apply_language
from model3d_client.services.settings import load_settings
from model3d_client.views.settings_dialog import SettingsDialog
from test_main_window import window  # noqa: F401  (fixture)


def test_settings_dialog_saves_and_applies(window, qapp, tmp_path):  # noqa: F811
    dialog = SettingsDialog(window.vm)
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("es"))
    dialog.export_edit.setText(str(tmp_path / "exports"))
    dialog.quality_combo.setCurrentIndex(dialog.quality_combo.findData(Quality.draft))
    try:
        dialog.accept()
        saved = load_settings(window.vm.paths.settings_file, "en")
        assert saved.language == "es"
        assert saved.export_dir == tmp_path / "exports"
        assert saved.default_quality == Quality.draft
        assert window.vm.settings.language == "es"
        assert QCoreApplication.translate("GenerationPanel", "Generate") == "Generar"
    finally:
        apply_language(qapp, "en")


def test_settings_dialog_starts_from_current_settings(window):  # noqa: F811
    window.vm.settings = window.vm.settings.model_copy(update={"language": "es", "default_quality": Quality.draft})
    dialog = SettingsDialog(window.vm)
    assert dialog.language_combo.currentData() == "es"
    assert dialog.quality_combo.currentData() == Quality.draft


def test_settings_dialog_shows_engine_log_tail(window):  # noqa: F811
    log = window.vm.paths.engine_log
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(f"line {i}" for i in range(250)))
    dialog = SettingsDialog(window.vm)  # keep a reference: the dialog has no parent
    text = dialog.log_view.toPlainText()
    assert text.startswith("line 50\n")
    assert text.endswith("line 249")


def test_settings_dialog_without_log(window):  # noqa: F811
    dialog = SettingsDialog(window.vm)
    assert dialog.log_view.toPlainText() == "No engine log yet."


def test_settings_dialog_offers_high_detail(window):  # noqa: F811
    dialog = SettingsDialog(window.vm)
    combo = dialog.quality_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [Quality.draft, Quality.final, Quality.high]
    assert combo.itemText(2) == "High detail"
