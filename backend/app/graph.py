import os, shlex, json, logging
from app.agents import ensure_agents, ensure_probe_agents
from langgraph.graph import START, END, StateGraph, MessagesState
from langchain.messages import ToolMessage, AnyMessage, AIMessage, HumanMessage

logger = logging.getLogger(__name__)


class state(MessagesState):
    command: str = None
    result: str = ''
    command_result: str = ''
    history: list[AnyMessage] = None
    flag: bool = False
    output_file: str = ''
    search_count: int = 0
    progress: list = None


def search(state: state):
    if not ensure_agents():
        raise RuntimeError('LLM 未配置，请先在设置中填写模型信息')

    from app.agents import agent_search

    if state.get('progress') is not None:
        state['progress'].append('正在查询知识库...')

    if state.get('search_count', 0) >= 10:
        logger.info('查询次数已达上限（10 次），跳过后续查询')
        return {
            **state,
            'result': ['已达到最大查询次数（10 次），请基于现有信息继续'],
        }
    logger.info('执行查询')
    mes = state['messages']
    state['history'] = mes
    if state['flag']:
        return state
    if state['command'] is not None:
        res = agent_search.invoke({'messages': [*mes, HumanMessage(content=state['command_result'])]})
    else:
        res = agent_search.invoke({'messages': mes})
    state['result'] = res['messages'][-1].content
    state['search_count'] = state.get('search_count', 0) + 1
    return state


def execute(state: state):
    if not ensure_agents():
        raise RuntimeError('LLM 未配置，请先在设置中填写模型信息')

    from app.agents import agent_execute

    if state.get('progress') is not None:
        state['progress'].append('正在执行命令...')

    logger.info('执行命令')
    user_question = state['history'][0].content if state.get('history') else ''
    execute_prompt = (
        f'用户问题：{user_question}\n\n'
        f'知识库检索结果：{state["result"]}'
    )
    res = agent_execute.invoke({'messages': [HumanMessage(content=execute_prompt)]})
    for msg in reversed(res['messages']):
        if isinstance(msg, ToolMessage):
            try:
                data = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if isinstance(data, dict):
                    state['command'] = data.get('command', state.get('command', ''))
                    state['flag'] = data.get('flag', False)
                    state['command_result'] = data.get('command_result', '')
                    if data.get('flag') and data.get('command'):
                        parts = shlex.split(data['command'])
                        for part in reversed(parts):
                            if not part.startswith('-'):
                                state['output_file'] = os.path.basename(part)
                                break
            except Exception:
                pass
            break
    return state


def which_continue_exec(state: state):
    branch = END if state['flag'] else 'execute'
    logger.info(f'路由决策：{branch}')
    return branch


# ── 执行图（search + execute 循环，不含 chat） ──
exec_workflow = StateGraph(state_schema=state)
exec_workflow.add_node('search', search)
exec_workflow.add_node('execute', execute)
exec_workflow.add_edge(START, 'search')
exec_workflow.add_edge('execute', 'search')
exec_workflow.add_conditional_edges(
    'search',
    which_continue_exec,
    {END: END, 'execute': 'execute'},
)


def exec_graph(question: str, progress: list = None) -> dict:
    logger.info(f'开始执行，用户问题：{question}')
    compiled = exec_workflow.compile()
    result = compiled.invoke({
        "messages": [HumanMessage(content=question)],
        "command": None,
        "result": "",
        "command_result": "",
        "history": [],
        "flag": False,
        "output_file": "",
        "search_count": 0,
        "progress": progress,
    })
    return result


def build_chat_prompt(state: dict) -> str:
    """根据执行状态构建 chat agent 的输入提示。"""
    user_question = state['history'][0].content if state.get('history') else ''
    prompt = (
        f'用户问题：{user_question}\n\n'
        f'知识库检索结果：{state.get("result", "")}\n'
    )
    if state.get('command'):
        prompt += f'\n执行的命令：{state["command"]}'
    if state.get('command_result'):
        prompt += f'\n命令执行结果：{state["command_result"]}'
    if state.get('output_file'):
        prompt += f'\n输出文件：{state["output_file"]}'
    return prompt


# ── ffprobe 执行图（probe_search + probe_execute 循环，不含 chat） ──

