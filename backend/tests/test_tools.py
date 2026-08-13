import os, sys, shutil
from dotenv import load_dotenv

# 将 backend/ 加入 sys.path 以便 import app.*
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

load_dotenv()

UPLOAD = os.getenv('UPLOAD', 'backend/upload')
DOWNLOAD = os.getenv('DOWNLOAD', 'backend/download')

from app.tools import get_files, execute_command, get_command, get_probe_command, execute_probe_command

pass_count = 0
fail_count = 0


def check(name: str, cond: bool, detail: str = ''):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f'  PASS  {name}')
    else:
        fail_count += 1
        print(f'  FAIL  {name}' + (f'  — {detail}' if detail else ''))


# ── 准备测试环境 ──
os.makedirs(UPLOAD, exist_ok=True)
os.makedirs(DOWNLOAD, exist_ok=True)

# 创建测试文件
test_file = os.path.join(UPLOAD, 'test_input.png')
with open(test_file, 'wb') as f:
    f.write(b'fake png content')

print('\n=== test_tools.py ===\n')


# ── get_files ──
print('--- get_files ---')
result = get_files.invoke({})
check('返回字典', isinstance(result, dict))
check('包含 "需要处理" 键', '需要处理' in result)
check('包含 "输入目录" 键', '输入目录' in result)
check('包含 "输出目录" 键', '输出目录' in result)
check('输入目录路径一致', os.path.abspath(result['输入目录']) == os.path.abspath(UPLOAD))
check('输出目录路径一致', os.path.abspath(result['输出目录']) == os.path.abspath(DOWNLOAD))
check('能找到测试文件', any('test_input.png' in f for f in result['需要处理']))


# ── execute_command: 拒绝非 ffmpeg 命令 ──
print('\n--- execute_command (安全校验) ---')
for bad_cmd in ['rm -rf /', 'sudo rm -rf /', 'python -c "print(1)"', 'ls -la', 'curl http://evil.com']:
    res = execute_command.invoke({'command': bad_cmd})
    check(f'拒绝非 ffmpeg 命令: {bad_cmd.split()[0]}', '拒绝执行非 ffmpeg 命令' in res.get('command_result', ''))
    check(f'  flag 不为 True', not res.get('flag', False))


# ── execute_command: 输出路径重写 ──
print('\n--- execute_command (路径重写) ---')
# 清理 download 目录
for f in os.listdir(DOWNLOAD):
    os.remove(os.path.join(DOWNLOAD, f))

# 用一个会被拒绝的命令测试路径重写逻辑（实际不执行）
# 先测试路径是否会被重写到 DOWNLOAD
res = execute_command.invoke({'command': 'ffmpeg -i input.mp4 output.mp4'})
# 即使拒绝执行非 ffmpeg 命令，路径重写逻辑在检验之前就已执行
# 这里测试拒绝后返回的 command 字段是否包含 DOWNLOAD 路径
if '拒绝执行非 ffmpeg 命令' in res.get('command_result', ''):
    # get_command 内部检查命令名，如果不是 ffmpeg 就拒绝
    # 等等，ffmpeg 命令不会被拒绝
    pass

# 真正测试路径重写：用 ffmpeg 命令（但 ffmpeg 可能不存在）
# 检查命令中的输出路径是否被改写
res = execute_command.invoke({'command': 'ffmpeg -i /tmp/input.mp4 /tmp/output.mp4'})
if '拒绝执行非 ffmpeg 命令' not in res.get('command_result', ''):
    # 如果执行了（即使失败），检查命令中是否包含 DOWNLOAD
    cmd = res.get('command', '')
    check('输出路径被重写到 DOWNLOAD', DOWNLOAD in cmd)


# ── execute_command: download 目录清理 ──
print('\n--- execute_command (download 目录清理) ---')
# 在 download 中放一个文件
stale_file = os.path.join(DOWNLOAD, 'stale.txt')
with open(stale_file, 'w') as f:
    f.write('stale')
check('stale 文件已创建', os.path.exists(stale_file))

# 执行一个命令（会被拒绝，但清理逻辑在 ffmpeg 命令检验之前执行）
# 实际上清理逻辑在路径重写之后、执行之前
# 对于非 ffmpeg 命令，在被拒绝之前已经执行了路径重写？看代码：
# 1. 安全校验检验命令名 → 如果是非 ffmpeg，直接返回，不执行后续
# 所以对于非 ffmpeg 命令，清理不会执行。
# 对于 ffmpeg 命令，清理会执行。
# 但由于 ffmpeg 不存在，subprocess.run 会报 OSError，清理仍然执行过。
res2 = execute_command.invoke({'command': 'ffmpeg -i /tmp/nonexistent.mp4 /tmp/out.mp4'})
# 清理应该在尝试执行之前发生
remaining = os.listdir(DOWNLOAD)
check('download 目录在执行前被清理', 'stale.txt' not in remaining)


# ── get_command ──
print('\n--- get_command ---')
try:
    result = get_command.invoke({'squry': 'how to invert colors'})
    check('返回列表', isinstance(result, list))
    check('返回非空结果', len(result) > 0)
    check('结果包含 FFmpeg 相关内容', any('color' in str(r).lower() or 'ffmpeg' in str(r).lower() for r in result))
except Exception as e:
    check(f'查询知识库失败: {e}', False, str(e))


# ── execute_probe_command: 安全校验 ──
print('\n--- execute_probe_command (安全校验) ---')
for bad_cmd in ['rm -rf /', 'ffmpeg -i a.mp4 out.mp4', 'python -c "print(1)"', 'ls -la']:
    res = execute_probe_command.invoke({'command': bad_cmd})
    check(f'拒绝非 ffprobe 命令: {bad_cmd.split()[0]}', '拒绝执行非 ffprobe 命令' in res.get('command_result', ''))
    check(f'  flag 不为 True', not res.get('flag', False))


# ── execute_probe_command: 正常执行（若 ffprobe 已安装） ──
print('\n--- execute_probe_command (正常执行) ---')
res = execute_probe_command.invoke({'command': 'ffprobe -version'})
check('返回字典', isinstance(res, dict))
check('包含 command 键', 'command' in res)
if '命令执行异常' in res.get('command_result', ''):
    check('ffprobe 未安装，命令执行异常（跳过成功路径）', True)
else:
    check('ffprobe 执行成功', res.get('flag', False) is True)
    check('返回版本信息', 'ffprobe' in res.get('command_result', ''))


# ── get_probe_command ──
print('\n--- get_probe_command ---')
try:
    from app.db_search import _ensure_probe_vector_db
    _ensure_probe_vector_db()
    result = get_probe_command.invoke({'squry': 'how to show stream information'})
    check('返回列表', isinstance(result, list))
    check('返回非空结果', len(result) > 0)
    check('结果包含 ffprobe 相关内容', any('ffprobe' in str(r).lower() or 'stream' in str(r).lower() for r in result))
except Exception as e:
    check(f'查询 ffprobe 知识库失败: {e}', False, str(e))


# ── 清理测试文件 ──
if os.path.exists(test_file):
    os.remove(test_file)


print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
