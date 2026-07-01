from dotenv import load_dotenv
import os

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.tools import tool
from langchain.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from langchain.agents.middleware import ToolCallLimitMiddleware

load_dotenv()

MODEL_NAME = os.getenv('MODEL_NAME')
BASE_URL = os.getenv('BASE_URL')
API_KEY = os.getenv('API_KEY')

@tool
def is_sun(is_sun:bool,config:RunnableConfig)->str:
    '''
    查询天气是否为晴天
    is_sun:根据LLM的结果选择合适的布尔型，返回中文语句
    '''
    if "metadata" not in config:
        config["metadata"] = {}
    config ['metadata']['is_sun']='2'
    if is_sun:
        return '是晴天'
    else:
        return '不是晴天'

search_limit = ToolCallLimitMiddleware(
    tool_name="is_sun",
    run_limit=3,
    thread_limit=10
)

model = ChatOpenAI(
    model=MODEL_NAME,
    base_url=BASE_URL,
    api_key=API_KEY,
    temperature=0.2,
    max_tokens = 256,
)

config = {"metadata": {}} 
agent = create_agent(
    model = model,
    tools=[is_sun],
    middleware = [search_limit],
)

# 流式agent输出测试，用于最后页面的安排
# stream = agent.stream_events(
#     {
#         "messages": [{"role": "user", "content": "已知今明两天是雨天，今天天气是晴天吗？"}],
#     },
#     version='v3')

# agent的流式输出实现
# for message in stream.messages:
#     for chunk in message.text:
#         print(chunk,end='',flush=True)

# for i in res['messages'][::-1]:
#     if isinstance(i,ToolMessage):
#         if not bool(i.content):
#             print('不是晴天')
#         else :
#             print('是晴天')

res = agent.invoke({
    "messages": [{"role": "user", "content": "已知今明两天是晴天，今天天气是晴天吗？一定要调用工具查询返回格式化内容"}],},
    config = config
    )

# print(config['metadata']['is_sun'])

print(res['messages'][-1].content)