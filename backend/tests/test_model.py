import os, sys, shutil, tempfile

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv
load_dotenv()

# **必须在 import app.model 之前**把设置文件指向临时目录：
# 本测试会调用 update_model_config()，它真的会落盘。虽然测试结尾会把旧配置写回去，
# 但中途一旦抛异常/被 Ctrl-C，用户的真实配置（含 API Key）就被永久覆盖了。
# 同时预置一份完整配置，让断言不依赖用户机器上恰好配了什么。
_TMP_DIR = tempfile.mkdtemp(prefix='model-test-')
_SETTINGS = os.path.join(_TMP_DIR, 'llm_settings.json')
os.environ['SETTINGS_FILE'] = _SETTINGS
with open(_SETTINGS, 'w', encoding='utf-8') as _f:
    _f.write('{"model": "test-model", "base_url": "https://test.example/v1",'
             ' "api_key": "sk-test-key", "temperature": 0.2, "max_tokens": 1024}')

from app.model import (
    is_configured, get_model, get_model_config,
    update_model_config, rebuild_agents, _model_config, _model,
    LLM_CONFIG_FIELDS, SETTINGS_FILE,
)

assert SETTINGS_FILE == os.environ['SETTINGS_FILE'], '设置文件未被隔离，测试会污染真实配置'

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


# ── LLM 配置不再来自环境变量（唯一来源是设置文件） ──
print('\n--- LLM 配置来源（应为设置文件，非 .env）---')
# 这些字段由设置文件独占。曾经 .env 与设置文件各存一套且设置文件优先，
# 导致"改了 .env 不生效"且报错时无从判断实际用的是哪一套。
import inspect  # noqa: E402

_src = inspect.getsource(__import__('app.model', fromlist=['x']))
for _env in ('MODEL_NAME', 'BASE_URL', 'API_KEY', 'TEMPERATURE', 'MAX_TOKENS'):
    check(f'源码中不再读取环境变量 {_env}',
          f"getenv('{_env}'" not in _src and f'getenv("{_env}"' not in _src)

# 实际注入一组环境变量，确认当前生效配置不受影响
_env_before = dict(_model_config)
os.environ['MODEL_NAME'] = 'env-injected-should-be-ignored'
os.environ['BASE_URL'] = 'https://env-injected.invalid/v1'
os.environ['API_KEY'] = 'env-injected-key'
_cfg_after = get_model_config()
check('注入 .env 风格变量后 model 未变',
      _cfg_after.get('model') == _env_before.get('model'),
      f"{_cfg_after.get('model')!r} vs {_env_before.get('model')!r}")
check('注入 .env 风格变量后 base_url 未变',
      _cfg_after.get('base_url') == _env_before.get('base_url'))
for _v in ('MODEL_NAME', 'BASE_URL', 'API_KEY'):
    os.environ.pop(_v, None)

check('设置文件路径可配置', isinstance(SETTINGS_FILE, str) and SETTINGS_FILE.endswith('.json'),
      SETTINGS_FILE)


# ── get_model ──
print('\n--- get_model ---')
model = get_model()
if is_configured():
    check('已配置时 model 不为 None', model is not None)
    # 回归：配置来自磁盘/环境（未经 /api/settings/llm 保存）时也必须已构建，
    # 否则重启后 ensure_agents() 返回 False，前端一直提示"LLM 未配置"。
    check('配置有效时启动即构建模型（无需先调 PUT 设置）', model is not None)
else:
    check('未配置时 model 为 None', model is None)

# ensure_agents 应与 get_model 一致（配置有效 ⇒ agents 可用）
print('\n--- ensure_agents 与配置一致性 ---')
from app.agents import ensure_agents, ensure_probe_agents
if is_configured():
    check('已配置时 ensure_agents() 为 True', ensure_agents() is True)
    check('已配置时 ensure_probe_agents() 为 True', ensure_probe_agents() is True)
else:
    check('未配置时 ensure_agents() 为 False', ensure_agents() is False)
    check('未配置时 ensure_probe_agents() 为 False', ensure_probe_agents() is False)


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
shutil.rmtree(_TMP_DIR, ignore_errors=True)
print('\n--- 已恢复原始配置 ---')


print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
