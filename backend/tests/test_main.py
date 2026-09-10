"""main.py 的回归测试：文件列表排序、任务归属、SSE 前置校验、设置接口。

沿用项目既有的 check(name, cond, detail) 风格，无测试框架依赖。
"""
import os
import sys
import shutil
import time
import asyncio

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv
load_dotenv()

import app.main as m

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


print('\n=== test_main.py ===\n')


# ── _list_dir_newest_first ──
print('--- _list_dir_newest_first ---')
tmp = os.path.join(m.DOWNLOAD_DIR, '_test_list')
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
try:
    # 按 c -> a -> b 的顺序创建，间隔确保 mtime 可区分
    for n in ('c.mp4', 'a.mp4', 'b.mp4'):
        with open(os.path.join(tmp, n), 'wb') as f:
            f.write(b'x')
        time.sleep(1.05)
    got = m._list_dir_newest_first(tmp)
    check('按修改时间倒序（最新在前）', got == ['b.mp4', 'a.mp4', 'c.mp4'], f'实际 {got}')

    # 子目录应被排除
    os.makedirs(os.path.join(tmp, 'subdir'), exist_ok=True)
    check('排除子目录', 'subdir' not in m._list_dir_newest_first(tmp))

    check('不存在的目录返回空列表', m._list_dir_newest_first(os.path.join(tmp, 'nope')) == [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ── _safe_path 防路径穿越 ──
print('\n--- _safe_path ---')
for bad in ('../etc/passwd', '', 'a\x00b', 'C:/x/y.mp4'):
    check(f'拒绝越界输入 {bad!r}', m._safe_path(m.DOWNLOAD_DIR, bad) is None)
check('放行正常文件名', m._safe_path(m.DOWNLOAD_DIR, 'ok.mp4') is not None)
check('放行目录内相对路径', m._safe_path(m.DOWNLOAD_DIR, 'sub/ok.mp4') is not None)


# ── 任务注册 / 归属校验 / TTL 清理 ──
print('\n--- 任务管理 ---')
jid, job = m._register_job('')
check('任务含 owner/created_at/kind 字段',
      all(k in job for k in ('owner', 'created_at', 'kind')))
check('默认归属凭证为 job_id 本身', job['owner'] == jid)
check('错误 owner 无法停止', m._stop_job(jid, 'wrong') is False)
check('正确 owner 可停止', m._stop_job(jid, jid) is True)
check('停止后事件已置位', job['stop'].is_set())
m._unregister_job(jid)
check('注销后无法再停止', m._stop_job(jid, jid) is False)

old_id, old_job = m._register_job('')
old_job['created_at'] = time.time() - (m.JOB_TTL_SECONDS + 10)
new_id, _ = m._register_job('ffprobe ')
check('注册时清理超 TTL 的过期记录', old_id not in m._jobs)
check('新记录保留且 kind 正确', m._jobs.get(new_id, {}).get('kind') == 'ffprobe ')
m._unregister_job(new_id)


# ── SSE 前置校验（初始化中 / LLM 未配置） ──
print('\n--- chat 路由前置校验 ---')


async def _route_checks():
    saved_status = m._init_state['status']
    m._init_state['status'] = 'running'
    r = await m.chat('q', [], [])
    ok_init = b'error' in r.body and b'done' in r.body
    m._init_state['status'] = 'ok'

    import app.model as model
    with model._config_lock:
        saved_key = model._model_config.get('api_key')
        model._model_config['api_key'] = None
    r2 = await m.chat('q', [], [])
    r3 = await m.probe_chat('q', [], [])
    with model._config_lock:
        model._model_config['api_key'] = saved_key
    model._build_model_locked()
    m._init_state['status'] = saved_status
    return ok_init, r2, r3


ok_init, r2, r3 = asyncio.run(_route_checks())
check('初始化中返回 SSE error+done', ok_init)
check('ffmpeg 路由未配置 LLM 时返回 SSE error', b'error' in r2.body)
check('ffprobe 路由未配置 LLM 时返回 SSE error', b'error' in r3.body)


# ── 设置接口不再返回陈旧缓存 ──
print('\n--- settings 接口 ---')
cfg = asyncio.run(m.get_llm_settings())
check('返回 configured 字段', 'configured' in cfg)
check('api_key 已脱敏（不含 **** 之外的原文）',
      ('****' in cfg.get('api_key', '')) or cfg.get('api_key', '') == '')
check('调用方拿到的是新 dict（非共享缓存）',
      asyncio.run(m.get_llm_settings()) is not cfg)
check('无 _settings_store 残留（旧实现的陈旧缓存已移除）', not hasattr(m, '_settings_store'))


print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
