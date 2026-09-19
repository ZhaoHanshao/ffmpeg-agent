"""从 OpenAI 兼容提供商拉取可用模型列表。

为什么放在服务端而不是浏览器直连：
- 多数提供商的 `/v1/models` 不带 CORS 头，浏览器直连会被挡；
- 服务端本来就在用同一个 base_url 发推理请求（信任模型没有变化），
  而且能复用已保存的 key，用户不必为了"看一眼列表"再粘贴一次。

只做只读的 GET，不发送任何用户数据。
"""
import logging

import httpx

logger = logging.getLogger(__name__)

# 拉列表是交互式操作，超时给短一点，别让用户干等
DEFAULT_TIMEOUT = 20.0


def models_url_candidates(base_url: str) -> list:
    """按可能性列出候选的模型列表地址。

    很多提供商的 base_url 不带 `/v1`（DeepSeek 官方示例就是 `https://api.deepseek.com`），
    但也同样接受带 `/v1` 的路径；反过来 OpenAI 的 base_url 必须带 `/v1`。
    两种都试一遍比让用户猜格式友好。
    """
    base = (base_url or '').strip().rstrip('/')
    if not base:
        return []
    if not (base.startswith('http://') or base.startswith('https://')):
        return []
    urls = [f'{base}/models']
    if not base.endswith('/v1'):
        urls.append(f'{base}/v1/models')
    return urls


def parse_models(payload) -> list:
    """从各家五花八门的返回里挑出模型名。

    见过或可能见到的形状：
      {"data": [{"id": "gpt-4o"}, ...]}         OpenAI 官方
      {"models": [{"name": "llama3"}, ...]}     Ollama 风格
      {"models": ["a", "b"]} / ["a", "b"]       裸列表
    认不出来就返回空列表，由调用方给出可读错误。
    """
    items = None
    if isinstance(payload, dict):
        for key in ('data', 'models', 'result', 'items'):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    elif isinstance(payload, list):
        items = payload

    if items is None:
        return []

    out = []
    for item in items:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get('id') or item.get('name') or item.get('model') or ''
        else:
            name = ''
        name = str(name).strip()
        # 去重但保持提供商给的顺序（通常新模型在前，比字母序更有用）
        if name and name not in out:
            out.append(name)
    return out


def friendly_error(exc: Exception, base_url: str) -> str:
    """把拉列表时的异常转成能照着做的提示。

    通用的 `_friendly_error` 会说"知识库查询或命令执行失败"，在这个上下文里
    完全没有指向性——用户点的是"获取列表"，需要知道是地址错了、还是 key 不对。
    """
    text = str(exc or '')
    low = text.lower()
    if 'timed out' in low or 'timeout' in type(exc).__name__.lower():
        return f'连接 {base_url} 超时：请检查接口地址与网络'
    if any(k in text for k in ('10061', 'Connection refused', 'ConnectError', 'getaddrinfo',
                               'Name or service not known', 'Failed to resolve')):
        return f'无法连接到 {base_url}：请确认地址可以从服务器访问'
    head = text[:16]
    if '401' in head:
        return '鉴权失败（401）：API Key 无效，或该 Key 无权列出模型'
    if '403' in head:
        return '拒绝访问（403）：该 API Key 无权列出模型'
    if '404' in head or '405' in head:
        return f'{base_url} 上没有 /models 接口：请确认这是 OpenAI 兼容的地址（通常以 /v1 结尾）'
    if '429' in head:
        return '请求过于频繁（429）：请稍后重试'
    if '不是 JSON' in text or '没有模型列表' in text:
        return f'{base_url} 的返回不是标准的模型列表：{text[:160]}'
    return f'获取模型列表失败：{text[:200]}'


def fetch_models(base_url: str, api_key: str = '', timeout: float = DEFAULT_TIMEOUT) -> list:
    """请求模型列表；失败抛异常（由 friendly_error 转成可读提示）。"""
    urls = models_url_candidates(base_url)
    if not urls:
        raise ValueError('接口地址必须以 http:// 或 https:// 开头')

    headers = {'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    last_error = None
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for url in urls:
            try:
                res = client.get(url, headers=headers)
            except httpx.HTTPError as e:
                last_error = e
                continue

            # 404/405 通常是这个候选路径不对，换下一个试
            if res.status_code in (404, 405):
                # 状态码放最前面：friendly_error 靠开头几位识别错误类型
                last_error = RuntimeError(f'HTTP {res.status_code}: {url}')
                continue
            if res.status_code >= 400:
                # 401/403 之类是明确的鉴权/权限问题，换路径没有意义，直接报出去
                raise RuntimeError(f'HTTP {res.status_code}: {res.text[:200]}')

            try:
                payload = res.json()
            except ValueError:
                last_error = RuntimeError(f'{url} 返回的不是 JSON')
                continue

            models = parse_models(payload)
            if models:
                logger.info(f'从 {url} 获取到 {len(models)} 个模型')
                return models
            last_error = RuntimeError(f'{url} 的返回里没有模型列表')
    raise RuntimeError(str(last_error) if last_error else '未能获取模型列表')
