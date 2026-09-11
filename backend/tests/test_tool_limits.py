"""验证工具调用上限的行为。

两个关键点：
1) 限定一次时，工具只执行一次、模型往返只有 2 次（不再撞 GraphRecursionError）
2) **风险点**：exit_behavior='end' 在"超限那一轮还调用了别的工具"时会抛
   NotImplementedError。执行 agent 有两个工具（get_files / execute_command），
   必须确认这种并行调用不会把整个请求打崩。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import ToolCallLimitMiddleware  # noqa: E402
from langchain.messages import AIMessage, SystemMessage  # noqa: E402
from langchain.tools import tool  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

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


counter = {'files': 0, 'exec': 0, 'model': 0}


@tool
def get_files(config=None):
    """获取可用文件"""
    counter['files'] += 1
    return {'需要处理': ['backend/upload/a.mp4'], '输出目录': 'backend/download'}


@tool
def execute_command(command: str, config=None):
    """执行 ffmpeg 命令"""
    counter['exec'] += 1
    return {'command': command, 'flag': True, 'command_result': f'{command} 执行成功'}


class Scripted(BaseChatModel):
    """按脚本产生工具调用：script 是一串"每轮要调用的工具名列表"。"""

    script: list = None

    @property
    def _llm_type(self):
        return 'scripted'

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        counter['model'] += 1
        idx = counter['model'] - 1
        names = self.script[idx] if idx < len(self.script) else []
        calls = []
        for j, n in enumerate(names):
            args = {'command': 'ffmpeg -i a.mp4 out.mp4'} if n == 'execute_command' else {}
            calls.append({'name': n, 'args': args, 'id': f'c{idx}_{j}', 'type': 'tool_call'})
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=calls))])

    def bind_tools(self, tools, **kwargs):
        return self


def build(script, **limit_kwargs):
    mw = ToolCallLimitMiddleware(**limit_kwargs)
    return create_agent(model=Scripted(script=script),
                        system_prompt=SystemMessage(content='t'),
                        tools=[get_files, execute_command], middleware=[mw])


print('\n=== test_tool_limits.py ===\n')

# ── 1) 串行：先 get_files，再 execute_command ──
print('--- 串行调用（正常路径）---')
counter.update(files=0, exec=0, model=0)
agent = build([['get_files'], ['execute_command'], []],
              tool_name='execute_command', run_limit=1, thread_limit=1, exit_behavior='end')
try:
    res = agent.invoke({'messages': [{'role': 'user', 'content': '转换'}]})
    check('串行调用不抛异常', True)
    check('get_files 执行 1 次', counter['files'] == 1, str(counter))
    check('execute_command 执行 1 次', counter['exec'] == 1, str(counter))
except Exception as e:  # noqa: BLE001
    check('串行调用不抛异常', False, f'{type(e).__name__}: {str(e)[:120]}')

# ── 2) 模型在同一轮并行调用两个工具（风险场景） ──
print('\n--- 并行调用两个工具（风险场景）---')
counter.update(files=0, exec=0, model=0)
agent2 = build([['get_files', 'execute_command'], []],
               tool_name='execute_command', run_limit=1, thread_limit=1, exit_behavior='end')
try:
    agent2.invoke({'messages': [{'role': 'user', 'content': '转换'}]})
    check('并行调用两个工具时不抛 NotImplementedError', True)
    check('execute_command 仍只执行 1 次', counter['exec'] <= 1, str(counter))
except NotImplementedError as e:
    check('并行调用两个工具时不抛 NotImplementedError', False, str(e)[:160])
except Exception as e:  # noqa: BLE001
    check('并行调用时未出现未预期异常', False, f'{type(e).__name__}: {str(e)[:160]}')

# ── 3) 搜索 agent（单工具）：一定是一次调用、两次往返 ──
print('\n--- 搜索 agent 单次限制 ---')


@tool
def get_command(squry: str):
    """查询知识库"""
    search_calls['n'] += 1
    return ['来源[1]，-vf negate']


class AlwaysSearch(BaseChatModel):
    """每轮都要求调用 get_command —— 用来验证第 2 轮会被拦下并结束。"""

    @property
    def _llm_type(self):
        return 'always-search'

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        search_calls['model'] += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content='',
            tool_calls=[{'name': 'get_command', 'args': {'squry': 'q'},
                         'id': 'c1', 'type': 'tool_call'}],
        ))])

    def bind_tools(self, tools, **kwargs):
        return self


search_calls = {'n': 0, 'model': 0}
search_agent = create_agent(
    model=AlwaysSearch(),
    system_prompt=SystemMessage(content='t'),
    tools=[get_command],
    middleware=[ToolCallLimitMiddleware(tool_name='get_command', run_limit=1, thread_limit=1,
                                        exit_behavior='end')],
)
try:
    out = search_agent.invoke({'messages': [{'role': 'user', 'content': '反色'}]})
    check('搜索工具只执行 1 次', search_calls['n'] == 1, str(search_calls))
    check('搜索模型往返只有 2 次', search_calls['model'] == 2, str(search_calls))
    check('搜索 agent 正常返回', bool(out.get('messages')))
except Exception as e:  # noqa: BLE001
    check('搜索 agent 正常返回', False, f'{type(e).__name__}: {str(e)[:120]}')

# ── 4) 真实配置：agents.py 里的限额确实是一次 ──
print('\n--- 真实配置核对 ---')
import app.agents as a  # noqa: E402

check('SEARCH_TOOL_LIMIT == 1', a.SEARCH_TOOL_LIMIT == 1, str(a.SEARCH_TOOL_LIMIT))
check('EXECUTE_TOOL_LIMIT == 1', a.EXECUTE_TOOL_LIMIT == 1, str(a.EXECUTE_TOOL_LIMIT))
for name in ('_search_tool_limit', '_execute_tool_limit',
             '_probe_search_tool_limit', '_probe_execute_tool_limit'):
    mw = getattr(a, name)
    check(f'{name} run_limit=1 且 exit_behavior=end',
          mw.run_limit == 1 and mw.exit_behavior == 'end',
          f'run_limit={mw.run_limit} exit={mw.exit_behavior}')

# 提示词必须包含"每轮只调用一个工具"的约束（并行调用的防护）
check('执行提示词含单工具约束',
      '每轮只调用一个工具' in a._execute_prompt, a._execute_prompt[:60])
check('probe 执行提示词含单工具约束',
      '每轮只调用一个工具' in a._probe_execute_prompt)
check('检索提示词要求只调用一次',
      '只调用一次' in a._search_prompt, a._search_prompt[:60])
check('probe 检索提示词要求只调用一次',
      '只调用一次' in a._probe_search_prompt)

print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
