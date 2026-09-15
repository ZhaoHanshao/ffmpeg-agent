"""多对话功能回归测试：存储层 CRUD、对话级模型覆盖、SSE 落库。

沿用项目既有的 check(name, cond, detail) 风格，无测试框架依赖。
测试把 CONVERSATIONS_DIR 指向临时目录，绝不碰真实会话数据。
"""
import json
import os
import shutil
import sys
import tempfile

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv
load_dotenv()

# 必须在 import app.conversations 之前改目录：它在导入时就扫描并建索引
_TMP_DIR = tempfile.mkdtemp(prefix='conv-test-')
os.environ['CONVERSATIONS_DIR'] = _TMP_DIR

import app.conversations as C  # noqa: E402
import app.model as M  # noqa: E402

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


print('\n=== test_conversations.py ===\n')

try:
    # ── 基础 CRUD ──
    print('--- 存储层 CRUD ---')
    check('初始索引为空', C.list_conversations() == [], f'实际 {C.list_conversations()}')

    rec = C.create_conversation('ffmpeg')
    cid = rec['id']
    check('创建返回 id', bool(cid) and len(cid) == 16, f'id={cid}')
    check('默认标题为「新对话」', rec['title'] == '新对话', rec['title'])
    check('落盘为独立文件', os.path.isfile(os.path.join(_TMP_DIR, f'{cid}.json')))
    check('列表里出现该对话', len(C.list_conversations()) == 1)

    # 首条用户消息决定标题
    C.append_turn(cid, {'text': '把视频压缩到 720p，尽量小一点，音轨保留'}, {'text': '好的'})
    got = C.get_conversation(cid)
    check('消息已写入（一问一答两条）', len(got['messages']) == 2, f"{len(got['messages'])}")
    check('用户消息角色正确', got['messages'][0]['role'] == 'user')
    check('AI 消息角色正确', got['messages'][1]['role'] == 'ai')
    check('标题由首条用户消息生成',
          got['title'].startswith('把视频压缩到 720p'), got['title'])
    check('标题长度受限', len(got['title']) <= C.TITLE_MAX_CHARS + 1, got['title'])
    check('meta 里的 message_count 同步', C.list_conversations()[0]['message_count'] == 2)

    # 已改名后追加消息不应覆盖标题
    C.rename_conversation(cid, '我的压缩任务')
    C.append_turn(cid, {'text': '再压小一点'}, {'text': '好'})
    check('改名后追加消息不覆盖标题', C.get_conversation(cid)['title'] == '我的压缩任务')

    # ── 排序 ──
    print('--- 排序与过滤 ---')
    rec2 = C.create_conversation('ffprobe')
    C.append_turn(rec2['id'], {'text': '看看分辨率'}, {'text': '1920x1080'})
    lst = C.list_conversations()
    check('最新更新的排最前', lst[0]['id'] == rec2['id'], f"{[x['id'] for x in lst]}")
    check('按模式过滤（ffprobe）',
          [x['id'] for x in C.list_conversations('ffprobe')] == [rec2['id']])
    check('按模式过滤（ffmpeg）',
          [x['id'] for x in C.list_conversations('ffmpeg')] == [cid])
    check('列表不含 messages 字段', 'messages' not in lst[0])

    # ── 持久化：重新扫描目录后仍在 ──
    print('--- 持久化 ---')
    C._load_index()
    check('重新扫描后仍能列出全部对话', len(C.list_conversations()) == 2)
    check('重新扫描后消息完好',
          len(C.get_conversation(cid)['messages']) == 4,
          f"{len(C.get_conversation(cid)['messages'])}")

    # ── 安全：id 校验 ──
    print('--- 安全 ---')
    check('非法 id 读不到', C.get_conversation('../../etc/passwd') is None)
    check('不存在的 id 读不到', C.get_conversation('deadbeefdeadbeef') is None)
    check('删除非法 id 返回 False', C.delete_conversation('..') is False)
    check('对不存在的对话 append_turn 返回 False',
          C.append_turn('deadbeefdeadbeef', {'text': 'x'}, {'text': 'y'}) is False)
    check('.tmp 残file不会被当成对话',
          not any(n.endswith('.tmp.json') for n in os.listdir(_TMP_DIR)))

    # 超长正文被截断（防止粘贴超大文本撑爆文件）
    long_text = 'x' * (C.MAX_TEXT_CHARS + 500)
    C.append_turn(cid, {'text': long_text}, {'text': 'ok'})
    msgs = C.get_conversation(cid)['messages']
    check('超长正文被截断到上限', len(msgs[-2]['text']) == C.MAX_TEXT_CHARS, f"{len(msgs[-2]['text'])}")

    # ── 对话级 LLM 覆盖 ──
    print('--- 对话级模型覆盖 ---')
    check('默认无覆盖', C.get_conversation(cid)['llm'] is None)
    check('meta 标记 has_llm_override=False',
          [x for x in C.list_conversations() if x['id'] == cid][0]['has_llm_override'] is False)

    C.set_llm_override(cid, {'model': 'gpt-4o-mini', 'temperature': 0.9})
    ov = C.get_conversation(cid)['llm']
    check('覆盖已保存', ov == {'model': 'gpt-4o-mini', 'temperature': 0.9}, str(ov))
    check('meta 同步覆盖标记',
          [x for x in C.list_conversations() if x['id'] == cid][0]['has_llm_override'] is True)
    check('meta 带出覆盖的模型名',
          [x for x in C.list_conversations() if x['id'] == cid][0]['llm_model'] == 'gpt-4o-mini')

    C.set_llm_override(cid, {'model': '  ', 'temperature': None, 'base_url': 'https://x.test/v1'})
    ov = C.get_conversation(cid)['llm']
    check('空白字符串与 None 视为「不覆盖」', ov == {'base_url': 'https://x.test/v1'}, str(ov))

    C.set_llm_override(cid, {})
    check('空 dict 清除覆盖（恢复继承全局）', C.get_conversation(cid)['llm'] is None)
    C.set_llm_override(cid, None)
    check('None 清除覆盖', C.get_conversation(cid)['llm'] is None)

    # ── 覆盖合并到全局配置 ──
    print('--- 配置合并 ---')
    base = M.get_raw_config()
    merged = M.merged_config({'temperature': 1.5})
    check('只覆盖指定字段，其余继承全局',
          merged['temperature'] == 1.5 and merged['model'] == base.get('model'),
          f"t={merged['temperature']} model={merged['model']}")
    check('merged_config(None) 等于全局配置',
          M.merged_config(None)['model'] == base.get('model'))
    check('空字符串不覆盖全局', M.merged_config({'model': ''})['model'] == base.get('model'))

    masked = M.mask_config(base)
    check('mask_config 不带出明文 key',
          (not base.get('api_key')) or masked['api_key'] != base.get('api_key'),
          masked.get('api_key', ''))
    check('mask_config 标出 key 是否已配置',
          masked['key_configured'] == bool(base.get('api_key')))
    # 把脱敏值原样回传不能把真 key 抹掉
    if base.get('api_key'):
        round_trip = M.merged_config({'api_key': M.mask_key(base['api_key'])})
        check('脱敏 key 回传不会覆盖真实 key', round_trip['api_key'] == base['api_key'])

    try:
        M.merged_config({'temperature': 9})
        check('temperature 越界应被拒绝', False, '未抛 ValueError')
    except ValueError:
        check('temperature 越界应被拒绝', True)
    try:
        M.merged_config({'base_url': 'ftp://x'})
        check('非法 base_url 应被拒绝', False, '未抛 ValueError')
    except ValueError:
        check('非法 base_url 应被拒绝', True)

    # ── 配置指纹必须区分 api_key ──
    print('--- 配置指纹 ---')
    a = dict(base, api_key='key-a')
    b = dict(base, api_key='key-b')
    check('不同 api_key 指纹不同', M.config_fingerprint(a) != M.config_fingerprint(b))
    check('相同配置指纹相同', M.config_fingerprint(a) == M.config_fingerprint(dict(a)))
    check('指纹不含明文 key 之外的信息缺失',
          'key-a' in M.config_fingerprint(a))

    # ── 删除 ──
    print('--- 删除 ---')
    check('删除存在的对话返回 True', C.delete_conversation(rec2['id']) is True)
    check('删除后从列表消失', len(C.list_conversations()) == 1)
    check('删除后文件也没了', not os.path.isfile(os.path.join(_TMP_DIR, f"{rec2['id']}.json")))
    check('重复删除返回 False', C.delete_conversation(rec2['id']) is False)

    # ── 写入原子性 ──
    print('--- 原子写 ---')
    leftovers = [n for n in os.listdir(_TMP_DIR) if n.endswith('.tmp')]
    check('目录里没有残留的 .tmp 文件', leftovers == [], str(leftovers))
    raw = json.load(open(os.path.join(_TMP_DIR, f'{cid}.json'), encoding='utf-8'))
    check('落盘 JSON 可解析且结构完整',
          raw['id'] == cid and isinstance(raw['messages'], list) and 'llm' in raw)

    # ── 端到端接线：对话级配置进图谱 + 流结束落库 ──
    print('--- _chat_response 接线 ---')
    import asyncio
    import app.main as m

    class _Spec:
        """记录 ensure() 收到的 state，用来验证 llm_config 有没有串下去。"""

        def __init__(self):
            self.seen = []

        def ensure(self, state=None):
            self.seen.append(state)
            return True

    class _Chunk:
        def __init__(self, content):
            self.content = content

    class _Agent:
        async def astream_events(self, payload, version='v2'):
            yield {'event': 'on_chat_model_stream', 'data': {'chunk': _Chunk('已')}}
            yield {'event': 'on_chat_model_stream', 'data': {'chunk': _Chunk('完成')}}

    captured = {}

    def fake_graph(q, p, **kw):
        captured.clear()
        captured.update(kw)
        captured['question'] = q
        return {
            'output_file': 'demo_output.webp',
            'result': '', 'command': None, 'command_result': '',
            'files': kw.get('files') or [],
            'history': [type('M', (), {'content': q})()],
        }

    async def drain(response):
        events = []
        # 前置校验失败时 _sse_error() 返回的是普通 Response（没有 body_iterator）
        if not hasattr(response, 'body_iterator'):
            body = response.body
            if isinstance(body, bytes):
                body = body.decode('utf-8', 'replace')
            for line in str(body).splitlines():
                if line.startswith('data: '):
                    events.append(json.loads(line[6:]))
            return events
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunk = chunk.decode('utf-8', 'replace')
            for line in str(chunk).splitlines():
                if line.startswith('data: '):
                    events.append(json.loads(line[6:]))
        return events

    # 打桩 chat agent：这里只验证接线，不需要真的构建 agent
    real_get_chat_agent = m.get_chat_agent
    m.get_chat_agent = lambda kind, cfg=None: _Agent()
    m._init_state['status'] = 'ok'

    conv = C.create_conversation('ffmpeg')
    try:
        # ① 没有覆盖 → llm_config 必须是 None（继承全局）
        spec = _Spec()
        resp = m._chat_response(
            '把图片反色', [], [], kind='', graph_fn=fake_graph,
            prompt_builder=lambda st: 'p', spec=spec, conversation_id=conv['id'],
        )
        asyncio.run(drain(resp))
        check('无覆盖时 ensure 收到 llm_config=None', spec.seen[0].get('llm_config') is None,
              str(spec.seen[0].get('llm_config')))
        check('无覆盖时图谱也拿到 llm_config=None', captured.get('llm_config') is None)
        check('本轮问答已落库（+2 条）',
              len(C.get_conversation(conv['id'])['messages']) == 2,
              str(len(C.get_conversation(conv['id'])['messages'])))

        # ② 有覆盖 → 合并后的完整配置要同时进 ensure 和图谱
        C.set_llm_override(conv['id'], {'model': 'gpt-4o-mini', 'temperature': 1.7})
        spec2 = _Spec()
        resp2 = m._chat_response(
            '把图片反色', [], [], kind='', graph_fn=fake_graph,
            prompt_builder=lambda st: 'p', spec=spec2, conversation_id=conv['id'],
        )
        events = asyncio.run(drain(resp2))
        cfg_seen = spec2.seen[0].get('llm_config') or {}
        check('有覆盖时 ensure 收到合并后的 model', cfg_seen.get('model') == 'gpt-4o-mini',
              str(cfg_seen.get('model')))
        check('有覆盖时 temperature 取覆盖值', cfg_seen.get('temperature') == 1.7,
              str(cfg_seen.get('temperature')))
        check('未覆盖的字段仍继承全局', cfg_seen.get('base_url') == M.get_raw_config().get('base_url'))
        check('图谱收到同一份对话级配置', captured.get('llm_config') == cfg_seen)
        check('SSE 正常推完 token 与 done',
              any(e.get('event') == 'token' for e in events) and events[-1].get('event') == 'done',
              str([e.get('event') for e in events]))

        # ③ 流结束后本轮问答必须落库（此时累计两轮 = 4 条）
        msgs = C.get_conversation(conv['id'])['messages']
        check('第二轮问答也写回（累计 4 条）', len(msgs) == 4, f'{len(msgs)}')
        check('用户消息文本已存', msgs[2]['text'] == '把图片反色', msgs[2]['text'])
        check('AI 消息是完整回答', msgs[3]['text'] == '已完成', repr(msgs[3]['text']))
        check('输出文件已记录', msgs[3]['output_file'] == 'demo_output.webp',
              msgs[3]['output_file'])
        check('对话标题由首轮问题生成', C.get_conversation(conv['id'])['title'] == '把图片反色')

        # ④ 对话不存在 → 直接 SSE 报错，不去跑图谱
        spec3 = _Spec()
        resp3 = m._chat_response(
            'x', [], [], kind='', graph_fn=fake_graph,
            prompt_builder=lambda st: 'p', spec=spec3, conversation_id='deadbeefdeadbeef',
        )
        ev3 = asyncio.run(drain(resp3))
        check('对话不存在时返回 SSE 错误', ev3[0].get('event') == 'error', str(ev3[:1]))
        check('对话不存在时不触碰图谱', spec3.seen == [])

        # ⑤ 覆盖配置非法 → SSE 报错而不是抛异常
        bad = C.create_conversation('ffmpeg')
        C.set_llm_override(bad['id'], {'temperature': 9})
        spec4 = _Spec()
        resp4 = m._chat_response(
            'x', [], [], kind='', graph_fn=fake_graph,
            prompt_builder=lambda st: 'p', spec=spec4, conversation_id=bad['id'],
        )
        ev4 = asyncio.run(drain(resp4))
        check('覆盖配置非法时返回 SSE 错误', ev4[0].get('event') == 'error', str(ev4[:1]))
        check('非法配置的报错信息可读', 'temperature' in ev4[0].get('text', ''),
              ev4[0].get('text', ''))
        check('前置失败不写入对话记录',
              C.get_conversation(bad['id'])['messages'] == [])
        C.delete_conversation(bad['id'])

        # ⑥ 图谱报错时：错误只存 error 字段，不能同时拼进 text（否则重开对话会显示两遍）
        fail_conv = C.create_conversation('ffmpeg')

        def failing_graph(q, p, **kw):
            raise RuntimeError('LLM 鉴权失败（401）')

        spec5 = _Spec()
        resp5 = m._chat_response(
            '会失败的请求', [], [], kind='', graph_fn=failing_graph,
            prompt_builder=lambda st: 'p', spec=spec5, conversation_id=fail_conv['id'],
        )
        ev5 = asyncio.run(drain(resp5))
        check('图谱异常时推 SSE error 而不是抛出去',
              [e.get('event') for e in ev5] == ['job', 'error', 'done'],
              str([e.get('event') for e in ev5]))
        stored = C.get_conversation(fail_conv['id'])['messages']
        check('失败的一轮也写进记录（用户不至于"问过什么都没了"）',
              len(stored) == 2, f'{len(stored)}')
        check('错误的 AI 消息 text 为空（避免与 error 重复显示）',
              stored[1]['text'] == '', repr(stored[1]['text']))
        check('错误内容存在 error 字段里', '401' in stored[1]['error'], stored[1]['error'])
        C.delete_conversation(fail_conv['id'])
    finally:
        m.get_chat_agent = real_get_chat_agent
        C.delete_conversation(conv['id'])

    # ── 真实 GraphSpec × 对话级配置 ──
    # 回归：GraphSpec.ensure() 曾把**自己**（GraphSpec）当作 AgentSpec 传给
    # agents_for()，于是任何"对话级模型覆盖"的第一次提问都会
    # AttributeError: 'GraphSpec' object has no attribute 'search_prompt'。
    # 上面用的都是假的 spec，正好绕开了这条真实路径。
    print('--- 真实 GraphSpec 的对话级配置 ---')
    import app.graph as G

    cfg_eff = M.merged_config({'model': 'gpt-4o-mini'})
    check('GraphSpec 已绑定对应的 AgentSpec',
          getattr(G.FFMPEG_GRAPH, 'agent_spec', None) is not None)
    try:
        ok = G.FFMPEG_GRAPH.ensure({'llm_config': cfg_eff})
        check('带对话级配置时 ensure 不抛异常', True)
        check('带对话级配置时 ensure 返回 True', ok is True, f'got {ok}')
    except Exception as e:  # noqa: BLE001 - 这里就是要暴露异常
        check('带对话级配置时 ensure 不抛异常', False, f'{type(e).__name__}: {e}')

    try:
        agent = G.FFMPEG_GRAPH.agent('chat', {'llm_config': cfg_eff})
        check('能取到按该配置构建的 chat agent', agent is not None)
        check('同一配置命中缓存（对象相同）',
              G.FFMPEG_GRAPH.agent('chat', {'llm_config': cfg_eff}) is agent)
        probe_agent = G.PROBE_GRAPH.agent('chat', {'llm_config': cfg_eff})
        check('ffprobe 变体也能构建且与 ffmpeg 不是同一对象', probe_agent is not None and probe_agent is not agent)
    except Exception as e:  # noqa: BLE001
        check('能取到按该配置构建的 chat agent', False, f'{type(e).__name__}: {e}')

    try:
        search_agent = G.FFMPEG_GRAPH.agent('search', {'llm_config': cfg_eff})
        execute_agent = G.FFMPEG_GRAPH.agent('execute', {'llm_config': cfg_eff})
        check('search/execute 角色索引正确（三个 agent 互不相同）',
              len({id(search_agent), id(execute_agent), id(agent)}) == 3)
    except Exception as e:  # noqa: BLE001
        check('search/execute 角色索引正确（三个 agent 互不相同）', False, f'{e}')

    # 无配置时必须仍走全局路径，不能被对话级改造带偏
    try:
        G.FFMPEG_GRAPH.ensure()
        check('不带 llm_config 时仍能走全局 ensure 路径', True)
    except Exception as e:  # noqa: BLE001
        check('不带 llm_config 时仍能走全局 ensure 路径', False, f'{e}')

finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)

print(f'\n通过 {pass_count} 项，失败 {fail_count} 项\n')
sys.exit(1 if fail_count else 0)
