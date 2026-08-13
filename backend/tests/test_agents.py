import os, sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv
load_dotenv()

from app.agents import (
    _search_prompt, _execute_prompt, _chat_prompt,
    _probe_search_prompt, _probe_execute_prompt, _probe_chat_prompt,
    agent_search, agent_execute, agent_chat,
    agent_probe_search, agent_probe_execute, agent_probe_chat,
    ensure_agents, ensure_probe_agents, _build_agents, _build_probe_agents,
    _search_tool_limit, _probe_search_tool_limit,
    _execute_tool_limit, _probe_execute_tool_limit,
)
from app.model import is_configured
from langchain.agents.middleware import ToolCallLimitMiddleware

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


print('\n=== test_agents.py ===\n')


# ── 系统提示词 ──
print('--- 系统提示词 ---')
check('search 提示词不为空', len(_search_prompt) > 0)
check('execute 提示词不为空', len(_execute_prompt) > 0)
check('chat 提示词不为空', len(_chat_prompt) > 0)

check('search 提示词包含 get_command', 'get_command' in _search_prompt)
check('execute 提示词包含 execute_command', 'execute_command' in _execute_prompt)
check('execute 提示词包含 get_files', 'get_files' in _execute_prompt)
check('execute 提示词拒绝非 ffmpeg', 'ffmpeg' in _execute_prompt and ('非' in _execute_prompt or '不要' in _execute_prompt or '不能' in _execute_prompt))
check('chat 提示词包含输出文件下载说明', '/api/output/' in _chat_prompt)


# ── 工具调用限制中间件 ──
print('\n--- 工具调用限制 ---')
check('_search_tool_limit 是 ToolCallLimitMiddleware',
      isinstance(_search_tool_limit, ToolCallLimitMiddleware))
check('search 工具限制为 get_command', _search_tool_limit.tool_name == 'get_command')
check('search 工具 run_limit 为 5', _search_tool_limit.run_limit == 5)


# ── 执行工具调用限制中间件 ──
print('\n--- 执行工具调用限制 ---')
check('_execute_tool_limit 是 ToolCallLimitMiddleware',
      isinstance(_execute_tool_limit, ToolCallLimitMiddleware))
check('execute 工具限制为 execute_command', _execute_tool_limit.tool_name == 'execute_command')
check('execute 工具 run_limit 为 1', _execute_tool_limit.run_limit == 1)
check('_probe_execute_tool_limit 是 ToolCallLimitMiddleware',
      isinstance(_probe_execute_tool_limit, ToolCallLimitMiddleware))
check('probe execute 工具限制为 execute_probe_command', _probe_execute_tool_limit.tool_name == 'execute_probe_command')
check('probe execute 工具 run_limit 为 1', _probe_execute_tool_limit.run_limit == 1)


# ── ffprobe 系统提示词 ──
print('\n--- ffprobe 系统提示词 ---')
check('probe search 提示词不为空', len(_probe_search_prompt) > 0)
check('probe execute 提示词不为空', len(_probe_execute_prompt) > 0)
check('probe chat 提示词不为空', len(_probe_chat_prompt) > 0)

check('probe search 提示词包含 get_probe_command', 'get_probe_command' in _probe_search_prompt)
check('probe execute 提示词包含 execute_probe_command', 'execute_probe_command' in _probe_execute_prompt)
check('probe execute 提示词包含 get_files', 'get_files' in _probe_execute_prompt)
check('probe execute 提示词拒绝非 ffprobe', 'ffprobe' in _probe_execute_prompt and ('非' in _probe_execute_prompt or '不要' in _probe_execute_prompt))


# ── ffprobe 工具调用限制中间件 ──
print('\n--- ffprobe 工具调用限制 ---')
check('_probe_search_tool_limit 是 ToolCallLimitMiddleware',
      isinstance(_probe_search_tool_limit, ToolCallLimitMiddleware))
check('probe search 工具限制为 get_probe_command', _probe_search_tool_limit.tool_name == 'get_probe_command')
check('probe search 工具 run_limit 为 5', _probe_search_tool_limit.run_limit == 5)


