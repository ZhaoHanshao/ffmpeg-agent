import copy
import json
import logging
import os
import threading
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# LLM 配置的唯一来源：这个 JSON 文件（由页面右上角 ⚙️ 设置弹窗写入）。
# 刻意不再从环境变量（.env）读取 model / base_url / api_key：
# 单一来源可以避免"改了 .env 却仍用旧配置"这类问题——此前 .env 与设置文件
# 各存一套 LLM 配置，设置文件优先级更高，改了 .env 不生效，报错时也很难判断
# 实际用的是哪一套。
SETTINGS_FILE = os.getenv('SETTINGS_FILE', 'backend/data/llm_settings.json')

# 只有这些字段归设置文件管；SETTINGS_FILE 路径本身仍可用环境变量覆盖。
LLM_CONFIG_FIELDS = ('model', 'base_url', 'api_key', 'temperature', 'max_tokens')

_DEFAULT_CONFIG = {
    'model': None,
    'base_url': None,
    'api_key': None,
    'temperature': 0.2,
    'max_tokens': 1024,
    'streaming': True,
}

_model_config = dict(_DEFAULT_CONFIG)

# RLock:get_model_config 持锁期间还会调用 is_configured,可重入避免死锁
_config_lock = threading.RLock()
_model = None


def _load_settings_file():
    """启动时从磁盘恢复上次保存的配置（LLM 配置的唯一来源）。"""
    try:
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k in LLM_CONFIG_FIELDS:
                if k in data and data[k] is not None:
                    _model_config[k] = data[k]
            logger.info(f'已从 {SETTINGS_FILE} 恢复 LLM 配置')
        else:
            logger.info(f'未找到 {SETTINGS_FILE}，LLM 未配置（可在页面右上角 ⚙️ 中填写）')
    except (OSError, ValueError) as e:
        logger.warning(f'读取配置文件 {SETTINGS_FILE} 失败,使用默认配置: {e}')


def _save_settings_file(cfg: dict):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(SETTINGS_FILE)), exist_ok=True)
        payload = {k: cfg.get(k) for k in LLM_CONFIG_FIELDS}
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f'保存配置文件 {SETTINGS_FILE} 失败(配置仅本次运行有效): {e}')


def _validate(cfg: dict) -> dict:
    """校验配置值,非法时抛 ValueError。"""
    if cfg.get('model') is not None:
        model = str(cfg['model']).strip()
        if not model:
            raise ValueError('模型名称不能为空')
        cfg['model'] = model
    if cfg.get('base_url') is not None:
        url = str(cfg['base_url']).strip()
        if url and not (url.startswith('http://') or url.startswith('https://')):
            raise ValueError('接口地址必须以 http:// 或 https:// 开头')
        cfg['base_url'] = url
    if cfg.get('temperature') is not None:
        try:
            t = float(cfg['temperature'])
        except (TypeError, ValueError):
            raise ValueError('temperature 必须是数字')
        if not (0 <= t <= 2):
            raise ValueError('temperature 必须在 0~2 之间')
        cfg['temperature'] = t
    if cfg.get('max_tokens') is not None:
        try:
            n = int(cfg['max_tokens'])
        except (TypeError, ValueError):
            raise ValueError('max_tokens 必须是整数')
        if n < 1:
            raise ValueError('max_tokens 必须 ≥ 1')
        cfg['max_tokens'] = n
    return cfg


_load_settings_file()


def mask_key(key: str) -> str:
    """将 API Key 脱敏(只留首尾),避免接口返回明文。"""
    if not key:
        return ''
    if len(key) <= 8:
        return '****'
    return f'{key[:3]}****{key[-4:]}'


def is_configured():
    with _config_lock:
        return bool(_model_config.get('model') and _model_config.get('base_url') and _model_config.get('api_key'))


def _build_model_locked():
    """在持锁状态下按当前配置构建 ChatOpenAI(配置不完整时返回 None)。"""
    global _model
    _model = ChatOpenAI(**_model_config) if is_configured() else None
    return _model


# 启动即按磁盘/环境配置构建模型实例。
# 此前只有 update_model_config() 会赋值 _model，而启动路径 _load_settings_file()
# 只恢复 _model_config，导致重启后 is_configured() 为 True 但 get_model() 为 None →
# ensure_agents() 返回 False → 前端每次重启都提示"LLM 未配置"，必须重新点一次保存。
with _config_lock:
    _build_model_locked()


def get_model():
    """返回当前 ChatOpenAI 实例；若配置有效但尚未构建（例如刚被清空重建）则惰性构建。"""
    global _model
    with _config_lock:
        if _model is None and is_configured():
            _build_model_locked()
        return _model


def rebuild_agents():
    from app.agents import _build_agents, _build_probe_agents
    import app.agents as agents_mod
    if is_configured():
        agents_mod.agent_search, agents_mod.agent_execute, agents_mod.agent_chat = _build_agents()
        agents_mod.agent_probe_search, agents_mod.agent_probe_execute, agents_mod.agent_probe_chat = _build_probe_agents()
    else:
        agents_mod.agent_search = None
        agents_mod.agent_execute = None
        agents_mod.agent_chat = None
        agents_mod.agent_probe_search = None
        agents_mod.agent_probe_execute = None
        agents_mod.agent_probe_chat = None


def update_model_config(new_config: dict):
    global _model_config
    with _config_lock:
        cfg = copy.deepcopy(_model_config)
        for k in LLM_CONFIG_FIELDS:
            if k == 'api_key':
                continue  # 下面单独处理（脱敏占位符不能覆盖真实 key）
            if k in new_config and new_config[k] is not None:
                cfg[k] = new_config[k]
        # api_key 特殊处理：空值或脱敏占位符(前端回显)时保留原 key,防止被掩码覆盖
        if 'api_key' in new_config:
            incoming = (new_config['api_key'] or '').strip()
            if incoming and incoming != mask_key(cfg['api_key'] or '') and incoming != (cfg['api_key'] or ''):
                cfg['api_key'] = incoming
        cfg = _validate(cfg)
        _model_config = cfg
        _build_model_locked()
    # 锁外重建 agents(避免与 is_configured 死锁/长持锁)
    rebuild_agents()
    _save_settings_file(cfg)


def get_model_config() -> dict:
    with _config_lock:
        api_key = _model_config.get('api_key') or ''
        return {
            'model': _model_config.get('model'),
            'base_url': _model_config.get('base_url'),
            'api_key': mask_key(api_key),
            'key_configured': bool(api_key),
            'temperature': _model_config.get('temperature'),
            'max_tokens': _model_config.get('max_tokens'),
            'configured': is_configured(),
        }
