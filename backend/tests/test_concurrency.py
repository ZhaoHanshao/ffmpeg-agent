"""验证分片加锁与并发闸门：正确性（成功/失败判定）+ 并发行为。"""
import os
import sys
import time
import shutil
import threading

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


DL = tools.DOWNLOAD
os.makedirs(DL, exist_ok=True)
SCRATCH = os.path.join(DL, '_locktest')
shutil.rmtree(SCRATCH, ignore_errors=True)
os.makedirs(SCRATCH, exist_ok=True)

print('\n=== test_concurrency.py ===\n')

# ── 成功 / 失败判定（确认 returncode 分支真的生效） ──
print('--- execute_command 结果判定 ---')
ok_path = os.path.join(SCRATCH, 'ok.mp4').replace('\\', '/')
r_ok = tools.execute_command.invoke({
    'command': f'ffmpeg -f lavfi -i testsrc=duration=0.2:size=64x64:rate=5 -y {ok_path}'
})
check('成功命令 flag=True', r_ok.get('flag') is True, str(r_ok.get('command_result'))[:120])
check('成功命令产物已生成', os.path.isfile(os.path.join(SCRATCH, 'ok.mp4')))

bad_path = os.path.join(SCRATCH, 'bad.mp4').replace('\\', '/')
r_bad = tools.execute_command.invoke({
    'command': f'ffmpeg -i {SCRATCH.replace(chr(92), "/")}/__missing__.mp4 -y {bad_path}'
})
res_text = r_bad.get('command_result', '')
check('失败命令 flag 不为 True', r_bad.get('flag') is not True)
check('失败命令返回"执行失败"', '执行失败' in res_text, res_text[:160])
check('失败命令未被误报成功', '执行成功' not in res_text)


# ── KeyedLockPool 语义 ──
print('\n--- KeyedLockPool ---')
# 用生产条带数（64），并显式挑出落到不同条带的两个 key。
# 原因：锁池是固定条带分片，不同 key 有 1/条带数 的概率落到同一把锁，
# 那时本来就不该并发。条带数设小（如 8）+ 名字固定，会变成约 1/6 概率的假失败
# （实测复现过）。这里先算出各自条带、挑一组不冲突的，测试才稳定。
pool = tools.KeyedLockPool()


def stripe(key):
    return hash(key) % len(pool._locks)


_distinct = None
for i in range(200):
    cand = [f'unique-key-{i}', f'unique-key-{i + 1}']
    if stripe(cand[0]) != stripe(cand[1]):
        _distinct = cand
        break
assert _distinct, '找不到落在不同条带的 key（不应发生）'

events = []          # (阶段, tag, 时刻)
events_lock = threading.Lock()


def worker(tag, keys, hold):
    with pool.acquire(keys):
        with events_lock:
            events.append(('enter', tag, time.time()))
        time.sleep(hold)
        with events_lock:
            events.append(('exit', tag, time.time()))


def max_overlap(evts):
    """同时处于临界区的最大任务数。"""
    pts = sorted((t, 1 if kind == 'enter' else -1) for kind, _tag, t in evts)
    cur = peak = 0
    for _, delta in pts:
        cur += delta
        peak = max(peak, cur)
    return peak


# 不同 key 应能并发：判据是"临界区是否发生重叠"。
# 原先断言总耗时 < 1.1s，机器负载稍高就假失败（实测偶发 1.20s）。
print(f'  （并发用例使用不同条带的 key：{_distinct} -> '
      f'条带 {[stripe(k) for k in _distinct]}）')
ths = [threading.Thread(target=worker, args=(f'A{i}', [_distinct[i]], 0.6)) for i in range(2)]
for t in ths:
    t.start()
for t in ths:
    t.join()
check('不同 key 可并发（临界区发生重叠）', max_overlap(events) >= 2,
      f'峰值重叠={max_overlap(events)}')

# 相同 key 应互斥：临界区不得重叠
events.clear()
ths = [threading.Thread(target=worker, args=(f'S{i}', ['same-key'], 0.4)) for i in range(2)]
for t in ths:
    t.start()
for t in ths:
    t.join()
check('相同 key 互斥（临界区无重叠）', max_overlap(events) == 1,
      f'峰值重叠={max_overlap(events)}')
order = [(k, tag) for k, tag, _ in sorted(events, key=lambda e: e[2])]
check('相同 key 未重叠（enter/exit 成对出现）',
      order == [('enter', 'S0'), ('exit', 'S0'), ('enter', 'S1'), ('exit', 'S1')]
      or order == [('enter', 'S1'), ('exit', 'S1'), ('enter', 'S0'), ('exit', 'S0')],
      str(order))


# ── 多 key 加锁不死锁（顺序应被排序固定） ──
print('\n--- 多输出加锁 ---')
done = []


def multi_worker(tag, keys):
    with pool.acquire(keys):
        done.append(tag)


t1 = threading.Thread(target=multi_worker, args=('M1', ['k-b', 'k-a']))
t2 = threading.Thread(target=multi_worker, args=('M2', ['k-a', 'k-b']))
t1.start(); t2.start()
t1.join(timeout=5); t2.join(timeout=5)
check('相反顺序的多 key 请求未死锁', len(done) == 2, str(done))


# ── _output_lock_keys 归一化 ──
print('\n--- _output_lock_keys ---')
k1 = tools._output_lock_keys(['ffmpeg', '-i', 'in.mp4', 'backend/download/a.mp4'])
k2 = tools._output_lock_keys(['ffmpeg', '-i', 'in.mp4', './backend/download/a.mp4'])
check('同一输出的不同写法得到同一 key', k1 == k2 and len(k1) == 1, f'{k1} vs {k2}')
k3 = tools._output_lock_keys(['ffmpeg', '-i', 'in.mp4', '-f', 'null', '-'])
check('stdout 输出（-）不产生锁 key', k3 == [], str(k3))
k4 = tools._output_lock_keys(['ffmpeg', '-i', 'in.mp4', 'out1.mp4', '-map', '0:v', 'out2.mp4'])
check('多输出得到两个 key', len(k4) == 2, str(k4))


# ── 并发闸门：设为 1 时应串行 ──
print('\n--- _ffmpeg_slot 并发闸门 ---')
saved = tools.MAX_CONCURRENT_FFMPEG
tools._ffmpeg_slots = threading.Semaphore(1)
peak = {'cur': 0, 'max': 0}
lock = threading.Lock()


def slot_worker():
    with tools._ffmpeg_slot():
        with lock:
            peak['cur'] += 1
            peak['max'] = max(peak['max'], peak['cur'])
        time.sleep(0.3)
        with lock:
            peak['cur'] -= 1


ths = [threading.Thread(target=slot_worker) for _ in range(3)]
for t in ths:
    t.start()
for t in ths:
    t.join()
check('信号量为 1 时峰值并发为 1', peak['max'] == 1, f"peak={peak['max']}")

# stop_event 已置位时应立刻中断等待
ev = threading.Event()
ev.set()
try:
    with tools._ffmpeg_slot(ev):
        pass
    check('已置位 stop_event 时 _ffmpeg_slot 立即中断', False, '未抛异常')
except InterruptedError:
    check('已置位 stop_event 时 _ffmpeg_slot 立即中断', True)

tools._ffmpeg_slots = threading.Semaphore(saved)
shutil.rmtree(SCRATCH, ignore_errors=True)

print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
