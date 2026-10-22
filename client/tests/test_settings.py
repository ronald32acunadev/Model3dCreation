from pathlib import Path

from model3d_contracts.jobs import Quality

from model3d_client.services.settings import Settings, load_settings, save_settings


def test_first_run_language_follows_spanish_locale(tmp_path):
    assert load_settings(tmp_path / "s.json", "es_AR").language == "es"
    assert load_settings(tmp_path / "s.json", "de_DE").language == "en"


def test_first_run_defaults(tmp_path):
    s = load_settings(tmp_path / "s.json", "en_US")
    assert s.default_texture is True
    assert s.default_quality == Quality.final
    assert s.export_dir == Path.home()


def test_settings_round_trip(tmp_path):
    s = Settings(language="es", default_texture=False, default_quality=Quality.draft, export_dir=tmp_path)
    save_settings(tmp_path / "sub" / "s.json", s)
    assert load_settings(tmp_path / "sub" / "s.json", "en_US") == s
    assert not list((tmp_path / "sub").glob("*.tmp"))


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    (tmp_path / "s.json").write_text("{not json")
    assert load_settings(tmp_path / "s.json", "en_US") == Settings()
