"""Spanish translation loading; English is the source language."""

import logging
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QCoreApplication, QEvent, QTranslator
from PySide6.QtWidgets import QApplication

I18N_DIR = Path(__file__).parent
QM_FILE = I18N_DIR / "model3d_es.qm"
logger = logging.getLogger(__name__)

_installed: QTranslator | None = None


def apply_language(app: QApplication, language: Literal["en", "es"]) -> None:
    """Switch the app's language and retranslate every open widget before returning."""
    global _installed
    if _installed is not None:
        app.removeTranslator(_installed)
        _installed = None
    if language == "es":
        translator = QTranslator(app)
        if translator.load(str(QM_FILE)):
            app.installTranslator(translator)
            _installed = translator
        else:
            logger.warning("Could not load the Spanish translation %s", QM_FILE)
    # Qt posts LanguageChange; deliver it now so widgets are retranslated when this returns.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.LanguageChange)
