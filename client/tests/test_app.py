from fakes import StubViewer
from model3d_client.app import build_app
from model3d_client.services.supervisor import EngineState


def test_build_app_wires_window_without_starting_engine(qtbot, tmp_path):
    app, window = build_app([], env={"MODEL3D_HOME": str(tmp_path)}, viewer_factory=StubViewer)
    window.ask_close_anyway = lambda: True
    qtbot.addWidget(window)
    assert window.vm.paths.data == tmp_path
    assert window.vm.supervisor.state == EngineState.stopped
    assert window.windowTitle() == "Model3D"
    assert window.vm.library.root == tmp_path / "library"
