"""Desktop app entry point: python -m model3d_client."""

import os
import sys
from collections.abc import Mapping

from model3d_client.app import build_app, build_setup, startup_mode
from model3d_client.paths import REPO_ROOT
from model3d_client.setup.state import read_pointer


def _yes() -> bool:
    return True


class Launcher:
    """Opens the setup wizard or the main window, and switches between them."""

    def __init__(self, argv: list[str], env: Mapping[str, str]):
        self.argv = argv
        self.env = env
        self.app = None
        self.window = None
        self.wizard = None
        self._mode = ""

    def start(self) -> int:
        mode = startup_mode(self.env, REPO_ROOT)
        if mode in ("main", "development"):
            self.show_main()
        else:
            self.show_setup(mode)
        return self.app.exec()

    def show_main(self) -> None:
        self.app, self.window = build_app(self.argv, self.env)
        self.window.vm.install_dir = read_pointer(self.env)
        self.window.vm.setup_requested.connect(self._on_setup_requested)
        self.window.show()
        self.window.vm.supervisor.start()

    def show_setup(self, mode: str) -> None:
        self.app, self.wizard = build_setup(self.argv, self.env, mode)
        self.wizard.completed.connect(self._on_setup_completed)
        self.wizard.show()

    # Each switch shows the next window before closing the current one, so Qt never sees zero windows and quits.
    def _on_setup_completed(self) -> None:
        wizard = self.wizard
        self.show_main()
        wizard.close()

    def _on_setup_requested(self, mode: str) -> None:
        self._mode = mode
        self.window.vm.shutdown(self._after_engine_stopped)  # the engine stops before its environment changes

    def _after_engine_stopped(self) -> None:
        window = self.window
        self.window = None
        self.show_setup(self._mode)
        window.ask_close_anyway = _yes
        window.close()


def main() -> int:
    return Launcher(sys.argv, os.environ).start()


if __name__ == "__main__":
    sys.exit(main())
