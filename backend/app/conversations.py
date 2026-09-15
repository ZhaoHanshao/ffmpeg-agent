"""多对话：会话记录的持久化与增删改查。

存储形态：`CONVERSATIONS_DIR/{id}.json`，一个对话一个文件。
- 元数据（标题/模式/时间/模型覆盖）和消息都在同一个文件里。
- 内存里维护一份索引 `{id: meta}`，启动时扫描目录构建一次；
  列表接口只读内存索引，不必每次解析所有文件。
  **不额外落盘索引文件**——那份冗余迟早会和真实文件不一致。
- 每个对话独立成文件的好处：追加消息只重写这一条，删除即删文件。

为什么不塞进单个 conversations.json：单文件在"追加一条消息"时要把全部历史
重写一遍，对话一多就变成 O(全部历史) 的写放大，且一次写坏全丢。
"""
import json
import logging
import os
import secrets
import threading
import time

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = os.getenv('CONVERSATIONS_DIR', 'backend/data/conversations')

# 消息条数上限：单对话保留最近 N 条，避免文件无限增长（前端只用最近 6 条做上下文）
MAX_MESSAGES = int(os.getenv('CONVERSATION_MAX_MESSAGES', '400') or 400)
# 单条消息正文上限（字符），防止粘贴超大文本把文件撑爆
MAX_TEXT_CHARS = 20000
TITLE_MAX_CHARS = 28

# 对话级可覆盖的 LLM 字段（与 model.LLM_CONFIG_FIELDS 保持一致）
LLM_OVERRIDE_FIELDS = ('model', 'base_url', 'api_key', 'temperature', 'max_tokens')

_lock = threading.RLock()
# id -> meta（不含 messages）；由 _load_index() 在导入时构建
_index = {}


# ── 基础工具 ──

def _path(cid: str) -> str:
    return os.path.join(CONVERSATIONS_DIR, f'{cid}.json')


def _valid_id(cid) -> bool:
    """id 只允许十六进制，避免通过 id 做路径穿越。"""
    cid = str(cid or '')
    return bool(cid) and len(cid) <= 64 and all(c in '0123456789abcdef' for c in cid)


def _now() -> float:
    return time.time()


def _meta_of(record: dict) -> dict:
    """从完整记录里取列表需要的字段（不含 messages，避免列表接口返回全部历史）。"""
    return {
        'id': record['id'],
        'title': record.get('title') or '新对话',
        'mode': record.get('mode') or 'ffmpeg',
        'created_at': record.get('created_at') or 0,
        'updated_at': record.get('updated_at') or 0,
        'message_count': len(record.get('messages') or []),
        'has_llm_override': bool(record.get('llm')),
        'llm_model': (record.get('llm') or {}).get('model') or '',
    }


