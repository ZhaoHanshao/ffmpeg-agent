from app.model import model
from langchain.agents import create_agent
from app.tools import get_command, get_files, execute_command
from langchain.messages import SystemMessage

# ── 知识库查询 agent ──
# 根据用户问题，查询 ChromaDB 向量库获取 FFmpeg 相关文档片段
agent_search = create_agent(
    model=model,
    system_prompt=SystemMessage(content=(
        '你是一个 FFmpeg 知识库查询助手。'
        '你的任务是根据用户的 FFmpeg 相关问题，使用 get_command 工具查询知识库，知识库为英文知识库，用英文进行查询，'
        '获取相关的 FFmpeg 命令和文档片段，然后将查询结果整理后返回。'
        '只需要返回查询到的 FFmpeg 命令和参数解释，不要添加额外说明。'
    )),
    tools=[get_command],
)

# ── 命令执行 agent ──
# 根据知识库结果，生成并执行正确的 ffmpeg 命令
agent_execute = create_agent(
    model=model,
    system_prompt=SystemMessage(content=(
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
        '8. 如果 ffmpeg 执行失败，分析 stderr 后最多再重试一次修正后的命令\n'
        '9. 如果最终结果仍失败，返回失败原因。'
    )),
    tools=[get_files, execute_command],
)

# ── 对话回复 agent ──
# 根据用户原始问题、知识库结果、执行结果，生成最终回复
agent_chat = create_agent(
    model=model,
    system_prompt=SystemMessage(content=(
        '你是一个 FFmpeg 助手。你的任务是根据用户的原始问题、知识库检索结果和执行结果，'
        '给用户一个完整、简洁的回答。\n\n'
        '要求：\n'
        '1. 先直接回答用户的问题——告诉用户是否已成功完成\n'
        '2. 如果成功，说明使用了什么命令、输出文件是什么\n'
        '3. 如果失败，说明失败原因和建议\n'
        '4. 输出文件可以在浏览器中通过 /api/output/文件名 下载\n'
        '5. 适当引用执行日志中的关键信息\n'
        '6. 使用中文、语气友好'
    )),
)