def probe_search(state: state):
    if not ensure_probe_agents():
        raise RuntimeError('LLM 未配置，请先在设置中填写模型信息')

    from app.agents import agent_probe_search

    if state.get('progress') is not None:
        state['progress'].append('正在查询 ffprobe 知识库...')

    if state.get('search_count', 0) >= 10:
        logger.info('ffprobe 查询次数已达上限（10 次），跳过后续查询')
        return {
            **state,
            'result': ['已达到最大查询次数（10 次），请基于现有信息继续'],
        }
    logger.info('执行 ffprobe 查询')
    mes = state['messages']
    state['history'] = mes
    if state['flag']:
        return state
    if state['command'] is not None:
        res = agent_probe_search.invoke({'messages': [*mes, HumanMessage(content=state['command_result'])]})
    else:
        res = agent_probe_search.invoke({'messages': mes})
    state['result'] = res['messages'][-1].content
    state['search_count'] = state.get('search_count', 0) + 1
    return state


def probe_execute(state: state):
    if not ensure_probe_agents():
        raise RuntimeError('LLM 未配置，请先在设置中填写模型信息')

    from app.agents import agent_probe_execute

    if state.get('progress') is not None:
        state['progress'].append('正在执行 ffprobe 命令...')

    logger.info('执行 ffprobe 命令')
    user_question = state['history'][0].content if state.get('history') else ''
    execute_prompt = (
        f'用户问题：{user_question}\n\n'
        f'知识库检索结果：{state["result"]}'
    )
    res = agent_probe_execute.invoke({'messages': [HumanMessage(content=execute_prompt)]})
    for msg in reversed(res['messages']):
        if isinstance(msg, ToolMessage):
            try:
                data = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                if isinstance(data, dict):
                    state['command'] = data.get('command', state.get('command', ''))
                    state['flag'] = data.get('flag', False)
                    state['command_result'] = data.get('command_result', '')
            except Exception:
                pass
            break
    return state


probe_exec_workflow = StateGraph(state_schema=state)
probe_exec_workflow.add_node('search', probe_search)
probe_exec_workflow.add_node('execute', probe_execute)
probe_exec_workflow.add_edge(START, 'search')
probe_exec_workflow.add_edge('execute', 'search')
probe_exec_workflow.add_conditional_edges(
    'search',
    which_continue_exec,
    {END: END, 'execute': 'execute'},
)


def probe_exec_graph(question: str, progress: list = None) -> dict:
    logger.info(f'开始执行 ffprobe 任务，用户问题：{question}')
    compiled = probe_exec_workflow.compile()
    result = compiled.invoke({
        "messages": [HumanMessage(content=question)],
        "command": None,
        "result": "",
        "command_result": "",
        "history": [],
        "flag": False,
        "output_file": "",
        "search_count": 0,
        "progress": progress,
    })
    return result


def build_probe_chat_prompt(state: dict) -> str:
    """根据 ffprobe 执行状态构建 chat agent 的输入提示。"""
    user_question = state['history'][0].content if state.get('history') else ''
    prompt = (
        f'用户问题：{user_question}\n\n'
        f'知识库检索结果：{state.get("result", "")}\n'
    )
    if state.get('command'):
        prompt += f'\n执行的命令：{state["command"]}'
    if state.get('command_result'):
        prompt += f'\n命令执行结果：{state["command_result"]}'
    return prompt


if __name__ == '__main__':
    import sys
    if not ensure_agents():
        print('错误：LLM 未配置，请先设置 MODEL_NAME、BASE_URL、API_KEY')
        sys.exit(1)
    if not ensure_probe_agents():
        print('错误：ffprobe agent 创建失败')
        sys.exit(1)
    args = sys.argv[1:]
    is_probe = bool(args) and args[0] == '--probe'
    if is_probe:
        args = args[1:]
    q = ' '.join(args) or ('如何查看视频的分辨率和编码信息？' if is_probe else '如何将图片反色？')
    exec_state = probe_exec_graph(q) if is_probe else exec_graph(q)
    prompt = build_probe_chat_prompt(exec_state) if is_probe else build_chat_prompt(exec_state)

    from app.agents import agent_chat, agent_probe_chat
    chat_agent = agent_probe_chat if is_probe else agent_chat
    res = chat_agent.invoke({'messages': [HumanMessage(content=prompt)]})
    reply = res['messages'][-1].content if 'messages' in res else str(res)
    logger.info(f"AI: {reply}")
    if exec_state.get('output_file'):
        logger.info(f"输出文件: {exec_state['output_file']}")