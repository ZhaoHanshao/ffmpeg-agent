import copy
from langchain_openai import ChatOpenAI

_model_config = {
    'model': None,
    'base_url': None,
    'api_key': None,
    'temperature': 0.2,
    'max_tokens': 1024,
    'streaming': True,
}

_model = None


def mask_key(key: str) -> str:
    """将 API Key 脱敏(只留首尾),避免接口返回明文。"""
    if not key:
        return ''
    if len(key) <= 8:
        return '****'
    return f'{key[:3]}****{key[-4:]}'


def is_configured():
    return bool(_model_config.get('model') and _model_config.get('base_url') and _model_config.get('api_key'))


def get_model():
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
    global _model_config, _model
    cfg = copy.deepcopy(_model_config)
    for k in ('model', 'base_url', 'temperature', 'max_tokens'):
        if k in new_config and new_config[k] is not None:
            cfg[k] = new_config[k]
    # api_key 特殊处理：空值或脱敏占位符(前端回显)时保留原 key,防止被掩码覆盖
    if 'api_key' in new_config:
        incoming = (new_config['api_key'] or '').strip()
        if incoming and incoming != mask_key(cfg['api_key'] or '') and incoming != (cfg['api_key'] or ''):
            cfg['api_key'] = incoming
    _model_config = cfg
    if is_configured():
        _model = ChatOpenAI(**cfg)
    else:
        _model = None
    rebuild_agents()


def get_model_config() -> dict:
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