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

# ── 两个角色，刻意分开 ──
#
# text  ：图谱里的 search / execute / chat agent —— 只处理文字（写命令、写回答）
# vision：素材画面/波形理解 —— 只处理图像
#
# 为什么不合成"一个模型兼顾"：
#   - 文本模型常常不收图像，喂图会直接报错或静默降级，画面信息全丢；
#   - 视觉模型未必更会写 ffmpeg 命令，让它在整条链路上跑会拉低命令质量、也更慢更贵。
# 所以两个角色的配置各自独立解析，**图像永远只送给 vision，命令永远只送给 text**。
TEXT_ROLE = 'text'
VISION_ROLE = 'vision'

_DEFAULT_CONFIG = {
    'model': None,
    'base_url': None,
    'api_key': None,
    'temperature': 0.2,
    'max_tokens': 1024,
    'streaming': True,
}

_model_config = dict(_DEFAULT_CONFIG)

# 视觉角色的**覆盖层**：只存用户显式填写的字段，其余在解析时从主模型继承
# （同一个服务商换个模型是最常见的用法，不该逼用户把 base_url/key 再抄一遍）。
# None 表示没启用独立视觉模型 → 图像分析回退到主模型。
_vision_config = None

# RLock:get_model_config 持锁期间还会调用 is_configured,可重入避免死锁
_config_lock = threading.RLock()
_model = None


def _load_settings_file():
    """启动时从磁盘恢复上次保存的配置（LLM 配置的唯一来源）。"""
    global _vision_config
    try:
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k in LLM_CONFIG_FIELDS:
                if k in data and data[k] is not None:
                    _model_config[k] = data[k]
            v = data.get('vision')
            if isinstance(v, dict):
                kept = {k: v[k] for k in LLM_CONFIG_FIELDS
                        if v.get(k) is not None and not (isinstance(v[k], str) and not v[k].strip())}
                _vision_config = kept or None
            logger.info(f'已从 {SETTINGS_FILE} 恢复 LLM 配置'
                        f'（视觉模型：{"已单独配置" if _vision_config else "跟随主模型"}）')
        else:
            logger.info(f'未找到 {SETTINGS_FILE}，LLM 未配置（可在页面右上角 ⚙️ 中填写）')
    except (OSError, ValueError) as e:
        logger.warning(f'读取配置文件 {SETTINGS_FILE} 失败,使用默认配置: {e}')


def _save_settings_file(cfg: dict, vision: dict = None):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(SETTINGS_FILE)), exist_ok=True)
        payload = {k: cfg.get(k) for k in LLM_CONFIG_FIELDS}
        # vision 为 None 时显式写 null：这是"回退主模型"的标记，
        # 不能省略这个键，否则旧文件里的视觉配置会被静默保留。
        payload['vision'] = ({k: vision.get(k) for k in LLM_CONFIG_FIELDS}
                             if vision else None)
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


def is_masked_key(value: str) -> bool:
    """判断一个值是不是脱敏回显（`sk-****abcd`）——这种值不能当成真实 key 使用。"""
    return '****' in str(value or '')


def _same_base_url(a: str, b: str) -> bool:
    """比较两个 base_url 是否指向同一个地方（忽略末尾斜杠与大小写）。"""
    return (a or '').strip().rstrip('/').lower() == (b or '').strip().rstrip('/').lower()


