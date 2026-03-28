# -*- mode: python ; coding: utf-8 -*-

import os
import shutil

from PyInstaller.config import CONF

project_root = os.path.abspath(SPECPATH)


def _clean_dir(path):
    if not path or not os.path.exists(path):
        return
    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        if os.path.isdir(entry_path):
            shutil.rmtree(entry_path, ignore_errors=True)
        else:
            try:
                os.remove(entry_path)
            except OSError:
                pass


CONF['noconfirm'] = True
_clean_dir(CONF.get('cachedir'))
_clean_dir(CONF.get('workpath'))

a = Analysis(
    [os.path.join(project_root, 'app.py')],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, 'templates'), 'templates'),
        (os.path.join(project_root, 'static'), 'static'),
        (os.path.join(project_root, 'data', '三元组.csv'), 'data'),
        (os.path.join(project_root, 'data', 'app_config.json'), 'data'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'mpl_toolkits'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 统一使用 onedir 产物，保留 dist/视频标注系统/data 供打包后直接编辑。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='视频标注系统',
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
    name='视频标注系统',
)
