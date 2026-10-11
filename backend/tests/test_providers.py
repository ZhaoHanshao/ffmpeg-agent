"""模型列表拉取（设置弹窗的下拉列表）的测试。

用**本地假提供商**（http.server 起在随机端口）跑真实的 HTTP 路径，
不打桩 httpx——路径拼接、回退、状态码处理、JSON 解析都是真跑的。

同时覆盖密钥安全边界：已存的 key 只允许发到**与它匹配的 base_url**，
否则拉列表就变成了把密钥泄露给用户随手填的地址。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import app.model as M  # noqa: E402
from app import providers  # noqa: E402

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


print('\n=== test_providers.py ===\n')

# ── 纯函数：地址候选与解析 ──
print('--- 地址候选 ---')
check('不带 /v1 的地址会额外尝试 /v1/models',
      providers.models_url_candidates('https://api.deepseek.com')
      == ['https://api.deepseek.com/models', 'https://api.deepseek.com/v1/models'])
check('带 /v1 的地址只试一个（不重复拼）',
      providers.models_url_candidates('https://api.openai.com/v1/')
      == ['https://api.openai.com/v1/models'],
      str(providers.models_url_candidates('https://api.openai.com/v1/')))
check('末尾斜杠被规范化',
      providers.models_url_candidates('https://x.example//') == ['https://x.example/models', 'https://x.example/v1/models'])
check('空地址返回空', providers.models_url_candidates('') == [])
check('非 http 地址返回空（不允许 file:// 之类）',
      providers.models_url_candidates('ftp://x.example') == [])

print('\n--- 返回解析 ---')
check('OpenAI 形状 {"data":[{"id":...}]}',
      providers.parse_models({'data': [{'id': 'gpt-4o'}, {'id': 'gpt-4o-mini'}]}) == ['gpt-4o', 'gpt-4o-mini'])
check('Ollama 形状 {"models":[{"name":...}]}',
      providers.parse_models({'models': [{'name': 'llama3'}]}) == ['llama3'])
check('裸字符串列表', providers.parse_models(['a', 'b']) == ['a', 'b'])
check('裸对象列表', providers.parse_models([{'id': 'a'}, {'model': 'b'}]) == ['a', 'b'])
check('去重且保持提供商顺序', providers.parse_models(['b', 'a', 'b']) == ['b', 'a'])
check('认不出来返回空', providers.parse_models({'nope': 1}) == [])
check('空输入返回空', providers.parse_models(None) == [])
check('忽略空名字', providers.parse_models([{'id': ''}, {'id': 'ok'}, {}]) == ['ok'])

# ── 本地假提供商 ──
state = {'mode': 'ok', 'hits': [], 'auth': []}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server 的约定
        state['hits'].append(self.path)
        state['auth'].append(self.headers.get('Authorization') or '')

        if state['mode'] == 'ok' and self.path in ('/models', '/v1/models'):
            self._json(200, {'data': [{'id': 'stub-basic'}, {'id': 'stub-vision-vl'}]})
        elif state['mode'] == 'only_v1' and self.path == '/v1/models':
            self._json(200, {'data': [{'id': 'stub-only-v1'}]})
        elif state['mode'] == 'only_v1':
            self._json(404, {'error': 'not found'})
        elif state['mode'] == 'unauthorized':
            self._json(401, {'error': {'message': 'Invalid API key'}})
        elif state['mode'] == 'notjson':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html>hi</html>')
        elif state['mode'] == 'emptylist':
            self._json(200, {'data': []})
        else:
            self._json(404, {'error': 'not found'})

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


server = HTTPServer(('127.0.0.1', 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
host = f'http://127.0.0.1:{port}'

try:
    print('\n--- 真实 HTTP 拉取 ---')
    state.update(mode='ok', hits=[], auth=[])
    got = providers.fetch_models(host, 'sk-typed-key')
    check('能取到模型列表', got == ['stub-basic', 'stub-vision-vl'], str(got))
    check('第一个候选就命中（不浪费一次请求）', state['hits'] == ['/models'], str(state['hits']))
    check('带了 Authorization 头', state['auth'] == ['Bearer sk-typed-key'], str(state['auth']))

    state.update(mode='only_v1', hits=[], auth=[])
    got = providers.fetch_models(host, 'k')
    check('第一个候选 404 时回退到 /v1/models', got == ['stub-only-v1'], str(got))
    check('确实尝试了两个候选', state['hits'] == ['/models', '/v1/models'], str(state['hits']))

    # 带 /v1 的 base_url 不该再拼出 /v1/models 之外的路径
    state.update(mode='ok', hits=[], auth=[])
    got = providers.fetch_models(f'{host}/v1', 'k')
    check('base_url 已带 /v1 时直接命中', state['hits'] == ['/v1/models'], str(state['hits']))

    print('\n--- 错误路径 ---')
    state.update(mode='unauthorized', hits=[], auth=[])
    try:
        providers.fetch_models(host, 'bad')
        check('401 时抛异常', False, '没有抛')
    except Exception as e:
        check('401 时抛异常', True)
        check('401 报错里带状态码', '401' in str(e), str(e))
    check('401 不会去试第二个候选（鉴权错换路径没意义）',
          state['hits'] == ['/models'], str(state['hits']))

    state['mode'] = 'notjson'
    try:
        providers.fetch_models(host, 'k')
        check('返回非 JSON 时抛异常', False, '没有抛')
    except Exception as e:
        check('返回非 JSON 时抛异常', True)

    state['mode'] = 'emptylist'
    try:
        providers.fetch_models(host, 'k')
        check('返回空列表时抛异常', False, '没有抛')
    except Exception as e:
        check('返回空列表时抛异常', True)

    state['mode'] = 'ok'
    try:
        providers.fetch_models('', 'k')
        check('空 base_url 抛异常', False, '没有抛')
    except ValueError:
        check('空 base_url 抛异常', True)

    # ── 密钥安全边界 ──
    print('\n--- 已存 key 只发给匹配的地址 ---')
    orig_text = M.get_raw_config()
    orig_vision = M._vision_config
    try:
        M._model_config.update(model='t', base_url=f'{host}/v1', api_key='sk-stored-secret')
        M._vision_config = None

        # 同一地址（含末尾斜杠/大小写差异）→ 可以用已存 key
        check('地址一致时取到已存 key',
              M.stored_key_for('text', f'{host}/v1/') == 'sk-stored-secret')
        check('地址大小写差异也算一致',
              M.stored_key_for('text', f'{host.upper()}/v1'.lower()) == 'sk-stored-secret')
        # 不同地址 → 不能把密钥发过去
        check('地址不一致时**不返回**已存 key',
              M.stored_key_for('text', 'https://evil.example/v1') == '',
              repr(M.stored_key_for('text', 'https://evil.example/v1')))

        check('脱敏回显被识别出来', M.is_masked_key('sk-****cdef') is True)
        check('真实 key 不会被误判为脱敏', M.is_masked_key('sk-abcdef123456') is False)

        # vision 角色取的是视觉层合并后的 key
        M._vision_config = {'model': 'v', 'base_url': 'https://vision.example/v1', 'api_key': 'sk-vision'}
        check('vision 角色取到视觉层自己的 key',
              M.stored_key_for('vision', 'https://vision.example/v1') == 'sk-vision')
        check('vision 角色的 key 不会发给主模型的地址',
              M.stored_key_for('vision', f'{host}/v1') == '')
    finally:
        M._model_config.clear()
        M._model_config.update(orig_text)
        M._vision_config = orig_vision
        M._build_model_locked()

finally:
    server.shutdown()
    server.server_close()

print(f'\n通过 {pass_count} 项，失败 {fail_count} 项\n')
sys.exit(1 if fail_count else 0)
