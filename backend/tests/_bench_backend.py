"""后端耗时构成基准：用于判断性能优化该往哪里投。

同时回答一个常见疑问——"换成更快的 Web 框架（如 Elysia.js/其他）能提速吗？"
把各层耗时量出来，就能看出 Web 框架层占比是否值得动。

用法: .venv\\Scripts\\python.exe backend/tests/_bench_backend.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import app.db_search as ds  # noqa: E402
import app.tools as tools  # noqa: E402
from app.tools import (split_command, find_output_indexes, _check_inputs,  # noqa: E402
                       _output_lock_keys, _is_inside_download, _run_binary, ffmpeg_bin)

UL = tools.UPLOAD
DL = tools.DOWNLOAD
REPS = 8


def bench(label, fn, reps=REPS):
    for _ in range(2):
        fn()
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    ts.sort()
    med = ts[len(ts) // 2]
    print(f'  {label:<40} 中位 {med*1000:8.1f} ms')
    return med


print('\n=== 1) Web/框架层（不碰业务逻辑）===')
try:
    from fastapi.testclient import TestClient
    import app.main as m

    client = TestClient(m.app)
    client.get('/api/health')
    rows = []
    for _ in range(30):
        t0 = time.perf_counter()
        client.get('/api/health')
        rows.append(time.perf_counter() - t0)
    rows.sort()
    print(f'  {"GET /api/health（纯框架+路由）":<40} 中位 {rows[len(rows)//2]*1000:8.1f} ms')
    print('  → 这就是"换 Web 框架"最多能省下的量级')
except Exception as exc:  # noqa: BLE001
    print(f'  跳过：{type(exc).__name__}: {exc}')

print('\n=== 2) 本地检索（ONNX 嵌入 + Chroma）===')
ds.get_text('warmup')
bench('get_text（k=RETRIEVAL_K）', lambda: ds.get_text('how to invert colors of an image'))
bench('单次嵌入 embed_query', lambda: ds.get_embeddings().embed_query('invert colors'))

print('\n=== 3) ffmpeg 路径：工具层 vs 裸进程 ===')
ff = ffmpeg_bin('ffmpeg')
src = os.path.join(UL, '_bench_src.mp4')
subprocess.run([ff, '-y', '-f', 'lavfi', '-i', 'testsrc=duration=2:size=320x240:rate=15',
                '-c:v', 'libx264', '-preset', 'ultrafast', src],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
rel = os.path.relpath(src).replace('\\', '/')
out = os.path.join(DL, '_bench_out.mp4')
null_args = [ff, '-y', '-i', src, '-vf', 'scale=160:-2', '-f', 'null', '-']

bench('裸 subprocess.run（基准）', lambda: subprocess.run(
    null_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
bench('_run_binary（进程等待实现）', lambda: _run_binary(
    list(null_args), tools.EXEC_TIMEOUT, 'bench'))
print('  → 两者应接近；差得越多说明等待实现有额外延迟')

print('\n=== 4) 工具层各校验/解析步骤（都很小，确认不是瓶颈）===')
parts = split_command(f'ffmpeg -i {rel} -vf scale=160:-2 -y {out}')
bench('split_command', lambda: split_command(f'ffmpeg -i {rel} -y {out}'), reps=50)
bench('find_output_indexes', lambda: find_output_indexes(parts), reps=50)
bench('_check_inputs（安全校验）', lambda: _check_inputs(parts), reps=50)
bench('_output_lock_keys', lambda: _output_lock_keys(parts), reps=50)
bench('_is_inside_download', lambda: _is_inside_download(out), reps=50)
bench('execute_command（完整一次）', lambda: tools.execute_command.invoke(
    {'command': f'ffmpeg -i {rel} -vf scale=160:-2 -y {out}'}))

print('\n=== 5) 启动阶段 ===')
t0 = time.perf_counter()
import app.main  # noqa: F401,E402
print(f'  {"import app.main（已缓存时为 0）":<40} 中位 {(time.perf_counter()-t0)*1000:8.1f} ms')
print('  冷启动实测约 6.9s，其中 langchain/langgraph 导入占大头（见 _bench_startup.ps1）')

for f in (src, out):
    if os.path.isfile(f):
        os.remove(f)