def _write(record: dict):
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    path = _path(record['id'])
    # 先写临时文件再替换：中途崩溃/被杀不会留下半截 JSON 把整个对话读坏
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read(cid: str):
    if not _valid_id(cid):
        return None
    try:
        with open(_path(cid), 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning(f'读取对话 {cid} 失败：{e}')
        return None
    if not isinstance(data, dict) or data.get('id') != cid:
        logger.warning(f'对话文件 {cid} 内容异常，已忽略')
        return None
    data.setdefault('messages', [])
    return data


def _load_index():
    """启动时扫描目录构建内存索引（只解析元数据字段，但仍然要读完整文件）。"""
    global _index
    idx = {}
    if not os.path.isdir(CONVERSATIONS_DIR):
        _index = idx
        return
    for name in os.listdir(CONVERSATIONS_DIR):
        if not name.endswith('.json') or name.endswith('.tmp'):
            continue
        record = _read(name[:-5])
        if record:
            idx[record['id']] = _meta_of(record)
    _index = idx
    if idx:
        logger.info(f'已加载 {len(idx)} 个对话记录')


def _clean_title(text: str) -> str:
    """标题取首条用户消息的前若干字。"""
    t = ' '.join((text or '').split())
    if len(t) > TITLE_MAX_CHARS:
        t = t[:TITLE_MAX_CHARS] + '…'
    return t or '新对话'


def _clip(text) -> str:
    s = str(text or '')
    return s[:MAX_TEXT_CHARS]


# ── 对外接口 ──

def list_conversations(mode: str = '') -> list:
    """按更新时间倒序列出对话元数据；mode 非空时只返回该模式的对话。"""
    with _lock:
        items = [dict(m) for m in _index.values() if not mode or m.get('mode') == mode]
    items.sort(key=lambda m: (-m.get('updated_at', 0), m.get('id', '')))
    return items


def create_conversation(mode: str = 'ffmpeg', title: str = '') -> dict:
    cid = secrets.token_hex(8)
    now = _now()
    record = {
        'id': cid,
        'title': (title or '').strip()[:TITLE_MAX_CHARS] or '新对话',
        'mode': 'ffprobe' if mode == 'ffprobe' else 'ffmpeg',
        'created_at': now,
        'updated_at': now,
        # None = 继承全局配置；有值时为字段级覆盖（未给的字段仍继承）
        'llm': None,
        'messages': [],
    }
    with _lock:
        _write(record)
        _index[cid] = _meta_of(record)
    logger.info(f'新建对话 {cid}（{record["mode"]}）')
    return record


def get_conversation(cid: str):
    with _lock:
        return _read(cid)


def rename_conversation(cid: str, title: str):
    with _lock:
        record = _read(cid)
        if not record:
            return None
        new_title = (title or '').strip()[:TITLE_MAX_CHARS]
        if new_title:
            record['title'] = new_title
            record['updated_at'] = _now()
            _write(record)
            _index[cid] = _meta_of(record)
        return record


def set_llm_override(cid: str, override):
    """设置/清除对话级 LLM 覆盖。override 为 None 或空 dict 表示恢复继承全局。"""
    with _lock:
        record = _read(cid)
        if not record:
            return None
        cleaned = {}
        if isinstance(override, dict):
            for k in LLM_OVERRIDE_FIELDS:
                v = override.get(k)
                if v is None:
                    continue
                if isinstance(v, str):
                    v = v.strip()
                    if not v:
                        continue
                cleaned[k] = v
        record['llm'] = cleaned or None
        record['updated_at'] = _now()
        _write(record)
        _index[cid] = _meta_of(record)
        return record


def delete_conversation(cid: str) -> bool:
    with _lock:
        if not _valid_id(cid) or cid not in _index:
            return False
        try:
            os.remove(_path(cid))
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f'删除对话 {cid} 失败：{e}')
            return False
        _index.pop(cid, None)
    logger.info(f'已删除对话 {cid}')
    return True


def append_turn(cid: str, user_msg: dict, ai_msg: dict) -> bool:
    """把一轮问答追加到对话末尾。对话不存在时返回 False（不凭空重建）。"""
    if not _valid_id(cid):
        return False
    with _lock:
        record = _read(cid)
        if not record:
            return False
        msgs = record.setdefault('messages', [])
        now = _now()
        um = {
            'role': 'user',
            'text': _clip(user_msg.get('text')),
            'files': user_msg.get('files') or [],
            'ts': user_msg.get('ts') or now,
        }
        am = {
            'role': 'ai',
            'text': _clip(ai_msg.get('text')),
            'output_file': ai_msg.get('output_file') or '',
            'error': _clip(ai_msg.get('error')),
            'ts': ai_msg.get('ts') or now,
        }
        msgs.append(um)
        msgs.append(am)
        if len(msgs) > MAX_MESSAGES:
            # 保留最近的 MAX_MESSAGES 条（成对裁剪，避免从 AI 回复中间切断）
            record['messages'] = msgs[-MAX_MESSAGES:]
        # 首条用户消息定标题：默认标题没有信息量，列表里全靠它区分
        if record.get('title') in ('', '新对话'):
            record['title'] = _clean_title(um['text'])
        record['updated_at'] = now
        _write(record)
        _index[cid] = _meta_of(record)
    return True


def stats() -> dict:
    with _lock:
        return {'conversations': len(_index), 'dir': CONVERSATIONS_DIR}


_load_index()
