# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for AI Bridge

block_cipher = None

a = Analysis(
    ['src/ai_bridge/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/ai_bridge/ui', 'ui'),
        ('src/ai_bridge/assets', 'assets'),
    ],
    hiddenimports=[
        'uvicorn.loops.asyncio',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        'uvicorn.logging',
        'pystray._win32',
        'ai_bridge',
        'ai_bridge.app',
        'ai_bridge.bridge',
        'ai_bridge.config',
        'ai_bridge.providers',
        'ai_bridge.quota',
        'ai_bridge.tray',
        'ai_bridge._resources',
        'ai_bridge.platform',
        'ai_bridge.platform.base',
        'ai_bridge.platform.windows',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AIBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='src/ai_bridge/assets/icon.ico',
)
