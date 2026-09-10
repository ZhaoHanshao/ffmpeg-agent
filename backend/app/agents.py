"""六个 LangChain agent（ffmpeg / ffprobe 各三个）。

结构说明：
- 两套变体（ffmpeg / ffprobe）的差异只有「提示词、检索工具、执行工具、执行工具名」，
  因此用 AgentSpec + _create 参数化构建，避免三份复制粘贴。
- 为兼容既有测试与调用方，保留了全部原有的模块级名称
  （`_search_prompt`、`agent_search`、`_build_agents`、`_search_tool_limit` 等），
  它们现在只是参数化结果或别名，语义与之前一致。
"""
from app.model import get_model, is_configured
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from app.tools import get_command, get_files, execute_command, get_probe_command, execute_probe_command
from langchain.messages import SystemMessage

# 检索工具单次任务内的调用次数上限 / 执行工具只允许一次
SEARCH_TOOL_LIMIT = 5
EXECUTE_TOOL_LIMIT = 1


# ── 提示词 ──

_search_prompt = (
    '你是一个 FFmpeg 知识库查询助手。'
    '你的任务是根据用户的 FFmpeg 相关问题，使用 get_command 工具查询知识库，知识库为英文知识库，用英文进行查询，'
    '获取相关的 FFmpeg 命令和文档片段，然后将查询结果整理后返回。'
    '只需要返回查询到的 FFmpeg 命令和参数解释，不要添加额外说明。'
    '必须原样保留检索结果中出现的命令与参数拼写，不要凭记忆补全或改写选项名；'
    '若检索结果里没有能直接回答问题的内容，就明确说明未检索到，不要编造命令。'
)

_execute_prompt = (
    '你是一个 FFmpeg 命令执行专家。你的职责是根据用户问题和知识库内容，'
    '生成并执行正确的 ffmpeg 命令。\n\n'
    '规则：\n'
    '1. 只能调用 execute_command 执行以 ffmpeg 开头的命令\n'
    '2. 先用 get_files 查看可用的输入文件\n'
    '3. 输入文件路径用 get_files 返回的实际路径\n'
    '4. 只处理 get_files 返回的文件，不要处理其他文件\n'
    '5. 输出文件只写文件名（如 output.webp），工具会自动重定向到输出目录\n'
    '6. 输出文件命名：如果用户没有明确指定输出文件名，默认命名为"原文件名去掉扩展名 + _output + 目标扩展名"'
    '（如 demo.mp4 → demo_output.mp4；格式转换时使用目标格式的扩展名，如 demo.webm 转 mp4 → demo_output.mp4）\n'
    '7. 一个任务只执行一次 ffmpeg，不要重复尝试多种参数\n'
    '8. 如果 ffmpeg 成功（返回 flag=true），立即结束，不要继续尝试其他命令\n'
    '9. 不要执行 convert、dwebp、apt-get、sudo、pip、python、ls、pwd、find、which 等非 ffmpeg 命令\n'
    '10. 如果 ffmpeg 执行失败：分析失败原因；仅当原因明确且有把握修正时（如参数拼写、路径、格式兼容问题），'
    '基于错误信息修正后重试一次；否则直接结束并返回失败原因，不要盲目反复重试。'
)

_chat_prompt = (
    '你是一个 FFmpeg 助手。你的任务是根据用户的原始问题、知识库检索结果和执行结果，'
    '给用户一个完整、简洁的回答。\n\n'
    '要求：\n'
    '1. 先直接回答用户的问题\n'
    '2. 如果本次执行了命令且成功，说明使用了什么命令、输出文件是什么\n'
    '3. 如果没有执行命令(纯知识问答)，直接根据知识库检索结果回答，不要声称执行了任何操作\n'
    '4. 如果失败，说明失败原因和建议\n'
    '5. 输出文件可以在浏览器中通过 /api/output/文件名 下载\n'
    '6. 适当引用执行日志中的关键信息\n'
    '7. 使用中文、语气友好'
)

_probe_search_prompt = (
    '你是一个 FFprobe 知识库查询助手。'
    '你的任务是根据用户的 FFprobe 相关问题，使用 get_probe_command 工具查询知识库，知识库为英文知识库，用英文进行查询，'
    '获取相关的 FFprobe 命令和文档片段，然后将查询结果整理后返回。'
    '只需要返回查询到的 FFprobe 命令和参数解释，不要添加额外说明。'
    '必须原样保留检索结果中出现的命令与参数拼写，不要凭记忆补全或改写选项名；'
    '若检索结果里没有能直接回答问题的内容，就明确说明未检索到，不要编造命令。'
)

_probe_execute_prompt = (
    '你是一个 FFprobe 命令执行专家。你的职责是根据用户问题和知识库内容，'
    '生成并执行正确的 ffprobe 命令。\n\n'
    '规则：\n'
    '1. 只能调用 execute_probe_command 执行以 ffprobe 开头的命令\n'
    '2. 先用 get_files 查看可用的输入文件\n'
    '3. 输入文件路径用 get_files 返回的实际路径\n'
    '4. 只处理 get_files 返回的文件，不要处理其他文件\n'
    '5. ffprobe 是只读分析工具，输出打印到终端即可，不要添加输出文件参数\n'
    '6. 查看媒体信息时使用 -show_format、-show_streams、-show_packets 等参数，常用组合：ffprobe -v error -show_format -show_streams\n'
    '7. 一个任务只执行一次 ffprobe，不要重复尝试多种参数\n'
    '8. 如果 ffprobe 成功（返回 flag=true），立即结束，不要继续尝试其他命令\n'
    '9. 不要执行 ffmpeg、convert、apt-get、sudo、pip、python、ls、pwd、find、which 等非 ffprobe 命令\n'
    '10. 如果 ffprobe 执行失败：分析失败原因；仅当原因明确且有把握修正时（如参数拼写、路径问题），'
    '基于错误信息修正后重试一次；否则直接结束并返回失败原因，不要盲目反复重试。'
)