def stored_key_for(role: str, base_url: str) -> str:
    """取服务端已存的、**与该 base_url 匹配的**真实 key；不匹配则返回空串。

    这个匹配校验是必要的：拉模型列表时若把已存的 key 发到用户随手填的另一个地址上，
    就等于把密钥泄露给了第三方。
    """
    with _config_lock:
        text = copy.deepcopy(_model_config)
        vision_layer = copy.deepcopy(_vision_config)
    if role == VISION_ROLE and vision_layer:
        try:
            cfg = _apply_override(text, vision_layer)
        except ValueError:
            cfg = text
    else:
        cfg = text
    if not _same_base_url(cfg.get('base_url'), base_url):
        return ''
    return cfg.get('api_key') or ''


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
    global _model_config, _vision_config
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

        # 视觉层：只有显式传了 'vision' 才动它。三种写法语义不同，刻意区分开：
        #   {'vision': null} / {} / 全空值        → 清除覆盖，画面分析回到跟随主模型
        #   {'vision': {'temperature': 0.9}}      → **没提模型**：增量改，保住已存的模型名
        #   {'vision': {'model': '   ', ...}}     → **明确把模型清空了**：同样清除覆盖
        # 区分"没提"和"清空"是必要的：前者是只想调个参数，后者是用户真的要把
        # 独立视觉模型关掉；把后者当成前者会让界面上的清空操作"自己变回去"。
        if 'vision' in new_config:
            v = new_config.get('vision')
            if not isinstance(v, dict):
                _vision_config = None
            else:
                kept = {}
                for k in LLM_CONFIG_FIELDS:
                    val = v.get(k)
                    if val is None:
                        continue
                    if isinstance(val, str) and not val.strip():
                        continue
                    kept[k] = val
                model_cleared = ('model' in v) and not str(v.get('model') or '').strip()
                if model_cleared or (not kept and 'model' not in v):
                    _vision_config = None
                else:
                    # 没提 model → 在旧层上增量；提了非空 model → 整体替换
                    base = copy.deepcopy(_vision_config) if ('model' not in v and _vision_config) else {}
                    merged_layer = {**base, **kept}
                    if merged_layer.get('model'):
                        _validate({**cfg, **merged_layer})  # 逐字段校验（越界/非法 URL）
                        _vision_config = merged_layer
                    else:
                        # 并完之后仍然没有模型名 → 这个覆盖层没有意义，按未分离处理，
                        # 避免出现"标记为已分离、实际还在用主模型"的错位状态
                        _vision_config = None
        _build_model_locked()
    # 锁外重建 agents(避免与 is_configured 死锁/长持锁)
    rebuild_agents()
    _save_settings_file(cfg, _vision_config)


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


# ── 按配置构建（多对话：每个对话可以覆盖全局配置）──
#
# 全局配置仍是默认值；对话级覆盖只给"想改的那几个字段"，其余字段继承全局。
# 因此下面所有函数都只接受/返回**完整配置**，合并逻辑集中在 merged_config()，
# 避免各处各写一遍"空值算继承吗"的判断。

def get_raw_config() -> dict:
    """全局配置（含真实 api_key）。仅供服务端内部使用，不要直接返回给前端。"""
    with _config_lock:
        return copy.deepcopy(_model_config)


def is_config_configured(cfg: dict) -> bool:
    """判断一份**完整配置**是否可用（model / base_url / api_key 三者齐全）。"""
    cfg = cfg or {}
    return bool(cfg.get('model') and cfg.get('base_url') and cfg.get('api_key'))


def mask_config(cfg: dict) -> dict:
    """脱敏后的配置视图（api_key 只留首尾），用于接口返回。"""
    cfg = cfg or {}
    api_key = cfg.get('api_key') or ''
    return {
        'model': cfg.get('model') or '',
        'base_url': cfg.get('base_url') or '',
        'api_key': mask_key(api_key),
        'key_configured': bool(api_key),
        'temperature': cfg.get('temperature'),
        'max_tokens': cfg.get('max_tokens'),
        'configured': is_config_configured(cfg),
    }


def merged_config(override) -> dict:
    """把对话级覆盖合并到全局主模型配置上，返回一份校验过的完整配置。

    override 为 None/空 → 直接就是全局配置（"继承"）。
    api_key 的语义与 update_model_config 一致：留空或回显脱敏值时视为继承全局，
    这样前端把 GET 到的脱敏值原样 PATCH 回来不会把真 key 抹掉。
    """
    with _config_lock:
        base = copy.deepcopy(_model_config)
    if not isinstance(override, dict):
        return base
    return _apply_override(base, override)


