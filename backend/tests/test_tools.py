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

# 真正测试路径重写：用 ffmpeg 命令（但 ffmpeg 可能不存在）
# 检查命令中的输出路径是否被改写
res = execute_command.invoke({'command': 'ffmpeg -i /tmp/input.mp4 /tmp/output.mp4'})
if '拒绝执行非 ffmpeg 命令' not in res.get('command_result', ''):
    # 如果执行了（即使失败），检查命令中是否包含 DOWNLOAD
    cmd = res.get('command', '')
    check('输出路径被重写到 DOWNLOAD', DOWNLOAD in cmd)


# ── execute_command: 输出路径包含判断（路径语义，非子串匹配） ──
print('\n--- execute_command (输出路径包含判断) ---')
from app.tools import _is_inside_download

check('裸文件名需要重写', _is_inside_download('output.mp4') is False)
check('DOWNLOAD 下绝对/相对路径不再重写',
      _is_inside_download(os.path.join(DOWNLOAD, 'out.mp4')) is True)
check('子目录中的输出不再重写',
      _is_inside_download(os.path.join(DOWNLOAD, 'sub', 'out.mp4')) is True)
check('stdout 输出（-）不参与重写', _is_inside_download('-') is False)
# 旧实现用子串匹配，`upload/download_x/out.mp4` 会被误判为"已在 DOWNLOAD 内"
check('同名子串路径不被误判（旧子串匹配的缺陷）',
      _is_inside_download(os.path.join('upload', 'download_x', 'out.mp4')) is False)


# ── execute_command: 不再清空 download 目录 ──
print('\n--- execute_command (download 目录保留) ---')
# 在 download 中放一个文件，执行命令后它应当仍然存在：
# 现在的策略是注入 -y 覆盖同名输出，而不是每次清空下载目录。
stale_file = os.path.join(DOWNLOAD, 'stale.txt')
with open(stale_file, 'w') as f:
    f.write('stale')
check('stale 文件已创建', os.path.exists(stale_file))

execute_command.invoke({'command': 'ffmpeg -i /tmp/nonexistent.mp4 /tmp/out.mp4'})
check('既有输出文件不会被清空（改为 -y 覆盖策略）', os.path.exists(stale_file))
os.remove(stale_file)


# ── get_command ──
print('\n--- get_command ---')
try:
    result = get_command.invoke({'squry': 'how to invert colors'})
    check('返回列表', isinstance(result, list))
    check('返回非空结果', len(result) > 0)
    check('结果包含 FFmpeg 相关内容', any('color' in str(r).lower() or 'ffmpeg' in str(r).lower() for r in result))
except Exception as e:
    check(f'查询知识库失败: {e}', False, str(e))


# ── 检索条数受 RETRIEVAL_K 约束（控制喂给 LLM 的提示词长度） ──
print('\n--- RETRIEVAL_K ---')
from app import db_search as _ds
check('RETRIEVAL_K 可配置且默认收敛到 8', _ds.RETRIEVAL_K == 8, f'实际 {_ds.RETRIEVAL_K}')
try:
    _r = _ds.get_text('how to invert colors')
    _n = len(_r) if isinstance(_r, list) else -1
    check(f'get_text 返回条数 <= RETRIEVAL_K（实际 {_n}）', 0 < _n <= _ds.RETRIEVAL_K)
    _total = sum(len(d.get('content', '')) for d in _r) if isinstance(_r, list) else 0
    check('检索载荷已收敛（< 12000 字符，避免提示词过长）', _total < 12000, f'实际 {_total}')
except Exception as e:
    check(f'RETRIEVAL_K 校验失败: {e}', False, str(e))

# ffprobe 侧同样走共用检索实现
try:
    from app.db_search import get_probe_text
    _rp = get_probe_text('show stream information')
    _np = len(_rp) if isinstance(_rp, list) else -1
    check(f'get_probe_text 返回条数 <= RETRIEVAL_K（实际 {_np}）', 0 < _np <= _ds.RETRIEVAL_K)
except Exception as e:
    check(f'ffprobe 检索条数校验失败: {e}', False, str(e))


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
