import copy
from langchain_openai import ChatOpenAI

_model_config = {
    'model': None,
    'base_url': None,
    'api_key': None,
    'temperature': 0.2,
    'max_tokens': 2048,
    'streaming': True,
}

_model = None


def is_configured():
    return bool(_model_config.get('model') and _model_config.get('base_url') and _model_config.get('api_key'))


def get_model():
    return _model


def rebuild_agents():
    from app.agents import _build_agents
    import app.agents as agents_mod
    if is_configured():
        agents_mod.agent_search, agents_mod.agent_execute, agents_mod.agent_chat = _build_agents()
    else:
        agents_mod.agent_search = None
        agents_mod.agent_execute = None
        agents_mod.agent_chat = None


def update_model_config(new_config: dict):
    global _model_config, _model
    cfg = copy.deepcopy(_model_config)
    for k in ('model', 'base_url', 'api_key', 'temperature', 'max_tokens'):
        if k in new_config and new_config[k] is not None:
            cfg[k] = new_config[k]
    _model_config = cfg
    if is_configured():
        _model = ChatOpenAI(**cfg)
    else:
        _model = None
    rebuild_agents()


def get_model_config() -> dict:
    return {
        'model': _model_config.get('model'),
        'base_url': _model_config.get('base_url'),
        'api_key': _model_config.get('api_key'),
        'temperature': _model_config.get('temperature'),
        'max_tokens': _model_config.get('max_tokens'),
        'configured': is_configured(),
    }