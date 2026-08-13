import os, sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv
load_dotenv()

from app.model import (
    is_configured, get_model, get_model_config,
    update_model_config, rebuild_agents, _model_config, _model
)

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


print('\n=== test_model.py ===\n')


# ── get_model_config ──
print('--- get_model_config ---')
cfg = get_model_config()
check('返回字典', isinstance(cfg, dict))
check('包含 model 键', 'model' in cfg)
check('包含 base_url 键', 'base_url' in cfg)
check('包含 api_key 键', 'api_key' in cfg)
check('包含 temperature 键', 'temperature' in cfg)
check('包含 max_tokens 键', 'max_tokens' in cfg)
check('包含 configured 键', 'configured' in cfg)
check('temperature 为 0.2', cfg.get('temperature') == 0.2)
check('max_tokens 为 1024', cfg.get('max_tokens') == 1024)


# ── is_configured ──
print('\n--- is_configured ---')
# 取决于实际环境变量，但接口应该正常工作
check('is_configured 返回布尔值', isinstance(is_configured(), bool))


# ── get_model ──
print('\n--- get_model ---')
model = get_model()
if is_configured():
    check('已配置时 model 不为 None', model is not None)
else:
    check('未配置时 model 为 None', model is None)


# ── update_model_config 触发 rebuild ──
print('\n--- update_model_config ---')
old_cfg = dict(get_model_config())

# 保存原始 agent 状态
from app import agents as agents_mod
original_agents = (agents_mod.agent_search, agents_mod.agent_execute, agents_mod.agent_chat)

# 用当前值更新（不改变配置）
update_model_config({
    'model': old_cfg.get('model'),
    'base_url': old_cfg.get('base_url'),
    'api_key': old_cfg.get('api_key'),
})
check('update_model_config 不报错', True)

if is_configured():
    # 修改 temperature
    update_model_config({'temperature': 0.5})
    new_cfg = get_model_config()
    check('temperature 已更新为 0.5', new_cfg.get('temperature') == 0.5)
    # 改回
    update_model_config({'temperature': 0.2})

    # 配置不变时，agent 实例应已重建
    check('model 不为 None', get_model() is not None)
else:
    check('未配置，跳过模型更新测试', True)


# ── rebuild_agents ──
print('\n--- rebuild_agents ---')
try:
    rebuild_agents()
    check('rebuild_agents 不报错', True)
    from app import agents as agents_mod
    probe_state = (agents_mod.agent_probe_search, agents_mod.agent_probe_execute, agents_mod.agent_probe_chat)
    if is_configured():
        check('ffprobe agents 已重建（均不为 None）', all(a is not None for a in probe_state))
    else:
        check('未配置时 ffprobe agents 为 None', all(a is None for a in probe_state))
except Exception as e:
    check(f'rebuild_agents 异常: {e}', False, str(e))


# ── 恢复原始状态 ──
update_model_config(old_cfg)
print('\n--- 已恢复原始配置 ---')


print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
