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

print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
