# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('qfluentwidgets')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
datas += [('installer/assets/setup-icon.ico', 'installer/assets')]

# The app only uses QtCore/QtGui/QtWidgets; qfluentwidgets additionally needs
# QtSvg/QtSvgWidgets (icons) and QtXml (icon.py). Every PySide6 module below was
# verified NOT to be imported by the app or by `import qfluentwidgets`, so it is
# safe to drop to shrink the bundle. Do NOT add QtSvg/QtSvgWidgets/QtXml here.
excluded_qt_modules = [
    # Web engine / web stack (largest savings)
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    # QML / Quick
    "PySide6.QtQml", "PySide6.QtQmlModels", "PySide6.QtQmlWorkerScript",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2", "PySide6.QtQuick3D",
    # 3D
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    # Charts / data visualization / graphs
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
    # Multimedia (qfluentwidgets.multimedia is not used by this app)
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    # Networking (not imported)
    "PySide6.QtNetwork", "PySide6.QtNetworkAuth", "PySide6.QtHttpServer",
    # PDF
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    # Connectivity / sensors / hardware
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    # SQL / test / tooling
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    # OpenGL python modules (not imported)
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    # Misc
    "PySide6.QtPrintSupport", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech", "PySide6.QtConcurrent", "PySide6.QtDBus",
]


a = Analysis(
    ['main_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_qt_modules,
    noarchive=False,
    optimize=0,
)

# PyInstaller's PySide6 hook copies almost every Qt6 DLL/plugin as a binary even
# when the Python module is excluded above. The app uses only QtCore/QtGui/
# QtWidgets (+ QtSvg/QtSvgWidgets/QtXml via qfluentwidgets), so strip the unused
# Qt feature DLLs from the bundle. opengl32sw.dll is intentionally kept as the
# software-OpenGL fallback for VMs/RDP/driverless machines.
_drop_binary_substrings = (
    "qt6quick", "qt6qml", "qt63d", "qt6charts", "qt6datavisualization", "qt6graphs",
    "qt6multimedia", "qt6spatialaudio", "qt6network", "qt6pdf", "qt6opengl",
    "qt6bluetooth", "qt6nfc", "qt6positioning", "qt6location", "qt6sensors",
    "qt6serialport", "qt6serialbus", "qt6sql", "qt6test", "qt6designer", "qt6help",
    "qt6webengine", "qt6webchannel", "qt6websockets", "qt6webview", "qt6httpserver",
    "qt6remoteobjects", "qt6scxml", "qt6statemachine", "qt6texttospeech",
    "qt6printsupport", "qt6virtualkeyboard", "qt6quick3d", "qt6uitools",
)


def _keep_binary(dest_name: str) -> bool:
    name = dest_name.replace("\\", "/").lower()
    if any(token in name for token in _drop_binary_substrings):
        return False
    # Drop QML payloads and plugins for unused Qt feature groups.
    if "/qml/" in name or name.startswith("pyside6/qml"):
        return False
    return True


a.binaries = [b for b in a.binaries if _keep_binary(b[0])]
a.datas = [d for d in a.datas if _keep_binary(d[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='KEF Controller',
    debug=False,
    icon='installer/assets/setup-icon.ico',
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
