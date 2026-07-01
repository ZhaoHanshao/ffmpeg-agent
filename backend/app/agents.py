from app.model import model
from langchain.agents import create_agent
from app.tools import get_commend, get_files, excute_command
from langchain.messages import SystemMessage

# RAG知识库查询agent
agent_search = create_agent(
    model=model,
    system_prompt=SystemMessage(content='你是一个RAG知识库查询AI，你负责查询知识库用以获取相关的知识和终端命令'),
    tools=[get_commend],
)

# 命令执行agent
agent_excute = create_agent(
    model=model,
    system_prompt=SystemMessage(content='你是一个任务执行AI，你负责对文件执行指令'),
    tools=[get_files, excute_command],
)

# 用户对话agent
agent_chat = create_agent(
    model=model,
    system_prompt=SystemMessage(content='你是一个反馈AI，将结果返回给对话的用户'),
)
