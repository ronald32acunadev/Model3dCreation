import pytest
from PySide6.QtCore import QCoreApplication

from model3d_client.i18n import apply_language
from model3d_client.setup.checks import RequirementId
from model3d_client.setup.runner import StepFailed
from model3d_client.viewmodels.setup import SetupPage
from model3d_client.views.setup_wizard import SetupWizard
from test_setup_vm import FakeRunner, make_vm, requirements  # noqa: F401  (fixture and helpers)


@pytest.fixture
def wizard(qtbot, make_vm):  # noqa: F811
    def build(**kwargs):
        view = SetupWizard(make_vm(**kwargs))
        qtbot.addWidget(view)
        return view

    return build


def test_wizard_starts_on_welcome_with_next_enabled(wizard):
    view = wizard()
    assert view.stack.currentWidget() is view.pages[SetupPage.welcome]
    assert view.next_button.isEnabled()
    assert view.back_button.isHidden()


def test_wizard_shows_requirement_command_for_missing_toolkit(wizard, make_vm):  # noqa: F811
    make_vm.state["reqs"] = requirements({RequirementId.cuda_toolkit})
    view = wizard()
    view.next_button.click()
    text = view.requirements_label.text()
    assert "sudo apt install cuda-toolkit-13-1" in text
    assert "https://developer.nvidia.com/cuda-downloads" in text
    assert "✗" in text


def test_wizard_next_disabled_while_blocked(wizard, make_vm):  # noqa: F811
    make_vm.state["reqs"] = requirements({RequirementId.driver})
    view = wizard()
    view.next_button.click()
    assert view.stack.currentWidget() is view.pages[SetupPage.requirements]
    assert not view.next_button.isEnabled()
    make_vm.state["reqs"] = requirements()
    view.recheck_button.click()
    assert view.next_button.isEnabled()


def test_wizard_folder_problem_is_explained(wizard, tmp_path):
    from model3d_client.setup.checks import FolderCheck

    view = wizard(folder_check=lambda folder, needed: FolderCheck(False, "no_symlinks", 10**12, needed))
    for _ in range(3):
        view.next_button.click()
    view.vm.choose_folder(tmp_path / "exfat")
    assert "exFAT" in view.folder_status.text()
    assert not view.next_button.isEnabled()


def test_wizard_install_page_shows_steps_and_log(wizard, tmp_path):
    FakeRunner.fail_with = StepFailed("no_space", "disk full", "weights")
    view = wizard()
    for _ in range(3):
        view.next_button.click()
    view.vm.choose_folder(tmp_path / "m3d")
    assert view.next_button.text() == "Install"
    view.next_button.click()
    assert view.stack.currentWidget() is view.pages[SetupPage.install]
    assert "working" in view.log_view.toPlainText()
    assert "The disk is full" in view.error_label.text()
    assert not view.retry_button.isHidden()
    assert view.cancel_button.isHidden()


def test_wizard_done_page_emits_completed(wizard, qtbot, tmp_path):
    view = wizard()
    for _ in range(3):
        view.next_button.click()
    view.vm.choose_folder(tmp_path / "m3d")
    view.next_button.click()
    assert view.stack.currentWidget() is view.pages[SetupPage.done]
    with qtbot.waitSignal(view.completed, timeout=1000):
        view.open_button.click()


def test_missing_folder_page_offers_choose_and_retry(qtbot, make_vm, tmp_path):  # noqa: F811
    from model3d_client.setup.recipe import Sources
    from model3d_client.viewmodels.setup import SetupViewModel
    from test_setup_vm import TOOLCHAIN, fake_recipe

    vm = SetupViewModel.for_existing(
        tmp_path / "unplugged", "missing_folder", sources=Sources(tmp_path, tmp_path / "uv", "0.1.0"),
        env=make_vm.env, check=lambda: (requirements(), TOOLCHAIN), runner_factory=FakeRunner,
        recipe=fake_recipe, background=lambda work: work())
    view = SetupWizard(vm)
    qtbot.addWidget(view)
    assert view.stack.currentWidget() is view.pages[SetupPage.missing_folder]
    assert str(tmp_path / "unplugged") in view.missing_label.text()
    view.choose_button.click()
    assert view.stack.currentWidget() is view.pages[SetupPage.welcome]


def test_wizard_retranslates_to_spanish(wizard, qapp):
    view = wizard()
    try:
        apply_language(qapp, "es")
        assert view.next_button.text() == QCoreApplication.translate("SetupWizard", "Next")
        assert view.next_button.text() == "Siguiente"
    finally:
        apply_language(qapp, "en")
    assert view.next_button.text() == "Next"