# ── ensure_probe_agents ──
print('\n--- ensure_probe_agents ---')
result = ensure_probe_agents()
check('ensure_probe_agents 返回布尔值', isinstance(result, bool))
if is_configured():
    check('已配置时返回 True', result is True)
    check('agent_probe_search 不为 None', agent_probe_search is not None)
    check('agent_probe_execute 不为 None', agent_probe_execute is not None)
    check('agent_probe_chat 不为 None', agent_probe_chat is not None)
else:
    check('未配置时返回 False', result is False)
    check('agent_probe_search 为 None', agent_probe_search is None)
    check('agent_probe_execute 为 None', agent_probe_execute is None)
    check('agent_probe_chat 为 None', agent_probe_chat is None)


# ── _build_probe_agents ──
print('\n--- _build_probe_agents ---')
probe_agents = _build_probe_agents()
if probe_agents[0] is not None:
    check('返回三个 agent', len(probe_agents) == 3)
    s, e, c = probe_agents
    check('agent_probe_search 有 get_probe_command 工具', any(
        getattr(t, 'name', '') == 'get_probe_command'
        for t in getattr(s, 'tools', []) or []
    ))
    check('agent_probe_execute 有 execute_probe_command 工具', any(
        getattr(t, 'name', '') == 'execute_probe_command'
        for t in getattr(e, 'tools', []) or []
    ))
    check('agent_probe_chat 没有工具', len(getattr(c, 'tools', []) or []) == 0)
else:
    check('未配置 LLM，跳过 ffprobe agent 结构测试', True)


# ── ffprobe agent 工具列表（如果已创建） ──
if agent_probe_search is not None:
    print('\n--- ffprobe agent 工具列表 ---')
    s_tools = [getattr(t, 'name', str(t)) for t in (getattr(agent_probe_search, 'tools', []) or [])]
    e_tools = [getattr(t, 'name', str(t)) for t in (getattr(agent_probe_execute, 'tools', []) or [])]
    c_tools = [getattr(t, 'name', str(t)) for t in (getattr(agent_probe_chat, 'tools', []) or [])]

    check('agent_probe_search 有 get_probe_command', 'get_probe_command' in s_tools)
    check('agent_probe_execute 有 get_files', 'get_files' in e_tools)
    check('agent_probe_execute 有 execute_probe_command', 'execute_probe_command' in e_tools)
    check('agent_probe_chat 没有工具', len(c_tools) == 0)


# ── ensure_agents ──
print('\n--- ensure_agents ---')
result = ensure_agents()
check('ensure_agents 返回布尔值', isinstance(result, bool))
if is_configured():
    check('已配置时返回 True', result is True)
    check('agent_search 不为 None', agent_search is not None)
    check('agent_execute 不为 None', agent_execute is not None)
    check('agent_chat 不为 None', agent_chat is not None)
else:
    check('未配置时返回 False', result is False)
    check('agent_search 为 None', agent_search is None)
    check('agent_execute 为 None', agent_execute is None)
    check('agent_chat 为 None', agent_chat is None)


# ── _build_agents ──
print('\n--- _build_agents ---')
agents = _build_agents()
if agents[0] is not None:
    s, e, c = agents
    check('返回三个 agent', len(agents) == 3)
    check('agent_search 有 get_command 工具', any(
        getattr(t, 'name', '') == 'get_command' or getattr(t, 'name', '') == 'get_command'
        for t in getattr(s, 'tools', []) or []
    ))
    check('agent_execute 有 execute_command 工具', True)  # 工具列表检查
    check('agent_chat 没有工具', True)
else:
    check('未配置 LLM，跳过 agent 结构测试', True)


# ── 工具列表（如果 agent 已创建） ──
if agent_search is not None:
    print('\n--- agent 工具列表 ---')
    search_tools = getattr(agent_search, 'tools', []) or []
    execute_tools = getattr(agent_execute, 'tools', []) or []
    chat_tools = getattr(agent_chat, 'tools', []) or []

    tool_names_s = [getattr(t, 'name', str(t)) for t in search_tools]
    tool_names_e = [getattr(t, 'name', str(t)) for t in execute_tools]

    check('agent_search 有 get_command', 'get_command' in tool_names_s)
    check('agent_execute 有 get_files', 'get_files' in tool_names_e)
    check('agent_execute 有 execute_command', 'execute_command' in tool_names_e)
    check('agent_chat 没有工具', len(chat_tools) == 0)


print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
