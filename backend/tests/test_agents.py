import os, sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv
load_dotenv()

from app import agents as agents_mod
from app.agents import (
    _search_prompt, _execute_prompt, _chat_prompt,
    _probe_search_prompt, _probe_execute_prompt, _probe_chat_prompt,
    ensure_agents, ensure_probe_agents, _build_agents, _build_probe_agents,
    _search_tool_limit, _probe_search_tool_limit,
    _execute_tool_limit, _probe_execute_tool_limit,
)
from app.model import is_configured
from langchain.agents.middleware import ToolCallLimitMiddleware

# 注意：agent_search 等是模块级变量，ensure_agents()/_build_agents() 会重新绑定它们。
# `from app.agents import agent_search` 拿到的是导入瞬间的快照（通常是 None），
# 因此这里统一用 agents_mod.agent_search 读取实时值。

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
    check('agent_probe_search 不为 None', agents_mod.agent_probe_search is not None)
    check('agent_probe_execute 不为 None', agents_mod.agent_probe_execute is not None)
    check('agent_probe_chat 不为 None', agents_mod.agent_probe_chat is not None)
else:
    check('未配置时返回 False', result is False)
    check('agent_probe_search 为 None', agents_mod.agent_probe_search is None)
    check('agent_probe_execute 为 None', agents_mod.agent_probe_execute is None)
    check('agent_probe_chat 为 None', agents_mod.agent_probe_chat is None)


# ── _build_probe_agents ──
print('\n--- _build_probe_agents ---')
probe_agents = _build_probe_agents()
if probe_agents[0] is not None:
    check('返回三个 agent', len(probe_agents) == 3)
else:
    check('未配置 LLM，跳过 ffprobe agent 结构测试', True)

# 工具装配的权威来源是 AgentSpec（create_agent 返回的是 CompiledStateGraph，
# 并不暴露 .tools，此前对这些属性的断言只会在"未配置"分支下空过）。
print('\n--- 工具装配（来自 AgentSpec） ---')
from app.agents import FFMPEG_SPEC, PROBE_SPEC
from app.tools import get_command, get_files, execute_command, get_probe_command, execute_probe_command

check('ffmpeg search 绑定 get_command', FFMPEG_SPEC.search_tool is get_command)
check('ffmpeg execute 绑定 execute_command', FFMPEG_SPEC.execute_tool is execute_command)
check('ffprobe search 绑定 get_probe_command', PROBE_SPEC.search_tool is get_probe_command)
check('ffprobe execute 绑定 execute_probe_command', PROBE_SPEC.execute_tool is execute_probe_command)
check('两套 spec 的提示词互相独立',
      FFMPEG_SPEC.search_prompt != PROBE_SPEC.search_prompt
      and FFMPEG_SPEC.execute_prompt != PROBE_SPEC.execute_prompt
      and FFMPEG_SPEC.chat_prompt != PROBE_SPEC.chat_prompt)
check('get_files 可用（执行 agent 共享）', get_files.name == 'get_files')


# ── ensure_agents ──
print('\n--- ensure_agents ---')
result = ensure_agents()
check('ensure_agents 返回布尔值', isinstance(result, bool))
if is_configured():
    check('已配置时返回 True', result is True)
    check('agent_search 不为 None', agents_mod.agent_search is not None)
    check('agent_execute 不为 None', agents_mod.agent_execute is not None)
    check('agent_chat 不为 None', agents_mod.agent_chat is not None)
else:
    check('未配置时返回 False', result is False)
    check('agent_search 为 None', agents_mod.agent_search is None)
    check('agent_execute 为 None', agents_mod.agent_execute is None)
    check('agent_chat 为 None', agents_mod.agent_chat is None)


# ── _build_agents ──
print('\n--- _build_agents ---')
agents = _build_agents()
if agents[0] is not None:
    check('返回三个 agent', len(agents) == 3)
else:
    check('未配置 LLM，跳过 agent 结构测试', True)


# ── 工具列表（如果 agent 已创建） ──
# 已由上面的「工具装配（来自 AgentSpec）」覆盖：create_agent 返回 CompiledStateGraph，
# 不暴露 .tools，因此这里不再重复做无效的属性检查。


print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
