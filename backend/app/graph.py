"""执行图：ffmpeg（search→execute 循环）与 ffprobe 两套变体。

两套图的差异只有「agents、日志措辞、是否记录输出文件」，因此用 GraphSpec +
节点工厂参数化构建；同时保留原有模块级名称（exec_graph、probe_exec_graph、
search、probe_search、build_chat_prompt …）作为兼容入口。
"""
import os, json, logging
import app.agents as agents_mod
from app.agents import FFMPEG_SPEC, PROBE_SPEC
from app.tools import split_command, find_output_indexes
from langgraph.graph import START, END, StateGraph, MessagesState
from langchain.messages import ToolMessage, AnyMessage, AIMessage, HumanMessage

logger = logging.getLogger(__name__)

MAX_SEARCH_COUNT = 5
MAX_EXECUTE_COUNT = 3


class GraphCancelled(Exception):
    """用户主动停止任务(SSE 中断或 /api/chat/stop)。"""


class state(MessagesState):
    command: str = None
    result: str = ''
    command_result: str = ''
    history: list[AnyMessage] = None
    flag: bool = False
    output_file: str = ''
    search_count: int = 0
    execute_count: int = 0
    progress: list = None
    files: list = None
    context: str = ''
    # 多模态素材分析结论（画面/波形 + 参数建议），注入执行提示词
    media_analysis: str = ''
    # 用户显式写出的 ffmpeg 参数，必须原样保留
    explicit_params: list = None
    stop_event: object = None
    proc_box: object = None


def _check_cancelled(state: state):
    ev = state.get('stop_event')
    if ev is not None and ev.is_set():
        raise GraphCancelled('任务已被用户停止')


def _as_text(content) -> str:
    """把消息内容统一成字符串。

    约定：state['result'] 与 state['command_result'] 始终是 str。
    消息 content 在两种情况下不是 str：
      - 多模态模型返回 list[dict]（如 [{'type':'text','text':...}]）
      - 上游直接塞了非字符串对象
    此前 search 触达上限时把 result 写成 list，下游 build_chat_prompt 会把
    Python 列表字面量（"['...']"）拼进提示词；这里统一收敛为 str。
    """
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get('text') or item.get('content')
                if isinstance(text, str):
                    parts.append(text)
        return '\n'.join(p for p in parts if p)
    return str(content)


class GraphSpec:
    """一套执行图的声明式定义（ffmpeg / ffprobe 的差异集中在此）。"""

    def __init__(self, name, agent_prefix, ensure_attr, search_progress,
                 execute_progress, log_prefix, capture_output):
        self.name = name
        # agent 属性名前缀，如 'agent' → agent_search/agent_execute
        self.agent_prefix = agent_prefix
        # 模块级 ensure 函数名，如 'ensure_agents'
        self.ensure_attr = ensure_attr
        self.search_progress = search_progress
        self.execute_progress = execute_progress
        self.log_prefix = log_prefix
        # ffprobe 只读，不产生输出文件
        self.capture_output = capture_output
        self.compiled = None

    def ensure(self):
        return getattr(agents_mod, self.ensure_attr)()

    def agent(self, role: str):
        """按需取当前 agent 实例（rebuild_agents 后仍能拿到最新对象）。"""
        return getattr(agents_mod, f'{self.agent_prefix}_{role}')

    def log(self, msg: str):
        logger.info(f'{self.log_prefix}{msg}' if self.log_prefix else msg)


def _make_search_node(spec: GraphSpec):
    """构建 search / probe_search 节点。"""

    def _search(state: state):
        _check_cancelled(state)

        if state.get('progress') is not None:
            state['progress'].append(spec.search_progress)

        # 上限检查放在 LLM 配置检查之前：这是纯粹的计数判断，
        # 不需要 agents，也不该在未配置 LLM 时因它而抛错。
        if state.get('search_count', 0) >= MAX_SEARCH_COUNT:
            spec.log(f'查询次数已达上限（{MAX_SEARCH_COUNT} 次），跳过后续查询')
            return {
                **state,
                'result': f'已达到最大查询次数（{MAX_SEARCH_COUNT} 次），请基于现有信息继续',
            }

        if not spec.ensure():
            raise RuntimeError('LLM 未配置，请先在设置中填写模型信息')

        spec.log('执行查询')
        mes = state['messages']
        state['history'] = mes
        if state['flag'] or state.get('execute_count', 0) >= MAX_EXECUTE_COUNT:
            return state
        # 多轮对话：把历史上下文作为参考消息前置(不要求其回答历史问题)
        context_msgs = []
        if state.get('context'):
            context_msgs = [HumanMessage(
                content=f'对话历史（仅供参考，请结合当前问题理解用户意图）：\n{state["context"]}')]
        if state['command'] is not None:
            res = spec.agent('search').invoke(
                {'messages': [*context_msgs, *mes, HumanMessage(content=state.get('command_result', ''))]})
        else:
            res = spec.agent('search').invoke({'messages': [*context_msgs, *mes]})
        state['result'] = _as_text(res['messages'][-1].content)
        state['search_count'] = state.get('search_count', 0) + 1
        return state

    return _search


