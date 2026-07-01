import os, shlex, json
from app.agents import agent_chat, agent_excute, agent_search
from langgraph.graph import START, END, StateGraph, MessagesState
from langchain.messages import ToolMessage, AnyMessage, AIMessage, HumanMessage


class state(MessagesState):
    command: str = None
    result: str = ''
    command_result: str = ''
    history: list[AnyMessage] = None
    flag: bool = False
    output_file: str = ''


def search(state: state):
    print('=' * 20 + '执行查询' + '=' * 20)
    mes = state['messages']
    state['history'] = mes
    if state['flag']:
        return state
    if state['command'] is not None:
        res = agent_search.invoke({'question': f'{mes},{state["command_result"]}'})
    else:
        res = agent_search.invoke({'question': mes})
    state['result'] = res['message'][-1].content
    return state


def excute(state: state):
    print('=' * 20 + '执行命令' + '=' * 20)
    res = agent_excute.invoke({'question': state['result']})
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


def chat(state: state):
    print('=' * 20 + '执行对话' + '=' * 20)
    res = agent_chat.invoke({'question': state['result']})
    ai_content = res['messages'][-1].content if 'messages' in res else str(res)
    state['history'] = (state.get('history', []) or []) + [AIMessage(content=ai_content)]
    return state


def which_continue(state: state):
    return 'excute' if state['flag'] is False else 'chat'


workflow = StateGraph(state_schema=state)
workflow.add_node('search', search)
workflow.add_node('excute', excute)
workflow.add_node('chat', chat)
workflow.add_edge(START, 'search')
workflow.add_edge('excute', 'search')
workflow.add_edge('chat', END)
workflow.add_conditional_edges('search', which_continue)


def run_graph(question: str) -> dict:
    """编译并执行图，返回 {reply, output_file}"""
    compiled = workflow.compile()
    result = compiled.invoke({"messages": [HumanMessage(content=question)]})
    reply = ''
    if result.get('history'):
        for msg in reversed(result['history']):
            if isinstance(msg, AIMessage):
                reply = msg.content
                break
    output_file = result.get('output_file', '') or ''
    return {"reply": reply, "output_file": output_file}


if __name__ == '__main__':
    import sys
    q = ' '.join(sys.argv[1:]) or '如何将图片反色？'
    res = run_graph(q)
    print(f"AI: {res['reply']}")
    if res['output_file']:
        print(f"输出文件: {res['output_file']}")
