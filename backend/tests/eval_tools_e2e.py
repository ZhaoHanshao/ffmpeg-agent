"""工具层端到端评估：用真实素材跑一批典型任务，找出实际失败点。

为什么不依赖 LLM：LLM key 可能失效，但**工具层是确定性的**，agent 最终会调用
`execute_command` / `execute_probe_command`，所以直接按 agent 会生成的形式调用这些
工具，就能在没有 LLM 的情况下测出真实成功率与失败模式。

用法: .venv\\Scripts\\python.exe backend/tests/eval_tools_e2e.py
"""
import os
import shutil
import subprocess
import sys
import time

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app import tools  # noqa: E402

DL = tools.DOWNLOAD
UL = tools.UPLOAD
SCRATCH = os.path.join(DL, '_eval')
os.makedirs(SCRATCH, exist_ok=True)

results = []


def run(name, command, expect_exists=None, expect_probe=None):
    """执行一条命令并记录结果。expect_exists: 期望生成的输出文件名。"""
    t0 = time.time()
    res = tools.execute_command.invoke({'command': command})
    dt = time.time() - t0
    ok = res.get('flag') is True
    detail = (res.get('command_result') or '')[:200].replace('\n', ' ')
    out_exists = None
    if expect_exists:
        out_exists = os.path.isfile(os.path.join(DL, expect_exists))
    results.append({
        'name': name, 'ok': ok, 'sec': dt, 'expect': expect_exists,
        'out_exists': out_exists, 'detail': detail,
    })
    mark = 'PASS' if ok else 'FAIL'
    extra = ''
    if expect_exists:
        extra = ' | 产物%s' % ('存在' if out_exists else '缺失')
    print(f'  {mark}  {name}  ({dt:.1f}s){extra}')
    if not ok:
        print(f'         {detail}')


def probe(name, command, expect_contains=None):
    res = tools.execute_probe_command.invoke({'command': command})
    text = res.get('command_result') or ''
    ok = res.get('flag') is True and (expect_contains is None or expect_contains in text)
    results.append({'name': name, 'ok': ok, 'sec': 0, 'expect': None,
                    'out_exists': None, 'detail': text[:200].replace('\n', ' ')})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f'         {text[:200]}')