def _make_execute_node(spec: GraphSpec):
    """构建 execute / probe_execute 节点。"""

    def _execute(state: state):
        if not spec.ensure():
            raise RuntimeError('LLM 未配置，请先在设置中填写模型信息')

        _check_cancelled(state)

        if state.get('progress') is not None:
            state['progress'].append(spec.execute_progress)

        spec.log('执行命令')
        state['execute_count'] = state.get('execute_count', 0) + 1
        spec.log(f'执行次数：{state["execute_count"]}/{MAX_EXECUTE_COUNT}')

        history_msgs = state.get('history') or []
        user_question = history_msgs[0].content if history_msgs else ''
        execute_prompt = (
            f'用户问题：{user_question}\n\n'
            f'知识库检索结果：{state.get("result", "")}'
        )
        # 多模态素材分析（画面/波形 + 参数建议）——让执行 agent 看得见素材，
        # 而不是只凭用户文字描述猜参数。
        if state.get('media_analysis'):
            execute_prompt += f'\n\n素材分析（由多模态模型根据实际画面/波形得出，请据此确定参数）：\n{state["media_analysis"]}'
        # 用户直接写出的参数必须原样使用，不允许被"优化"掉
        if state.get('explicit_params'):
            execute_prompt += (
                '\n\n用户显式指定的参数（**必须原样出现在命令中，不得更改取值或省略**）：'
                + ' '.join(state['explicit_params'])
            )
        if state.get('context'):
            execute_prompt += f'\n\n对话历史（仅供参考）：\n{state["context"]}'

        configurable = {
            'selected_files': state.get('files') or [],
            'stop_event': state.get('stop_event'),
            'proc': state.get('proc_box'),
        }
        # 仅 ffmpeg 需要进度回调（ffprobe 无转码进度）
        if spec.capture_output:
            configurable['progress'] = state.get('progress')

        res = spec.agent('execute').invoke(
            {'messages': [HumanMessage(content=execute_prompt)]},
            config={'configurable': configurable},
        )

        for msg in reversed(res['messages']):
            if isinstance(msg, ToolMessage):
                try:
                    data = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                    if isinstance(data, dict):
                        state['command'] = _as_text(data.get('command', state.get('command', '')))
                        state['flag'] = data.get('flag', False)
                        state['command_result'] = _as_text(data.get('command_result', ''))
                        if spec.capture_output and data.get('flag') and data.get('command'):
                            parts = split_command(data['command'])
                            idxs = find_output_indexes(parts)
                            if idxs:
                                state['output_file'] = os.path.basename(parts[idxs[-1]])
                except Exception as e:
                    spec.log(f'解析 execute 工具返回失败：{e}')
                break
        return state

    return _execute


def which_continue_exec(state: state):
    ev = state.get('stop_event')
    if ev is not None and ev.is_set():
        branch = END
    elif not (state.get('files') or []):
        # 纯知识问答(未选择文件)：检索后直接结束,不进入命令执行阶段
        branch = END
    elif state.get('flag', False):
        branch = END
    elif state.get('execute_count', 0) >= MAX_EXECUTE_COUNT:
        logger.info(f'执行次数已达上限（{MAX_EXECUTE_COUNT} 次），强制结束')
        branch = END
    else:
        branch = 'execute'
    logger.info(f'路由决策：{branch}')
    return branch


def _build_graph(spec: GraphSpec):
    """构建并编译一张执行图（模块加载时各调用一次，请求内复用）。"""
    workflow = StateGraph(state_schema=state)
    workflow.add_node('search', _make_search_node(spec))
    workflow.add_node('execute', _make_execute_node(spec))
    workflow.add_edge(START, 'search')
    workflow.add_edge('execute', 'search')
    workflow.add_conditional_edges(
        'search',
        which_continue_exec,
        {END: END, 'execute': 'execute'},
    )
    return workflow.compile()


def _run_graph(spec: GraphSpec, question: str, progress=None, files=None, context='',
               stop_event=None, proc_box=None, media_analysis='', explicit_params=None) -> dict:
    spec.log(f'开始执行，用户问题：{question}')
    return spec.compiled.invoke({
        "messages": [HumanMessage(content=question)],
        "command": None,
        "result": "",
        "command_result": "",
        "history": [],
        "flag": False,
        "output_file": "",
        "search_count": 0,
        "execute_count": 0,
        "progress": progress,
        "files": files or [],
        "context": context or '',
        "media_analysis": media_analysis or '',
        "explicit_params": explicit_params or [],
        "stop_event": stop_event,
        "proc_box": proc_box,
    })


