"""六个 LangChain agent（ffmpeg / ffprobe 各三个）。

结构说明：
- 两套变体（ffmpeg / ffprobe）的差异只有「提示词、检索工具、执行工具、执行工具名」，
  因此用 AgentSpec + _create 参数化构建，避免三份复制粘贴。
- 为兼容既有测试与调用方，保留了全部原有的模块级名称
  （`_search_prompt`、`agent_search`、`_build_agents`、`_search_tool_limit` 等），
  它们现在只是参数化结果或别名，语义与之前一致。
"""
from app.model import (
    get_model, is_configured, build_model_for, config_fingerprint, is_config_configured,
)
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from app.tools import get_command, get_files, execute_command, get_probe_command, execute_probe_command
from langchain.messages import SystemMessage
import logging
import threading

logger = logging.getLogger(__name__)

# 检索/执行工具在**单次 agent 调用内**的允许次数。
#
# 都设为 1：一次调用只查一次知识库、只执行一次命令。
# 关键点是必须配 exit_behavior='end'——只设 run_limit 而不结束循环时，
# 模型会反复重试被拦下的工具，一路撞到 GraphRecursionError（实测 1 次限制下
# 模型往返高达 4999 次）。exit_behavior='end' 会在超限那一步直接结束 agent，
# 实测工具执行 1 次、模型往返 2 次。
SEARCH_TOOL_LIMIT = 1
EXECUTE_TOOL_LIMIT = 1


# ── 提示词 ──

_search_prompt = (
    '你是一个 FFmpeg 知识库查询助手，只为**一次**检索调用做准备。\n\n'
    '工作方式（必须遵守）：\n'
    '1. 先在心里拆解用户需求，把「要做什么 + 涉及哪些参数 + 常见坑」合成**一条**'
    '覆盖面最广的英文检索式，然后**只调用一次** get_command。\n'
    '   例：用户说"把视频压小一点"，检索式应同时覆盖 scale / crf / preset / bitrate，'
    '而不是只查 "compress video"。\n'
    '2. 知识库是英文的，检索式用英文；不要用中文查询。\n'
    '3. 工具返回后**立即给出结论**，不要再调用工具，也不要追问。\n\n'
    '输出要求（供下游据此写 ffmpeg 命令，务必简洁）：\n'
    '- 直接列出可用的 ffmpeg 命令与关键参数，参数取值照抄检索结果；\n'
    '- 只保留与当前需求相关的 3~6 条要点，不要罗列无关章节；\n'
    '- **原样保留检索结果中的选项拼写**，不要凭记忆补全或改写选项名；\n'
    '- 检索结果里没有对应内容时，明确写「未检索到」，不要编造命令。\n'
    '不要输出寒暄、解释或额外说明。'
)

_execute_prompt = (
    '你是一个 FFmpeg 命令执行专家。你的职责是根据用户问题和知识库内容，'
    '生成并执行正确的 ffmpeg 命令。\n\n'
    '规则：\n'
    '1. 只能调用 execute_command 执行以 ffmpeg 开头的命令\n'
    '2. **每轮只调用一个工具**：先调用 get_files 拿真实路径；在拿到它的返回之前，'
    '不要在同一轮里同时调用 execute_command\n'
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
    '你是一个 FFprobe 知识库查询助手，只为**一次**检索调用做准备。\n\n'
    '工作方式（必须遵守）：\n'
    '1. 先把用户需求拆解成「要查看哪些字段 + 对应 ffprobe 参数 + 常见坑」，'
    '合成**一条**覆盖面最广的英文检索式，然后**只调用一次** get_probe_command。\n'
    '   例：用户说"看看这个视频什么情况"，检索式应同时覆盖 show_format / show_streams / '
    'codec / bit_rate / duration，而不是只查 "video info"。\n'
    '2. 知识库是英文的，检索式用英文；不要用中文查询。\n'
    '3. 工具返回后**立即给出结论**，不要再调用工具，也不要追问。\n\n'
    '输出要求（供下游据此写 ffprobe 命令，务必简洁）：\n'
    '- 直接列出可用的 ffprobe 命令与关键参数，参数取值照抄检索结果；\n'
    '- 只保留与当前需求相关的 3~6 条要点，不要罗列无关章节；\n'
    '- **原样保留检索结果中的选项拼写**，不要凭记忆补全或改写选项名；\n'
    '- 检索结果里没有对应内容时，明确写「未检索到」，不要编造命令。\n'
    '不要输出寒暄、解释或额外说明。'
)

