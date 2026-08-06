# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：ffmpeg-agent Windows 便携版（onedir, 无控制台）
# 构建前需先：npm run build（frontend/dist）、导出 ONNX 嵌入模型（backend/data/bge_onnx，由 build_exe.ps1 自动执行）
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
    'app.onnx_embed',
    'app.ffmpeg_download',
    'app.build_vector_db',
    'dotenv',
    'multipart',
]

# 数据文件：前端构建产物 + ONNX 嵌入模型 + 预构建向量库（运行时无 torch、不依赖 ffmpeg.org）
datas += [
    ('frontend/dist', 'frontend/dist'),
    ('backend/data/bge_onnx', 'backend/data/bge_onnx'),
    ('backend/data/chroma_db', 'backend/data/chroma_db'),
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
        # 运行时不再需要 torch/transformers/sentence_transformers/scipy/sklearn
        # （嵌入走 onnxruntime+tokenizers，ffmpeg 首跑下载）——排除避免误收集
        'torch',
        'transformers',
        'sentence_transformers',
        'scipy',
        'sklearn',
        'datasets',
        'accelerate',
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
