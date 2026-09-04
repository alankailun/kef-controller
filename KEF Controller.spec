# -*- mode: python ; coding: utf-8 -*-

datas = []
binaries = []
hiddenimports = []

hiddenimports += [
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',
    'webview.platforms.win32',
    'clr',
    'pythonnet',
    'clr_loader',
]
datas += [('installer/assets/setup-icon.ico', 'installer/assets')]
datas += [('kef_app/ui/web', 'kef_app/ui/web')]

# The app only uses QtCore/QtGui/QtWidgets (tray icons are drawn with QPainter,
# the app icon is an .ico). Every PySide6 module below was verified NOT to be
# imported by the app, so it is safe to drop to shrink the bundle.
excluded_qt_modules = [
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtXml",
    # QML / Quick
    "PySide6.QtQml", "PySide6.QtQmlModels", "PySide6.QtQmlWorkerScript",
    "PySide6.QtQuickControls2", "PySide6.QtQuick3D",
    # 3D
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    # Charts / data visualization / graphs
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
    # Multimedia is not used by this app.
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    # Networking APIs not used by the controller.
    "PySide6.QtNetwork", "PySide6.QtNetworkAuth", "PySide6.QtHttpServer",
    # PDF
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    # Connectivity / sensors / hardware
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    # SQL / test / tooling
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    # WebEngine is intentionally excluded: Edge WebView2 supplies the browser.
    "PySide6.QtWebView", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    # Misc.
    "PySide6.QtRemoteObjects", "PySide6.QtScxml",
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

_drop_binary_substrings = (
    "qt63d", "qt6charts", "qt6datavisualization", "qt6graphs",
    "qt6multimedia", "qt6spatialaudio", "qt6pdf", "qt6opengl",
    "qt6bluetooth", "qt6nfc", "qt6location", "qt6sensors",
    "qt6serialport", "qt6serialbus", "qt6sql", "qt6test", "qt6designer", "qt6help",
    "qt6websockets", "qt6webview", "qt6httpserver",
    "qt6network", "qt6svg",
    "qt6remoteobjects", "qt6scxml", "qt6statemachine", "qt6texttospeech",
    "qt6virtualkeyboard", "qt6quick3d", "qt6uitools",
    # The UI is Edge WebView2; Qt only provides the event loop, tray, and
    # raster QPainter icons. It creates no QML/Quick scene or GL surface.
    "qt6qml", "qt6quick", "opengl32sw",
)

_drop_exact_paths = {
    # Qt6Core uses Windows' system ICU.  A generic ICU installation found on
    # PATH can otherwise be mistaken for this dependency by PyInstaller; its
    # same-named icuuc.dll has a different ABI and makes QtCore fail to load.
    "icuuc.dll",
    "icudt78.dll",
    # Keep only the Windows platform backend. The direct2d/offscreen/minimal
    # backends are optional alternatives and not used by this desktop app.
    "pyside6/plugins/platforms/qdirect2d.dll",
    "pyside6/plugins/platforms/qminimal.dll",
    "pyside6/plugins/platforms/qoffscreen.dll",
    # Image formats not used by the app. Keep qico for the Windows app icon.
    "pyside6/plugins/imageformats/qsvg.dll",
    "pyside6/plugins/iconengines/qsvgicon.dll",
    "pyside6/plugins/imageformats/qgif.dll",
    "pyside6/plugins/imageformats/qicns.dll",
    "pyside6/plugins/imageformats/qjpeg.dll",
    "pyside6/plugins/imageformats/qpdf.dll",
    "pyside6/plugins/imageformats/qtga.dll",
    "pyside6/plugins/imageformats/qtiff.dll",
    "pyside6/plugins/imageformats/qwbmp.dll",
    "pyside6/plugins/imageformats/qwebp.dll",
    # Optional plugins not used by this UI. Keep qmodernwindowsstyle.dll: it is
    # small and preserves modern Windows styling for native controls such as
    # combo boxes, scrollbars, and push buttons in the frozen app.
    "pyside6/plugins/generic/qtuiotouchplugin.dll",
    "pyside6/plugins/networkinformation/qnetworklistmanager.dll",
    "pyside6/plugins/tls/qcertonlybackend.dll",
    "pyside6/plugins/tls/qopensslbackend.dll",
    "pyside6/plugins/tls/qschannelbackend.dll",
    "pyside6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
}


def _keep_binary(dest_name: str) -> bool:
    name = dest_name.replace("\\", "/").lower()
    if name.startswith("pyside6/translations/"):
        return False
    if name in _drop_exact_paths:
        return False
    if any(token in name for token in _drop_binary_substrings):
        return False
    # Drop QML payloads and plugins for unused Qt feature groups.
    if "/qml/" in name or name.startswith("pyside6/qml"):
        return False
    return True


a.binaries = [entry for entry in a.binaries if _keep_binary(entry[0])]
a.datas = [entry for entry in a.datas if _keep_binary(entry[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KEF Controller',
    debug=False,
    icon='installer/assets/setup-icon.ico',
    bootloader_ignore_signals=False,
    strip=False,
    # Inno Setup's solid LZMA compression handles the onedir payload as a
    # whole.  UPX gives unsigned desktop software an unnecessary antivirus
    # false-positive risk and is deliberately disabled.
    upx=False,
    upx_exclude=[],
    version='installer/version_info.txt',
    # COLLECT inherits this setting from EXE in PyInstaller 6.
    contents_directory='runtime',
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='KEF Controller',
)
