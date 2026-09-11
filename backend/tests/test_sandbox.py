"""命令沙箱与多步工作流的回归测试。

两块内容：
1) **安全边界**：越界读、网络协议必须始终被拒绝（改动校验逻辑后最容易回退的地方）
2) **多步/两遍工作流**：裸文件名作为输入（如上一步产物）必须能正确解析并与 ffmpeg
   自身的路径解析一致

背景：一次端到端评估发现 GIF 调色板两遍法的第二遍必然失败——校验按"文件是否存在于
UPLOAD/DOWNLOAD"放行了裸文件名，但 ffmpeg 是相对**进程工作目录**（仓库根）解析裸名的，
于是出现"校验通过、执行报 No such file"的割裂。
"""
import os
import shutil
import subprocess
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app import tools  # noqa: E402

DL = tools.DOWNLOAD
UL = tools.UPLOAD
SCRATCH = os.path.join(DL, '_sbox')

pass_count = 0
fail_count = 0


def check(name, cond, detail=''):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f'  PASS  {name}')
    else:
        fail_count += 1
        print(f'  FAIL  {name}' + (f'  — {detail}' if detail else ''))


print('\n=== test_sandbox.py ===\n')

os.makedirs(SCRATCH, exist_ok=True)
os.makedirs(UL, exist_ok=True)

# ── 安全边界：必须拒绝 ──
print('--- 安全边界（必须拒绝）---')
ATTACKS = [
    ('绝对路径越界（Windows 系统文件）', 'ffmpeg -i C:/Windows/win.ini out.png'),
    ('相对路径穿越', 'ffmpeg -i ../../../etc/passwd out.png'),
    ('项目内但不在允许目录', 'ffmpeg -i requirements.txt out.png'),
    ('SSRF http', 'ffmpeg -i http://evil.example/x.mp4 out.mp4'),
    ('SSRF https', 'ffmpeg -i https://evil.example/x.mp4 out.mp4'),
    ('file:// 越界', 'ffmpeg -i file:///C:/Windows/win.ini out.png'),
    ('裸名但文件不存在', 'ffmpeg -i definitely_missing_xyz.mp4 out.mp4'),
    ('子目录穿越到 upload 之外', 'ffmpeg -i backend/upload/../../requirements.txt out.png'),
]
for label, cmd in ATTACKS:
    res = tools.execute_command.invoke({'command': cmd})
    text = res.get('command_result') or ''
    check(f'拒绝：{label}', '拒绝执行' in text, text[:120])

# ffprobe 侧同样规则
print('\n--- ffprobe 侧同样拒绝 ---')
for label, cmd in (('绝对路径越界', 'ffprobe -v error C:/Windows/win.ini'),
                   ('SSRF', 'ffprobe -v error http://evil.example/x.mp4')):
    res = tools.execute_probe_command.invoke({'command': cmd})
    text = res.get('command_result') or ''
    check(f'拒绝：{label}', '拒绝执行' in text, text[:120])

# ── 合法用法：必须放行 ──
print('\n--- 合法用法（必须放行）---')
src = os.path.join(UL, '_sbox_src.png')
subprocess.run([tools.ffmpeg_bin('ffmpeg'), '-y', '-f', 'lavfi', '-i',
                'testsrc=duration=1:size=64x64:rate=1', '-frames:v', '1', src],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

rel = os.path.relpath(src).replace('\\', '/')
res = tools.execute_command.invoke({'command': f'ffmpeg -i {rel} -vf negate -f null -'})
check('放行：UPLOAD 内相对路径', '拒绝执行' not in (res.get('command_result') or ''))

res = tools.execute_command.invoke({'command': 'ffmpeg -f lavfi -i testsrc=duration=1 -f null -'})
check('放行：lavfi 虚拟源', '拒绝执行' not in (res.get('command_result') or ''))

# ── 多步工作流：裸文件名输入必须被解析成真实路径 ──
print('\n--- 多步工作流（裸文件名输入的解析）---')
res1 = tools.execute_command.invoke({
    'command': f'ffmpeg -i {rel} -vf "scale=64:-1,palettegen" -y _sbox_pal.png'
})
check('第 1 遍生成中间产物', res1.get('flag') is True,
      (res1.get('command_result') or '')[:120])
check('中间产物确实存在于 DOWNLOAD',
      os.path.isfile(os.path.join(DL, '_sbox_pal.png')))

# 第二遍用**裸文件名**引用上一步产物——这里曾必然失败
res2 = tools.execute_command.invoke({
    'command': f'ffmpeg -i {rel} -i _sbox_pal.png -lavfi "scale=64:-1 [x]; [x][1:v] paletteuse" '
               f'-y _sbox_out.gif'
})
check('第 2 遍（裸名输入）成功', res2.get('flag') is True,
      (res2.get('command_result') or '')[:160])
check('最终产物存在', os.path.isfile(os.path.join(DL, '_sbox_out.gif')))

# 命令里的裸名应被补全为真实路径
cmd2 = res2.get('command') or ''
check('命令中的裸名输入已补全为实际路径',
      '_sbox_pal.png' not in cmd2.split('-i')[1] or os.sep in cmd2,
      cmd2[:160])

# ── 中间产物识别：两遍法里"上一步产物"作为 -i 输入，不得被拒 ──
print('\n--- 中间产物识别（用真实文件构造）---')
from app.tools import _check_inputs, split_command  # noqa: E402

mid = os.path.join(DL, '_sbox_mid.png')
shutil.copyfile(os.path.join(UL, '_sbox_src.png') if os.path.isfile(os.path.join(UL, '_sbox_src.png'))
                else os.path.join(DL, '_sbox_pal.png'), mid)
mid_rel = os.path.relpath(mid).replace('\\', '/')
mid_name = os.path.basename(mid)

# 形式：`ffmpeg -i <已存在输入> -i <中间产物相对路径> -lavfi x <输出>`
parts = split_command(f'ffmpeg -i {mid_rel} -i {mid_rel} -lavfi x out_mid.gif')
check('相对路径的中间产物作为输入不被拒', _check_inputs(parts) == [], str(_check_inputs(parts)))

# 越界输入在任何形式下都必须被拒
parts2 = split_command('ffmpeg -i C:/Windows/win.ini out.png')
check('越界输入仍被拒', 'C:/Windows/win.ini' in _check_inputs(parts2), str(_check_inputs(parts2)))
check('其输出参数不被误判为输入', 'out.png' not in _check_inputs(parts2), str(_check_inputs(parts2)))

if os.path.isfile(mid):
    os.remove(mid)

# ── 清理 ──
for f in ('_sbox_pal.png', '_sbox_out.gif'):
    p = os.path.join(DL, f)
    if os.path.isfile(p):
        os.remove(p)
if os.path.isfile(src):
    os.remove(src)
shutil.rmtree(SCRATCH, ignore_errors=True)

print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
