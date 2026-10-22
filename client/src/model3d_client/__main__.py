"""Desktop app entry point: python -m model3d_client."""

import sys

from model3d_client.app import build_app


def main() -> int:
    app, window = build_app(sys.argv)
    window.show()
    window.vm.supervisor.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
