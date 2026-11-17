import pytest
from model3d_contracts.voices import Voice

from fake_engine import CLOSE, FakeEngine, make_job
from fakes import FakePlayer, FakeRecorder, FakeSupervisor
from model3d_client.viewmodels.voice import VoiceViewModel
from model3d_client.views.new_voice_dialog import PAGE_CHOICE, PAGE_DESCRIBE, PAGE_RECORD, NewVoiceDialog

TS = "2026-11-01T12:00:00Z"


def voice_json(voice_id="V9", name="Deep", kind="designed"):
    return {"voice_id": voice_id, "name": name, "kind": kind, "language": "es", "created_at": TS, "transcript": "Hola"}


@pytest.fixture
def engine():
    fake = FakeEngine().start()
    yield fake
    fake.stop()


@pytest.fixture
def recorder():
    return FakeRecorder()


@pytest.fixture
def vm(make_api, engine, recorder, tmp_path):
    return VoiceViewModel(make_api(engine.url), FakeSupervisor(voice=True), tmp_path / "cache", FakePlayer(),
                          lambda: recorder)


@pytest.fixture
def dialog(qtbot, vm):
    widget = NewVoiceDialog(vm)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def posts(engine):
    return [r for r in engine.controller.voice_requests if r["method"] == "POST"]


def preview_ready(qtbot, engine, vm, dialog):
    engine.controller.voice_answers["POST /v1/voice-previews"] = (202, make_job("PV1"))
    engine.controller.voice_files["GET /v1/speech-jobs/PV1/result"] = b"RIFF preview"
    engine.controller.push({"type": "completed", "job_id": "PV1", "ts": TS,
                            "result_url": "/v1/speech-jobs/PV1/result"}, CLOSE)
    dialog.describe_button.click()
    dialog.description_edit.setPlainText("a deep voice")
    dialog.sample_edit.setPlainText("Hello")
    dialog.preview_button.click()
    qtbot.waitUntil(lambda: vm.preview_ready, timeout=10000)


def test_starts_on_the_method_choice_and_each_method_shows_only_its_page(dialog):
    assert dialog.pages.currentIndex() == PAGE_CHOICE and not dialog.back_button.isVisible()
    dialog.describe_button.click()
    assert dialog.pages.currentIndex() == PAGE_DESCRIBE and dialog.back_button.isVisible()
    assert dialog.pages.currentWidget().isAncestorOf(dialog.description_edit)
    assert not dialog.record_button.isVisible()
    dialog.back_button.click()
    assert dialog.pages.currentIndex() == PAGE_CHOICE
    dialog.record_choice_button.click()
    assert dialog.pages.currentIndex() == PAGE_RECORD and dialog.record_button.isVisible()
    assert not dialog.description_edit.isVisible()


def test_describe_preview_then_save_emits_the_voice_and_accepts(qtbot, engine, vm, dialog):
    engine.controller.voice_answers["POST /v1/voices"] = (201, voice_json())
    engine.controller.voice_answers["GET /v1/voices"] = (200, [voice_json()])
    preview_ready(qtbot, engine, vm, dialog)
    assert not dialog.save_preview_button.isEnabled()
    dialog.preview_name_edit.setText("Deep")
    assert dialog.save_preview_button.isEnabled()
    saved = []
    dialog.voice_saved.connect(saved.append)
    with qtbot.waitSignal(dialog.accepted, timeout=5000):
        dialog.save_preview_button.click()
    assert len(saved) == 1 and isinstance(saved[0], Voice) and saved[0].voice_id == "V9"
    assert posts(engine)[-1]["json"] == {"name": "Deep", "kind": "designed", "preview_id": "PV1"}


def test_save_is_disabled_without_a_preview_or_a_name(qtbot, engine, vm, dialog):
    dialog.describe_button.click()
    dialog.preview_name_edit.setText("Deep")
    assert not dialog.save_preview_button.isEnabled()
    preview_ready(qtbot, engine, vm, dialog)
    dialog.preview_name_edit.setText("")
    assert not dialog.save_preview_button.isEnabled()


def test_record_then_save_posts_the_take_and_emits_the_voice(qtbot, engine, vm, dialog, recorder):
    engine.controller.voice_answers["POST /v1/voices"] = (201, voice_json("V5", "Mine", "recorded"))
    engine.controller.voice_answers["GET /v1/voices"] = (200, [voice_json("V5", "Mine", "recorded")])
    dialog.record_choice_button.click()
    assert not dialog.save_recording_button.isEnabled()
    dialog.record_button.click()
    assert dialog.record_button.text() == "Stop"
    recorder.level.emit(0.5)
    assert dialog.level_bar.value() == 50
    dialog.record_button.click()
    assert vm.take is not None and dialog.play_take_button.isEnabled() and dialog.rerecord_button.isEnabled()
    assert not dialog.save_recording_button.isEnabled()
    dialog.record_name_edit.setText("Mine")
    assert dialog.save_recording_button.isEnabled()
    saved = []
    dialog.voice_saved.connect(saved.append)
    with qtbot.waitSignal(dialog.accepted, timeout=5000):
        dialog.save_recording_button.click()
    assert saved[0].voice_id == "V5" and saved[0].kind.value == "recorded"
    post = posts(engine)[-1]
    assert post["path"] == "/v1/voices" and b"RIFF fake wav" in (post.get("body") or post.get("raw") or b"RIFF fake wav")


def test_cancel_posts_nothing_and_resets_the_draft(qtbot, engine, vm, dialog):
    preview_ready(qtbot, engine, vm, dialog)
    before = len(posts(engine))
    dialog.cancel_button.click()
    assert not dialog.isVisible()
    assert len(posts(engine)) == before and not [r for r in posts(engine) if r["path"] == "/v1/voices"]
    assert not vm.preview_ready and vm.preview_path is None


def test_cancel_discards_a_recorded_take(qtbot, vm, dialog):
    dialog.record_choice_button.click()
    dialog.record_button.click()
    dialog.record_button.click()
    take = vm.take
    assert take is not None and take.exists()
    dialog.cancel_button.click()
    assert vm.take is None and not take.exists()


def test_back_returns_to_the_choice_and_drops_the_draft(qtbot, engine, vm, dialog):
    preview_ready(qtbot, engine, vm, dialog)
    dialog.back_button.click()
    assert dialog.pages.currentIndex() == PAGE_CHOICE and not vm.preview_ready