_probe_execute_prompt = (
    '你是一个 FFprobe 命令执行专家。你的职责是根据用户问题和知识库内容，'
    '生成并执行正确的 ffprobe 命令。\n\n'
    '规则：\n'
    '1. 只能调用 execute_probe_command 执行以 ffprobe 开头的命令\n'
    '2. **每轮只调用一个工具**：先调用 get_files 拿真实路径；在拿到它的返回之前，'
    '不要在同一轮里同时调用 execute_probe_command\n'
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
#
# 全部使用 exit_behavior='end'：超限即结束该 agent，不再让模型重复试错。
# 注意这与 exit_behavior='continue'（默认）差别很大——后者只拦工具、放模型继续，
# 模型会一次次重试直到撞上 GraphRecursionError（实测限制 1 次时模型往返 4999 次）。
#
# 代价：exit_behavior='end' 要求"超限那一轮不能同时调用别的工具"（否则中间件会抛
# NotImplementedError）。因此这里对 count 设 1，配合提示词里
# "先 get_files 拿到路径、再调 execute_command"的顺序要求，确保每轮只有一个工具调用。
_search_tool_limit = ToolCallLimitMiddleware(
    tool_name="get_command",
    run_limit=SEARCH_TOOL_LIMIT,
    thread_limit=SEARCH_TOOL_LIMIT,
    exit_behavior="end",
)

_execute_tool_limit = ToolCallLimitMiddleware(
    tool_name="execute_command",
    run_limit=EXECUTE_TOOL_LIMIT,
    thread_limit=EXECUTE_TOOL_LIMIT,
    exit_behavior="end",
)

_probe_search_tool_limit = ToolCallLimitMiddleware(
    tool_name="get_probe_command",
    run_limit=SEARCH_TOOL_LIMIT,
    thread_limit=SEARCH_TOOL_LIMIT,
    exit_behavior="end",
)

_probe_execute_tool_limit = ToolCallLimitMiddleware(
    tool_name="execute_probe_command",
    run_limit=EXECUTE_TOOL_LIMIT,
    thread_limit=EXECUTE_TOOL_LIMIT,
    exit_behavior="end",
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
    """按 spec 用**全局配置**构建三个 agent。"""
    return _create_with(spec, get_model())


def _create_with(spec: AgentSpec, model):
    """按 spec + 指定模型实例构建三个 agent。"""
    if model is None:
        return None, None, None
    return (
        create_agent(
            model=model,
            system_prompt=SystemMessage(content=spec.search_prompt),
            tools=[spec.search_tool],
            middleware=[spec.search_limit],
        ),
        create_agent(
            model=model,
            system_prompt=SystemMessage(content=spec.execute_prompt),
            tools=[get_files, spec.execute_tool],
            middleware=[spec.execute_limit],
        ),
        create_agent(
            model=model,
            system_prompt=SystemMessage(content=spec.chat_prompt),
            tools=[],
        ),
    )


# ── 按配置指纹缓存的 agent（多对话：每个对话可以用自己的模型）──
#
# 为什么缓存而不是每次现建：create_agent 要重新推导工具 schema、编译中间件链，
# 每个请求建一次纯属浪费；而同一份配置构建出来的 agent 是可复用的（无请求态）。
# 键必须含真实 api_key（见 model.config_fingerprint），否则换 key 后复用旧 agent。
_agents_cache = {}
_agents_cache_order = []
_cache_lock = threading.Lock()
_CACHE_MAX = 8


def _cache_get(spec_name: str, fingerprint: str):
    with _cache_lock:
        return _agents_cache.get((spec_name, fingerprint))


def _cache_put(spec_name: str, fingerprint: str, agents):
    with _cache_lock:
        key = (spec_name, fingerprint)
        _agents_cache[key] = agents
        _agents_cache_order.append(key)
        # 简单 FIFO 淘汰：绑定多个模型时缓存不会无限增长
        while len(_agents_cache_order) > _CACHE_MAX:
            old = _agents_cache_order.pop(0)
            if old != key:
                _agents_cache.pop(old, None)


def clear_agent_cache():
    with _cache_lock:
        _agents_cache.clear()
        _agents_cache_order.clear()


def agents_for(spec: AgentSpec, cfg: dict = None):
    """返回 (search, execute, chat)。

    cfg 为 None/空 → 全局配置（等价于 spec.agents()）。
    cfg 为完整配置 → 按指纹缓存构建，对话级模型覆盖走这条路。
    """
    if not cfg:
        return _create(spec)
    if not is_config_configured(cfg):
        return None, None, None
    fingerprint = config_fingerprint(cfg)
    cached = _cache_get(spec.name, fingerprint)
    if cached is not None:
        return cached
    model = build_model_for(cfg)
    if model is None:
        return None, None, None
    built = _create_with(spec, model)
    _cache_put(spec.name, fingerprint, built)
    logger.info(f'按对话级配置构建 {spec.name} agents（model={cfg.get("model")}）')
    return built


def ensure_agents_for(spec: AgentSpec, cfg: dict = None) -> bool:
    return agents_for(spec, cfg)[0] is not None



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
