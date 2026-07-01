from utils.agent import agent_chat,agent_excute,agent_search
from langgraph.graph import START,END,StateGraph,MessagesState
from langchain.messages import ToolMessage


"""
START : 开始节点，用于图结构的开始（graph.set_entity）
END : 结束节点，用于图结构的结束
MessagesState : 用于节点与节点之间的消息传输（官方推荐：通常情况下可以自定义消息结构）
StateGraph : 有状态的图结构（该图结构允许自定义消息）
"""

class state(MessagesState):
    command:str = None #执行的命令
    result:str  #知识库查询结果
    command_result: str #执行结果
    history:MessagesState.messages
    question:MessagesState.messages
    flag:bool = False #是否结束命令执行的校验

def search(state:state):
    '''
    graph节点search
    负责查询知识库获取ffmpeg需求的命令
    2种情况：
    flag==False默认将获取的命令和相关说明传入excute
    2、并非第一次：执行成功->flag = True->chat
                  执行失败->尝试分析错误，继续尝试执行->excute
    '''
    print('='*20+'执行查询'+'='*20)
    # 获取查询问题
    mes = state['messages']
    state['question'] = mes
    # 判断是否执行成功，成功则直接结束
    if state['flag']:
        return state
    # 不成功或者未执行则查询并传给excut
    # 不成功要附带错误返回，command_result
    res = agent_search.invoke({'question':mes})
    if state['command']!=None:
        res = agent_search.invoke({'question':f'{mes},{state['command_result']}'})
    else:    
        res = agent_search.invoke({'question':mes})
    state['result'] = res['message'][-1].content
    return state


# 命令执行节点
# 对result中的语句进行认识并生成命令传入tool，执行命令，最后返回执行结果
def excute(state:state):
    print('='*20+'执行命令'+'='*20)
    result = state['result']
    res = agent_excute.invoke({'question':result})
    # if res.tool_calls:
    #     print('调用工具执行命令成功')
    return state

def chat(state:state):
    print('='*20+'执行对话'+'='*20)
    result = state['result']
    res = agent_chat.invoke({'question':result})
    return state

# 分支节点选择
def which_continue(state:state):
    if state['flag']==False:
        return 'excute'
    else:
        return 'chat'

workflow = StateGraph(state_schema=state)

# 注册节点
workflow.add_node('search',search)
workflow.add_node('excute',excute)
workflow.add_node('chat',chat)

# 注册边
workflow.add_edge(START,agent_search)

# 条件边
workflow.add_conditional_edges(agent_search,which_continue)
