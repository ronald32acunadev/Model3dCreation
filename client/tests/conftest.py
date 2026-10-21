import os

# Widgets render offscreen in tests; set before any Qt module is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
