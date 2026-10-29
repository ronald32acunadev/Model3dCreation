from datetime import UTC, datetime

import pytest

from model3d_client.setup.state import (
    InstallState,
    StepRecord,
    config_file,
    load_state,
    read_pointer,
    save_state,
    write_pointer,
)


def test_pointer_round_trip_uses_xdg_config_home(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg"), "HOME": str(tmp_path / "home")}
    install_dir = tmp_path / "Disco 2" / "Modelos 3D"
    install_dir.mkdir(parents=True)
    write_pointer(env, install_dir)
    assert (tmp_path / "cfg/model3d/config.json").is_file()
    assert read_pointer(env) == install_dir.resolve()
    assert not (tmp_path / "home").exists()


def test_config_file_falls_back_to_home_and_none(tmp_path):
    home = tmp_path / "home"
    expected = home / ".config/model3d/config.json"
    assert config_file({"HOME": str(home)}) == expected
    assert config_file({"XDG_CONFIG_HOME": "", "HOME": str(home)}) == expected
    assert config_file({}) is None


def test_read_pointer_missing_or_corrupt_returns_none(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    assert read_pointer(env) is None
    path = tmp_path / "cfg/model3d/config.json"
    path.parent.mkdir(parents=True)
    for content in ("not json", "[1]", '{"install_dir": ""}'):
        path.write_text(content)
        assert read_pointer(env) is None
    assert read_pointer({}) is None


def test_write_pointer_without_home_raises(tmp_path):
    with pytest.raises(RuntimeError, match="HOME"):
        write_pointer({}, tmp_path)


def test_state_round_trip(tmp_path):
    state = InstallState(
        app_version="0.1.0",
        components=["base", "skeletons"],
        steps={
            "python": StepRecord(fingerprint="a", finished_at=datetime(2026, 10, 23, tzinfo=UTC)),
            "source": StepRecord(fingerprint="b", finished_at=datetime(2026, 10, 24, tzinfo=UTC)),
        },
    )
    save_state(tmp_path, state)
    assert (tmp_path / "install.json").is_file()
    assert load_state(tmp_path) == state


def test_load_state_missing_or_invalid_returns_none(tmp_path):
    assert load_state(tmp_path) is None
    (tmp_path / "install.json").write_text('{"app_version": 1}')
    assert load_state(tmp_path) is None
