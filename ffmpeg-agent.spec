# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：ffmpeg-agent Windows 便携版（onedir, 无控制台）
# 构建前需先：npm run build（frontend/dist）、下载 ffmpeg/ffprobe.exe 到 ffmpeg/
# 用法：.venv\Scripts\python -m PyInstaller --noconfirm --clean ffmpeg-agent.spec

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []


def add_collect(pkg):
    """收集某包的全部子模块+数据+二进制（用于动态导入较多的包）。"""
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# 动态导入较多、hooks 覆盖不全的包
for pkg in (
    'chromadb',
    'sentence_transformers',
    'langchain',
    'langchain_core',
    'langchain_openai',
    'langchain_chroma',
    'langchain_text_splitters',
    'langgraph',
    'fastapi',
    'uvicorn',
    'bs4',
):
    add_collect(pkg)

# 动态导入补充
hiddenimports += collect_submodules('chromadb.utils')
hiddenimports += [
    'app.main',
    'app.graph',
    'app.agents',
    'app.tools',
    'app.model',
    'app.db_search',
    'app.build_vector_db',
    'dotenv',
    'multipart',
]

# 数据文件：前端构建产物 + ffmpeg/ffprobe 可执行文件
datas += [
    ('frontend/dist', 'frontend/dist'),
    ('ffmpeg/ffmpeg.exe', 'ffmpeg'),
    ('ffmpeg/ffprobe.exe', 'ffmpeg'),
]

a = Analysis(
    ['backend/app/main.py'],
    pathex=['backend'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'PIL',
        'matplotlib',
        'IPython',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ffmpeg-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 无控制台窗口
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='ffmpeg-agent',
)
