import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import model3d_client.i18n
from model3d_client.i18n import apply_language
from test_main_window import window  # noqa: F401  (fixture)

I18N = Path(model3d_client.i18n.__file__).parent


def test_switching_to_spanish_retranslates_open_window(window, qapp):  # noqa: F811
    try:
        apply_language(qapp, "es")
        assert window.generation_panel.generate_button.text() == "Generar"
        apply_language(qapp, "en")
        assert window.generation_panel.generate_button.text() == "Generate"
    finally:
        apply_language(qapp, "en")


def test_every_source_string_is_translated():
    messages = ElementTree.parse(I18N / "model3d_es.ts").getroot().findall(".//message")
    assert len(messages) >= 50
    for message in messages:
        translation = message.find("translation")
        source = message.findtext("source")
        assert translation is not None, source
        assert translation.get("type") not in ("unfinished", "vanished", "obsolete"), source
        assert (translation.text or "").strip(), source


def test_qm_is_up_to_date(tmp_path):
    lrelease = Path(sys.executable).parent / "pyside6-lrelease"
    subprocess.run([str(lrelease), str(I18N / "model3d_es.ts"), "-qm", str(tmp_path / "out.qm")],
                   check=True, capture_output=True)
    assert (tmp_path / "out.qm").read_bytes() == (I18N / "model3d_es.qm").read_bytes()


def test_spanish_catalog_has_rig_strings():
    translations = {
        message.findtext("source"): message.findtext("translation")
        for message in ElementTree.parse(I18N / "model3d_es.ts").getroot().findall(".//message")
    }
    assert translations["With skeleton"] == "Con esqueleto"
    assert translations["Add skeleton"] == "Agregar esqueleto"
    assert translations["Adding skeleton…"] == "Agregando esqueleto…"
