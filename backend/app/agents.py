from app.model import get_model, is_configured
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from app.tools import get_command, get_files, execute_command, get_probe_command, execute_probe_command
from langchain.messages import SystemMessage

_search_tool_limit = ToolCallLimitMiddleware(
    tool_name="get_command",
    run_limit=5,
    thread_limit=5,
)

_execute_tool_limit = ToolCallLimitMiddleware(
    tool_name="execute_command",
    run_limit=1,
    thread_limit=1,
)

_search_prompt = (
    '你是一个 FFmpeg 知识库查询助手。'
    '你的任务是根据用户的 FFmpeg 相关问题，使用 get_command 工具查询知识库，知识库为英文知识库，用英文进行查询，'
    '获取相关的 FFmpeg 命令和文档片段，然后将查询结果整理后返回。'
    '只需要返回查询到的 FFmpeg 命令和参数解释，不要添加额外说明。'
)

_execute_prompt = (
    '你是一个 FFmpeg 命令执行专家。你的职责是根据用户问题和知识库内容，'
    '生成并执行正确的 ffmpeg 命令。\n\n'
    '规则：\n'
    '1. 只能调用 execute_command 执行以 ffmpeg 开头的命令\n'
    '2. 先用 get_files 查看可用的输入文件\n'
    '3. 输入文件路径用 get_files 返回的实际路径\n'
    '4. 输出文件只写文件名（如 output.webp），工具会自动重定向到输出目录\n'
    '5. 一个任务只执行一次 ffmpeg，不要重复尝试多种参数\n'
    '6. 如果 ffmpeg 成功（返回 flag=true），立即结束，不要继续尝试其他命令\n'
    '7. 不要执行 convert、dwebp、apt-get、sudo、pip、python、ls、pwd、find、which 等非 ffmpeg 命令\n'
    '8. 如果 ffmpeg 执行失败，不要重试，直接返回失败原因。'
)

_chat_prompt = (
    '你是一个 FFmpeg 助手。你的任务是根据用户的原始问题、知识库检索结果和执行结果，'
    '给用户一个完整、简洁的回答。\n\n'
    '要求：\n'
    '1. 先直接回答用户的问题——告诉用户是否已成功完成\n'
    '2. 如果成功，说明使用了什么命令、输出文件是什么\n'
    '3. 如果失败，说明失败原因和建议\n'
    '4. 输出文件可以在浏览器中通过 /api/output/文件名 下载\n'
    '5. 适当引用执行日志中的关键信息\n'
    '6. 使用中文、语气友好'
)


def _build_agents():
    m = get_model()
    if m is None:
        return None, None, None
    return (
        create_agent(model=m, system_prompt=SystemMessage(content=_search_prompt), tools=[get_command], middleware=[_search_tool_limit]),
        create_agent(model=m, system_prompt=SystemMessage(content=_execute_prompt), tools=[get_files, execute_command], middleware=[_execute_tool_limit]),
        create_agent(model=m, system_prompt=SystemMessage(content=_chat_prompt), tools=[]),
    )


agent_search, agent_execute, agent_chat = None, None, None


def ensure_agents():
    global agent_search, agent_execute, agent_chat
    if not is_configured():
        return False
    if agent_search is None:
        agent_search, agent_execute, agent_chat = _build_agents()
    return agent_search is not None


# ── ffprobe agents ──

_probe_search_tool_limit = ToolCallLimitMiddleware(
    tool_name="get_probe_command",
    run_limit=5,
    thread_limit=5,
)

_probe_execute_tool_limit = ToolCallLimitMiddleware(
    tool_name="execute_probe_command",
    run_limit=1,
    thread_limit=1,
)

_probe_search_prompt = (
    '你是一个 FFprobe 知识库查询助手。'
    '你的任务是根据用户的 FFprobe 相关问题，使用 get_probe_command 工具查询知识库，知识库为英文知识库，用英文进行查询，'
    '获取相关的 FFprobe 命令和文档片段，然后将查询结果整理后返回。'
    '只需要返回查询到的 FFprobe 命令和参数解释，不要添加额外说明。'
)

_probe_execute_prompt = (
    '你是一个 FFprobe 命令执行专家。你的职责是根据用户问题和知识库内容，'
    '生成并执行正确的 ffprobe 命令。\n\n'
    '规则：\n'
    '1. 只能调用 execute_probe_command 执行以 ffprobe 开头的命令\n'
    '2. 先用 get_files 查看可用的输入文件\n'
    '3. 输入文件路径用 get_files 返回的实际路径\n'
    '4. ffprobe 是只读分析工具，输出打印到终端即可，不要添加输出文件参数\n'
    '5. 查看媒体信息时使用 -show_format、-show_streams、-show_packets 等参数，常用组合：ffprobe -v error -show_format -show_streams\n'
    '6. 一个任务只执行一次 ffprobe，不要重复尝试多种参数\n'
    '7. 如果 ffprobe 成功（返回 flag=true），立即结束，不要继续尝试其他命令\n'
    '8. 不要执行 ffmpeg、convert、apt-get、sudo、pip、python、ls、pwd、find、which 等非 ffprobe 命令\n'
    '9. 如果 ffprobe 执行失败，不要重试，直接返回失败原因。'
)

_probe_chat_prompt = (
    '你是一个 FFprobe 助手。你的任务是根据用户的原始问题、知识库检索结果和执行结果，'
    '给用户一个完整、简洁的回答。\n\n'
    '要求：\n'
    '1. 先直接回答用户的问题——告诉用户是否已成功完成\n'
    '2. 如果成功，说明使用了什么命令，并把 ffprobe 输出的关键信息'
    '（格式、编码、分辨率、码率、时长等）整理成易读的说明\n'
    '3. 如果失败，说明失败原因和建议\n'
    '4. 适当引用执行日志中的关键信息\n'
    '5. 使用中文、语气友好'
)


def _build_probe_agents():
    m = get_model()
    if m is None:
        return None, None, None
    return (
        create_agent(model=m, system_prompt=SystemMessage(content=_probe_search_prompt), tools=[get_probe_command], middleware=[_probe_search_tool_limit]),
        create_agent(model=m, system_prompt=SystemMessage(content=_probe_execute_prompt), tools=[get_files, execute_probe_command], middleware=[_probe_execute_tool_limit]),
        create_agent(model=m, system_prompt=SystemMessage(content=_probe_chat_prompt), tools=[]),
    )


agent_probe_search, agent_probe_execute, agent_probe_chat = None, None, None


def ensure_probe_agents():
    global agent_probe_search, agent_probe_execute, agent_probe_chat
    if not is_configured():
        return False
    if agent_probe_search is None:
        agent_probe_search, agent_probe_execute, agent_probe_chat = _build_probe_agents()
    return agent_probe_search is not None