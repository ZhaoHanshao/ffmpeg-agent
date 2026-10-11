"""验证 _run_binary 与 execute_probe_command 在"有 stdout 但退出码非 0"时的行为。

真实 ffprobe 很难稳定复现该场景，这里直接用能产生 stdout 且返回非 0 的命令
(优先 python，退化到 cmd)来验证错误信息保留了部分结果。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app import tools  # noqa: E402

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


print('\n=== test_probe_output.py ===\n')

# 找一个"能同时输出 stdout 并以非 0 退出"的解释器
python = sys.executable
parts = [python, '-c', "import sys; print('PARTIAL_RESULT_LINE'); sys.stderr.write('boom\\n'); sys.exit(3)"]

print('--- _run_binary 返回三元组 ---')
rc, out, err = tools._run_binary(parts, 30, 'test')
check('退出码为 3', rc == 3, f'实际 {rc}')
check('stdout 被捕获', 'PARTIAL_RESULT_LINE' in out, repr(out[:80]))
check('stderr 被捕获', 'boom' in err, repr(err[:80]))

print('\n--- 失败信息保留部分 stdout ---')
# 直接复刻 execute_probe_command 的失败分支逻辑
output = out.strip()
detail = output or err[-2000:]
if output and err.strip():
    detail = f'{output}\n[stderr] {err[-1000:]}'
check('失败详情包含 stdout 内容', 'PARTIAL_RESULT_LINE' in detail)
check('失败详情同时保留 stderr', 'boom' in detail)

print('\n--- 超时会抛 TimeoutError 并杀掉进程 ---')
try:
    tools._run_binary([python, '-c', 'import time; time.sleep(30)'], 1, 'sleepy')
    check('超时应抛 TimeoutError', False, '未抛异常')
except TimeoutError as e:
    check('超时抛 TimeoutError', True)
    check('异常信息含标签', 'sleepy' in str(e), str(e)[:80])
except Exception as e:  # noqa: BLE001
    check('超时抛 TimeoutError', False, f'抛了 {type(e).__name__}: {e}')

print('\n--- stop_event 触发 InterruptedError ---')
import threading  # noqa: E402

ev = threading.Event()
ev.set()
try:
    tools._run_binary([python, '-c', 'import time; time.sleep(30)'], 30, 'stopped', stop_event=ev)
    check('已置位的 stop_event 应中断执行', False, '未抛异常')
except InterruptedError:
    check('已置位的 stop_event 立即中断', True)
except Exception as e:  # noqa: BLE001
    check('已置位的 stop_event 立即中断', False, f'抛了 {type(e).__name__}: {e}')

print('\n--- 子进程不弹控制台窗口 ---')
# 打包版是 windowed（无控制台）的 exe，ffmpeg/ffprobe 是控制台程序：
# 不传 CREATE_NO_WINDOW 时 Windows 会**新建**一个黑窗口，每执行一条命令闪一次。
# 这里拦下 Popen 看真实传了什么参数（比读源码断言可靠）。
import subprocess as _sp  # noqa: E402

_recorded = []
_real_popen = _sp.Popen


class _RecordingPopen(_real_popen):
    def __init__(self, *a, **kw):
        _recorded.append(kw)
        super().__init__(*a, **kw)


_sp.Popen = _RecordingPopen
try:
    tools._run_binary([python, '-c', 'print(1)'], 30, 'silent')
    # 时长探测是另一条独立的 spawn 路径，单独走一遍
    tools._probe_duration(os.path.abspath(__file__))
finally:
    _sp.Popen = _real_popen

check('_run_binary 与 _probe_duration 都真的起了子进程', len(_recorded) == 2, f'{len(_recorded)} 次')
if os.name == 'nt':
    for _i, _kw in enumerate(_recorded):
        _flags = _kw.get('creationflags') or 0
        _si = _kw.get('startupinfo')
        check(f'第 {_i + 1} 个 spawn 带 CREATE_NO_WINDOW',
              bool(_flags & _sp.CREATE_NO_WINDOW), f'creationflags={_flags}')
        check(f'第 {_i + 1} 个 spawn 请求隐藏窗口（SW_HIDE）',
              _si is not None and bool(_si.dwFlags & _sp.STARTF_USESHOWWINDOW)
              and _si.wShowWindow == _sp.SW_HIDE,
              f'startupinfo={_si!r}')
else:
    check('非 Windows 不传 creationflags',
          all(not k.get('creationflags') for k in _recorded), '')

print('\n--- 静态检查：新增的 spawn 点不能忘了静默参数 ---')
# 上面两条只钉住了现有的两个调用点。以后谁再加一处 subprocess.Popen/run
# 而忘了 silent_spawn_kwargs()，黑窗口就又回来了——所以扫一遍源码兜住。
import re  # noqa: E402

_app_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app')
_missing = []
for _fn in sorted(os.listdir(_app_dir)):
    if not _fn.endswith('.py'):
        continue
    _src = open(os.path.join(_app_dir, _fn), encoding='utf-8').read()
    for _m in re.finditer(r'subprocess\.(?:Popen|run)\(', _src):
        _depth = 0
        _start = _m.end() - 1
        _call = ''
        for _i in range(_start, len(_src)):
            if _src[_i] == '(':
                _depth += 1
            elif _src[_i] == ')':
                _depth -= 1
                if _depth == 0:
                    _call = _src[_start:_i + 1]
                    break
        if 'silent_spawn_kwargs' not in _call:
            _missing.append(f'{_fn}:{_src[:_m.start()].count(chr(10)) + 1}')

check('backend/app 里每个 subprocess.Popen/run 都带了 silent_spawn_kwargs',
      not _missing, '漏掉的：' + (', '.join(_missing) or '(无)'))

print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