def _apply_override(base: dict, override: dict) -> dict:
    """把 override 的非空字段盖到 base 上并校验。

    "空值算继承"的规则集中在这里：对话级覆盖和视觉覆盖用的是同一套语义。
    """
    cfg = dict(base)
    for k in LLM_CONFIG_FIELDS:
        if k == 'api_key':
            continue  # 下面单独处理（脱敏占位符不能覆盖真实 key）
        v = override.get(k)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        cfg[k] = v
    if 'api_key' in override:
        incoming = str(override.get('api_key') or '').strip()
        if incoming and incoming != mask_key(base.get('api_key') or '') and incoming != (base.get('api_key') or ''):
            cfg['api_key'] = incoming
    return _validate(cfg)


def vision_separate() -> bool:
    """是否单独配置了视觉模型（false = 画面分析回退主模型）。"""
    with _config_lock:
        return _vision_config is not None


def merged_vision_config(override=None):
    """解析**图像分析**要用的完整配置。返回 (config, used_dedicated)。

    单独配了视觉模型时，以**全局主模型配置**为底、盖上视觉覆盖层：
    - 用主模型做底是为了让"同一个服务商换个模型"只填一个模型名，base_url/key 自动继承；
    - 底必须是**全局**配置而不是对话级有效配置 —— 对话级覆盖改的是"这个对话用哪个文本模型、
      什么温度"，那是文本角色的职责，**不能渗进视觉角色**，否则一次对话级调温就会
      悄悄改掉画面分析的采样参数，正是要避免的"混用"。

    没单独配视觉模型时，直接回退主模型的有效配置（used_dedicated=False）——
    这时两个角色本来就是同一个模型，由 media.py 的能力探测决定要不要跳过图片。
    """
    conv_vision = None
    if isinstance(override, dict):
        v = override.get('vision')
        if isinstance(v, dict):
            conv_vision = v
    with _config_lock:
        vision_layer = copy.deepcopy(_vision_config)
        text_global = copy.deepcopy(_model_config)
    if not vision_layer:
        return merged_config(override), False
    layer = dict(vision_layer)
    if conv_vision:
        # 对话级视觉覆盖也走同一套"空值算继承"
        layer = {**layer, **{k: v for k, v in conv_vision.items()
                             if v is not None and not (isinstance(v, str) and not v.strip())}}
    return _apply_override(text_global, layer), True


def get_vision_config() -> dict:
    """视觉角色的脱敏视图（给前端展示"画面分析实际会用哪个模型"）。"""
    with _config_lock:
        layer = copy.deepcopy(_vision_config)
        text = copy.deepcopy(_model_config)
    if not layer:
        # 回退态：直接展示主模型，并标明来源是回退
        view = mask_config(text)
        view.update({'separate': False, 'source': 'text'})
        return view
    try:
        cfg = _apply_override(text, layer)
    except ValueError as e:
        view = mask_config({})
        view.update({'separate': True, 'source': 'vision', 'error': str(e)})
        return view
    view = mask_config(cfg)
    view.update({'separate': True, 'source': 'vision'})
    return view


def config_fingerprint(cfg: dict) -> str:
    """配置指纹：用于缓存"按这份配置构建好的 agent"。

    必须包含真实 api_key——换了 key 却复用旧 agent 会一直用错凭证。
    """
    cfg = cfg or {}
    parts = [str(cfg.get(k) or '') for k in ('model', 'base_url', 'api_key', 'temperature', 'max_tokens')]
    return '|'.join(parts)


def build_model_for(cfg: dict):
    """按给定完整配置构建 ChatOpenAI；配置不齐全时返回 None。"""
    if not is_config_configured(cfg):
        return None
    kwargs = {k: cfg.get(k) for k in ('model', 'base_url', 'api_key', 'temperature', 'max_tokens')}
    kwargs['streaming'] = True
    return ChatOpenAI(**kwargs)