print('\n=== 准备素材 ===')
src_video = os.path.join(UL, '_eval_src.mp4')
src_image = os.path.join(UL, '_eval_src.png')
src_audio = os.path.join(UL, '_eval_src.mp3')
ff = tools.ffmpeg_bin('ffmpeg')
for path, args in (
    (src_video, ['-f', 'lavfi', '-i', 'testsrc=duration=2:size=320x240:rate=15',
                 '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                 '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest']),
    (src_image, ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-frames:v', '1']),
    (src_audio, ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2']),
):
    if not os.path.isfile(path):
        subprocess.run([ff, '-y', *args, path], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
print('  素材:', {os.path.basename(p): os.path.isfile(p) for p in (src_video, src_image, src_audio)})

# 相对路径形式（与 get_files 返回给 agent 的一致）
V = os.path.relpath(src_video).replace('\\', '/')
I = os.path.relpath(src_image).replace('\\', '/')
A = os.path.relpath(src_audio).replace('\\', '/')

print('\n=== 典型任务（按 agent 会写出的形式）===')

# 1. 纯文件名输出（提示词要求的写法）
run('图片反色（裸输出名）',
    f'ffmpeg -i {I} -vf negate e_invert.png', expect_exists='e_invert.png')

# 2. 带下划线输出名
run('视频转 mp4（裸输出名）',
    f'ffmpeg -i {V} -c:v libx264 -preset ultrafast e_conv.mp4', expect_exists='e_conv.mp4')

# 3. 提取音频
run('提取音频为 mp3',
    f'ffmpeg -i {V} -vn -acodec libmp3lame e_audio.mp3', expect_exists='e_audio.mp3')

# 4. 裁剪（-ss/-t 不应被误判为输出）
run('裁剪前 1 秒',
    f'ffmpeg -i {V} -ss 0 -t 1 -c copy e_trim.mp4', expect_exists='e_trim.mp4')

# 5. 缩放（scale 含冒号与负值）
run('缩放到 160 宽',
    f'ffmpeg -i {V} -vf scale=160:-2 -c:a copy e_scale.mp4', expect_exists='e_scale.mp4')

# 6. 多输出
run('多输出（mp4 + gif）',
    f'ffmpeg -i {V} -c:v libx264 -preset ultrafast e_m1.mp4 -vf fps=5 e_m2.gif')

# 7. 输出文件名带空格
run('输出名含空格',
    f'ffmpeg -i {I} -vf negate "e_has space.png"', expect_exists='e_has space.png')

# 8. 输出名含中文
run('输出名含中文',
    f'ffmpeg -i {I} -vf negate e_反色图.png', expect_exists='e_反色图.png')

# 9. 输出名含括号（常见于"原文件名 (1).png"）
run('输出名含括号',
    f'ffmpeg -i {I} -vf negate "e_paren(1).png"', expect_exists='e_paren(1).png')

# 10. -map 多流
run('显式 -map',
    f'ffmpeg -i {V} -map 0:v -map 0:a? -c copy e_map.mp4', expect_exists='e_map.mp4')

# 11. 已存在输出（-y 覆盖）
run('覆盖已存在输出',
    f'ffmpeg -i {I} -vf negate e_invert.png', expect_exists='e_invert.png')

# 12. 静态图转视频
run('单图转 1 秒视频',
    f'ffmpeg -loop 1 -i {I} -t 1 -pix_fmt yuv420p -vf scale=160:-2 e_fromimg.mp4',
    expect_exists='e_fromimg.mp4')

# 13. lavfi 虚拟输入
run('lavfi 生成彩条',
    'ffmpeg -f lavfi -i smptebars=duration=1:size=160x120 -c:v libx264 -preset ultrafast e_bars.mp4',
    expect_exists='e_bars.mp4')

# 14. GIF 调色板两遍法（真实工作流是两次 ffmpeg 调用：
#     第一遍生成调色板 PNG，第二遍用它生成 GIF —— 第二遍的输入正是第一遍的产物）
print('  [GIF 两遍法] 第 1 遍：生成调色板')
run('GIF 调色板第 1 遍（生成 palette.png）',
    f'ffmpeg -i {V} -vf "fps=5,scale=160:-1:flags=lanczos,palettegen" -y e_pal.png',
    expect_exists='e_pal.png')
print('  [GIF 两遍法] 第 2 遍：用中间产物作为输入（关键：裸文件名输入）')
run('GIF 调色板第 2 遍（输入=上一步产物）',
    f'ffmpeg -i {V} -i e_pal.png -lavfi "fps=5,scale=160:-1:flags=lanczos [x]; [x][1:v] paletteuse" '
    f'-y e_pal.gif',
    expect_exists='e_pal.gif')

# 15. 两遍编码风格的日志输出（-pass）
run('两遍编码 pass1（输出到 null）',
    f'ffmpeg -i {V} -c:v libx264 -preset ultrafast -pass 1 -f null -')

print('\n=== ffprobe 任务 ===')
probe('查看分辨率', f'ffprobe -v error -select_streams v:0 -show_entries stream=width,height '
                   f'-of default=noprint_wrappers=1 {V}', expect_contains='width=')
probe('查看编码', f'ffprobe -v error -show_entries stream=codec_name -of default=noprint_wrappers=1 {V}',
      expect_contains='codec_name=')
probe('查看时长（format）', f'ffprobe -v error -show_entries format=duration '
                          f'-of default=noprint_wrappers=1 {V}', expect_contains='duration=')
probe('JSON 输出', f'ffprobe -v error -show_streams -of json {V}', expect_contains='"codec_type"')

print('\n=== 汇总 ===')
passed = sum(1 for r in results if r['ok'])
total = len(results)
print(f'  通过 {passed}/{total}')
failed = [r for r in results if not r['ok']]
if failed:
    print('\n  失败项：')
    for r in failed:
        print(f"    - {r['name']}: {r['detail'][:140]}")
missing = [r for r in results if r.get('expect') and r.get('out_exists') is False]
if missing:
    print('\n  命令成功但产物缺失：')
    for r in missing:
        print(f"    - {r['name']}: 期望 {r['expect']}")

# 清理
shutil.rmtree(SCRATCH, ignore_errors=True)
for f in os.listdir(DL):
    if f.startswith('e_'):
        try:
            os.remove(os.path.join(DL, f))
        except OSError:
            pass
for p in (src_video, src_image, src_audio):
    if os.path.isfile(p):
        os.remove(p)

sys.exit(0 if not failed and not missing else 1)
