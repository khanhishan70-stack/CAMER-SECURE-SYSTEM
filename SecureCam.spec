# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('C:\\Users\\Admin\\OneDrive\\Desktop\\camera SECU 2\\static', 'static'), ('C:\\Users\\Admin\\OneDrive\\Desktop\\camera SECU 2\\app', 'app'), ('C:\\Users\\Admin\\AppData\\Roaming\\Python\\Python314\\site-packages\\insightface', 'insightface'), ('C:\\Users\\Admin\\.insightface\\models\\buffalo_l\\1k3d68.onnx', '.insightface/models/buffalo_l'), ('C:\\Users\\Admin\\.insightface\\models\\buffalo_l\\2d106det.onnx', '.insightface/models/buffalo_l'), ('C:\\Users\\Admin\\.insightface\\models\\buffalo_l\\det_10g.onnx', '.insightface/models/buffalo_l'), ('C:\\Users\\Admin\\.insightface\\models\\buffalo_l\\genderage.onnx', '.insightface/models/buffalo_l'), ('C:\\Users\\Admin\\.insightface\\models\\buffalo_l\\w600k_r50.onnx', '.insightface/models/buffalo_l')]
datas += collect_data_files('onnxruntime')


a = Analysis(
    ['C:\\Users\\Admin\\OneDrive\\Desktop\\camera SECU 2\\run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['app.main', 'app.config', 'app.camera_utils', 'app.face_ai', 'app.face_store', 'app.password_store', 'app.security_engine', 'app.lock_screen', 'app.windows_lock', 'onnxruntime', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'tensorflow', 'jax', 'sklearn', 'skimage', 'matplotlib', 'sympy', 'tables', 'PIL.ImageQt', 'insightface'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SecureCam',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SecureCam',
)