def _build_chat_prompt(state: dict, include_output_file: bool = True) -> str:
    history_msgs = state.get('history') or []
    user_question = history_msgs[0].content if history_msgs else ''
    prompt = (
        f'用户问题：{user_question}\n\n'
        f'知识库检索结果：{state.get("result", "")}\n'
    )
    if state.get('context'):
        prompt += f'\n对话历史（仅供参考）：\n{state["context"]}'
    if state.get('media_analysis'):
        prompt += f'\n素材分析（多模态结论，可在回答里引用）：\n{state["media_analysis"]}'
    if state.get('explicit_params'):
        prompt += f'\n用户显式指定的参数：{" ".join(state["explicit_params"])}'
    if state.get('command'):
        prompt += f'\n执行的命令：{state["command"]}'
    if state.get('command_result'):
        prompt += f'\n命令执行结果：{state["command_result"]}'
    if include_output_file and state.get('output_file'):
        prompt += f'\n输出文件：{state["output_file"]}'
    if state.get('files'):
        prompt += f'\n选择处理的文件：{", ".join(os.path.basename(f) for f in state["files"])}'
    return prompt


# ── 变体声明 ──

FFMPEG_GRAPH = GraphSpec(
    name='ffmpeg',
    agent_prefix='agent',
    ensure_attr='ensure_agents',
    search_progress='正在查询知识库...',
    execute_progress='正在执行命令...',
    log_prefix='',
    capture_output=True,
)

PROBE_GRAPH = GraphSpec(
    name='ffprobe',
    agent_prefix='agent_probe',
    ensure_attr='ensure_probe_agents',
    search_progress='正在查询 ffprobe 知识库...',
    execute_progress='正在执行 ffprobe 命令...',
    log_prefix='ffprobe ',
    capture_output=False,
)

# 模块加载时各编译一次,避免每个请求重复 compile()
FFMPEG_GRAPH.compiled = _build_graph(FFMPEG_GRAPH)
PROBE_GRAPH.compiled = _build_graph(PROBE_GRAPH)


# ── 公开入口（保持既有签名）──

def exec_graph(question: str, progress: list = None, files: list = None, context: str = '',
               stop_event=None, proc_box=None, media_analysis: str = '',
               explicit_params: list = None) -> dict:
    return _run_graph(FFMPEG_GRAPH, question, progress, files, context, stop_event, proc_box,
                      media_analysis, explicit_params)


def probe_exec_graph(question: str, progress: list = None, files: list = None, context: str = '',
                     stop_event=None, proc_box=None, media_analysis: str = '',
                     explicit_params: list = None) -> dict:
    return _run_graph(PROBE_GRAPH, question, progress, files, context, stop_event, proc_box,
                      media_analysis, explicit_params)


def build_chat_prompt(state: dict) -> str:
    """根据执行状态构建 chat agent 的输入提示。"""
    return _build_chat_prompt(state, include_output_file=True)


def build_probe_chat_prompt(state: dict) -> str:
    """根据 ffprobe 执行状态构建 chat agent 的输入提示（无输出文件概念）。"""
    return _build_chat_prompt(state, include_output_file=False)


# 兼容既有调用方与测试的名称
search = _make_search_node(FFMPEG_GRAPH)
execute = _make_execute_node(FFMPEG_GRAPH)
probe_search = _make_search_node(PROBE_GRAPH)
probe_execute = _make_execute_node(PROBE_GRAPH)

exec_workflow = FFMPEG_GRAPH.compiled
probe_exec_workflow = PROBE_GRAPH.compiled


def chat_agent_for(is_probe: bool):
    """返回对应变体的 chat agent（供 __main__ 与路由使用）。"""
    agents_mod.ensure_probe_agents() if is_probe else agents_mod.ensure_agents()
    return agents_mod.agent_probe_chat if is_probe else agents_mod.agent_chat


def get_chat_agent(kind: str = ''):
    """按 kind（'' = ffmpeg，'ffprobe' = ffprobe）取 chat agent。

    在请求期读取模块属性，因此 rebuild_agents() 之后拿到的是最新实例。
    """
    return chat_agent_for(kind.strip() == 'ffprobe')


if __name__ == '__main__':
    import sys
    if not FFMPEG_GRAPH.ensure():
        print('错误：LLM 未配置，请在页面右上角 ⚙️ 设置中填写模型信息'
              '（配置保存在 backend/data/llm_settings.json）')
        sys.exit(1)
    if not PROBE_GRAPH.ensure():
        print('错误：ffprobe agent 创建失败')
        sys.exit(1)
    args = sys.argv[1:]
    is_probe = bool(args) and args[0] == '--probe'
    if is_probe:
        args = args[1:]
    q = ' '.join(args) or ('如何查看视频的分辨率和编码信息？' if is_probe else '如何将图片反色？')
    exec_state = probe_exec_graph(q) if is_probe else exec_graph(q)
    prompt = build_probe_chat_prompt(exec_state) if is_probe else build_chat_prompt(exec_state)

    res = chat_agent_for(is_probe).invoke({'messages': [HumanMessage(content=prompt)]})
    reply = res['messages'][-1].content if 'messages' in res else str(res)
    logger.info(f"AI: {reply}")
    if exec_state.get('output_file'):
        logger.info(f"输出文件: {exec_state['output_file']}")