_probe_chat_prompt = (
    '你是一个 FFprobe 助手。你的任务是根据用户的原始问题、知识库检索结果和执行结果，'
    '给用户一个完整、简洁的回答。\n\n'
    '要求：\n'
    '1. 先直接回答用户的问题\n'
    '2. 如果本次执行了命令且成功，说明使用了什么命令，并把 ffprobe 输出的关键信息'
    '（格式、编码、分辨率、码率、时长等）整理成易读的说明\n'
    '3. 如果没有执行命令(纯知识问答)，直接根据知识库检索结果回答，不要声称执行了任何操作\n'
    '4. 如果失败，说明失败原因和建议\n'
    '5. 适当引用执行日志中的关键信息\n'
    '6. 使用中文、语气友好'
)


# ── 工具调用上限中间件 ──

_search_tool_limit = ToolCallLimitMiddleware(
    tool_name="get_command",
    run_limit=SEARCH_TOOL_LIMIT,
    thread_limit=SEARCH_TOOL_LIMIT,
)

_execute_tool_limit = ToolCallLimitMiddleware(
    tool_name="execute_command",
    run_limit=EXECUTE_TOOL_LIMIT,
    thread_limit=EXECUTE_TOOL_LIMIT,
)

_probe_search_tool_limit = ToolCallLimitMiddleware(
    tool_name="get_probe_command",
    run_limit=SEARCH_TOOL_LIMIT,
    thread_limit=SEARCH_TOOL_LIMIT,
)

_probe_execute_tool_limit = ToolCallLimitMiddleware(
    tool_name="execute_probe_command",
    run_limit=EXECUTE_TOOL_LIMIT,
    thread_limit=EXECUTE_TOOL_LIMIT,
)


class AgentSpec:
    """一套（检索/执行/回答）agent 的声明式定义。

    两套变体的唯一差异都在这里，graph.py 也复用同一份声明，
    避免"改一处忘一处"造成 ffmpeg 与 ffprobe 行为漂移。
    """

    def __init__(self, name, search_prompt, execute_prompt, chat_prompt,
                 search_tool, execute_tool, search_limit, execute_limit,
                 ensure_fn=None):
        self.name = name
        self.search_prompt = search_prompt
        self.execute_prompt = execute_prompt
        self.chat_prompt = chat_prompt
        self.search_tool = search_tool
        self.execute_tool = execute_tool
        self.search_limit = search_limit
        self.execute_limit = execute_limit
        self._ensure_fn = ensure_fn

    @property
    def ensure(self):
        """惰性绑定的 ensure 函数（在模块末尾完成绑定，避免前向引用）。"""
        return self._ensure_fn

    def agents(self):
        """返回 (search_agent, execute_agent, chat_agent)；LLM 未配置时返回 (None, None, None)。"""
        return _create(self)


FFMPEG_SPEC = AgentSpec(
    name='ffmpeg',
    search_prompt=_search_prompt,
    execute_prompt=_execute_prompt,
    chat_prompt=_chat_prompt,
    search_tool=get_command,
    execute_tool=execute_command,
    search_limit=_search_tool_limit,
    execute_limit=_execute_tool_limit,
)

PROBE_SPEC = AgentSpec(
    name='ffprobe',
    search_prompt=_probe_search_prompt,
    execute_prompt=_probe_execute_prompt,
    chat_prompt=_probe_chat_prompt,
    search_tool=get_probe_command,
    execute_tool=execute_probe_command,
    search_limit=_probe_search_tool_limit,
    execute_limit=_probe_execute_tool_limit,
)


def _create(spec: AgentSpec):
    """按 spec 构建三个 agent。"""
    m = get_model()
    if m is None:
        return None, None, None
    return (
        create_agent(
            model=m,
            system_prompt=SystemMessage(content=spec.search_prompt),
            tools=[spec.search_tool],
            middleware=[spec.search_limit],
        ),
        create_agent(
            model=m,
            system_prompt=SystemMessage(content=spec.execute_prompt),
            tools=[get_files, spec.execute_tool],
            middleware=[spec.execute_limit],
        ),
        create_agent(
            model=m,
            system_prompt=SystemMessage(content=spec.chat_prompt),
            tools=[],
        ),
    )


# ── 模块级 agent 句柄（None 表示 LLM 未配置；rebuild_agents 会重新赋值）──

agent_search, agent_execute, agent_chat = None, None, None
agent_probe_search, agent_probe_execute, agent_probe_chat = None, None, None


def ensure_agents():
    global agent_search, agent_execute, agent_chat
    if not is_configured():
        return False
    if agent_search is None:
        agent_search, agent_execute, agent_chat = _create(FFMPEG_SPEC)
    return agent_search is not None


def ensure_probe_agents():
    global agent_probe_search, agent_probe_execute, agent_probe_chat
    if not is_configured():
        return False
    if agent_probe_search is None:
        agent_probe_search, agent_probe_execute, agent_probe_chat = _create(PROBE_SPEC)
    return agent_probe_search is not None


# 绑定 ensure 函数（供 graph.py 通过 spec 取用，无需再硬编码 ensure_agents/ensure_probe_agents）
FFMPEG_SPEC._ensure_fn = ensure_agents
PROBE_SPEC._ensure_fn = ensure_probe_agents


# ── 兼容既有调用方的别名 ──

def _build_agents():
    return _create(FFMPEG_SPEC)


def _build_probe_agents():
    return _create(PROBE_SPEC)
