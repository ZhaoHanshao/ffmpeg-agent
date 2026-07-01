from utils.model import model
from langchain.agents import create_agent
from langchain.messages import SystemMessage,HumanMessage,AIMessage

'''
三个Agent
agent_search：RAG知识库查询agent，负责查询向量数据库内容，获得相关知识
agent_excute：命令执行agent，负责执行ffmpeg相关命令
agent_chat：用户对话agent，负责与用户对话
'''

# RAG知识库查询agent，负责查询向量数据库内容，获得相关知识
# 需要绑定两个tool：1、负责查询数据
#                  2、负责判断执行命令返回结果是否成功
agent_search = create_agent(
    model = model,
    system_prompt=SystemMessage(content='你是一个RAG知识库查询AI，你负责查询知识库用以获取相关的知识和终端命令'),
    # tools=
)

# 命令执行agent，负责执行ffmpeg相关命令
agent_excute = create_agent(
    model=model,
    system_prompt=SystemMessage(content='你是一个任务执行AI，你负责对文件执行指令'),
    # tools=
)

# 用户对话agent，负责与用户对话
agent_chat = create_agent(
    model = model,
    system_prompt=SystemMessage(content='你是一个反馈AI，将结果返回给对话的用户')
    # tools=
)