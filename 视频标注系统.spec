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


def _prepare_external_data(app_dist_dir):
    data_dir = os.path.join(app_dist_dir, 'data')
    internal_data_dir = os.path.join(app_dist_dir, '_internal', 'data')

    os.makedirs(os.path.join(data_dir, 'videos'), exist_ok=True)
    os.makedirs(os.path.join(data_dir, 'annotations'), exist_ok=True)

    for filename in ('三元组.csv', 'app_config.json'):
        source_path = os.path.join(project_root, 'data', filename)
        target_path = os.path.join(data_dir, filename)
        if os.path.exists(source_path):
            shutil.copy2(source_path, target_path)

        internal_path = os.path.join(internal_data_dir, filename)
        if os.path.exists(internal_path):
            os.remove(internal_path)

    if os.path.isdir(internal_data_dir) and not os.listdir(internal_data_dir):
        os.rmdir(internal_data_dir)


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

_prepare_external_data(coll.name)
