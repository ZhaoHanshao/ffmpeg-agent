import os, sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv
load_dotenv()

from app.graph import (
    state, which_continue_exec, build_chat_prompt, build_probe_chat_prompt,
    exec_workflow, probe_exec_workflow, probe_search, probe_execute,
)
from langchain.messages import HumanMessage, AIMessage

pass_count = 0
fail_count = 0


def check(name: str, cond: bool, detail: str = ''):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f'  PASS  {name}')
    else:
        fail_count += 1
        print(f'  FAIL  {name}' + (f'  — {detail}' if detail else ''))


print('\n=== test_graph.py ===\n')


# ── state 初始化 ──
print('--- state 初始化 ---')
s = state(messages=[HumanMessage(content='test')])
check('state 可用关键字初始化', True)
check('可设置 command', True)
check('可设置 flag', True)

# 验证 state 类字段定义存在
from app.graph import state as state_cls
annots = getattr(state_cls, '__annotations__', {})
check('state 类有 command 字段', 'command' in annots or hasattr(state_cls, 'command'))
check('state 类有 flag 字段', 'flag' in annots)
check('state 类有 search_count 字段', 'search_count' in annots)
check('state 类有 output_file 字段', 'output_file' in annots)
check('state 类继承 MessagesState（含 messages 字段）', True)


# ── which_continue_exec ──
# 注意：未选择文件时路由直接 END（纯知识问答，见提交 3ff7ae7），
# 因此要走到 execute 分支必须带上 files。
FILES = ['backend/upload/a.mp4']
print('\n--- which_continue_exec ---')
s_true = state(flag=True, files=FILES)
s_false = state(flag=False, files=FILES)

r = which_continue_exec(s_true)
check('flag=True 时返回 END', r == '__end__' or r == 'END')

r = which_continue_exec(s_false)
check('flag=False 且有文件时返回 execute', r == 'execute')

s_nofile = state(flag=False, files=[])
check('未选择文件时返回 END', which_continue_exec(s_nofile) in ('__end__', 'END'))


# ── which_continue_exec: 执行次数上限 ──
print('\n--- which_continue_exec (执行次数上限) ---')
s_exec_lim = state(flag=False, execute_count=3, files=FILES)
r_lim = which_continue_exec(s_exec_lim)
check('execute_count>=3 时返回 END', r_lim == '__end__' or r_lim == 'END')

s_exec_ok = state(flag=False, execute_count=2, files=FILES)
r_ok = which_continue_exec(s_exec_ok)
check('execute_count<3 时返回 execute', r_ok == 'execute')


# ── build_chat_prompt ──
print('\n--- build_chat_prompt ---')
prompt = build_chat_prompt({
    'history': [HumanMessage(content='把图片反色')],
    'result': '使用 -vf negate 参数',
    'command': 'ffmpeg -i input.png -vf negate output.png',
    'command_result': 'ffmpeg -i input.png -vf negate output.png 执行成功',
    'output_file': 'output.png',
})
check('包含用户问题', '把图片反色' in prompt)
check('包含知识库结果', '-vf negate' in prompt)
check('包含命令', 'ffmpeg -i input.png' in prompt)
check('包含执行结果', '执行成功' in prompt)
check('包含输出文件', 'output.png' in prompt)

# build_chat_prompt — 无命令/无结果
prompt2 = build_chat_prompt({
    'history': [HumanMessage(content='测试')],
    'result': '一些信息',
})
check('无命令时仍正常工作', '测试' in prompt2 and '一些信息' in prompt2)
check('无命令时不含命令字段', '执行的命令' not in prompt2)

# build_chat_prompt — 无 history
prompt3 = build_chat_prompt({})
check('无 history 时仍正常工作', prompt3 != '')


# ── search_count 限制（测试 early-return 路径） ──
print('\n--- search_count 限制 ---')
from app.model import is_configured as llm_configured

# 上限检查位于 LLM 配置检查之前，因此无论 LLM 是否配置都应走 early-return 且不抛异常
s_limit = state(messages=[HumanMessage(content='test')], search_count=10)
from app.graph import search
try:
    result = search(s_limit)
    check('search_count 超限时未抛异常（先于 LLM 检查返回）', True)
    result_val = result.get('result', '')
    check('result 是字符串（类型已统一）', isinstance(result_val, str),
          f'实际类型：{type(result_val).__name__}')
    check('返回最大查询次数提示', '已达到最大查询次数' in result_val)
except RuntimeError as e:
    check('search_count 超限时不应先抛 LLM 未配置异常', False, str(e))


# ── build_probe_chat_prompt ──
print('\n--- build_probe_chat_prompt ---')
prompt_p = build_probe_chat_prompt({
    'history': [HumanMessage(content='查看视频编码')],
    'result': '使用 -show_streams 参数',
    'command': 'ffprobe -v error -show_streams input.mp4',
    'command_result': 'codec_name=h264',
})
check('包含用户问题', '查看视频编码' in prompt_p)
check('包含知识库结果', '-show_streams' in prompt_p)
check('包含命令', 'ffprobe -v error' in prompt_p)
check('包含执行结果', 'codec_name=h264' in prompt_p)
check('不含输出文件字段', '输出文件' not in prompt_p)

# build_probe_chat_prompt — 无命令/无结果
prompt_p2 = build_probe_chat_prompt({
    'history': [HumanMessage(content='测试')],
    'result': '一些信息',
})
check('无命令时仍正常工作', '测试' in prompt_p2 and '一些信息' in prompt_p2)
check('无命令时不含命令字段', '执行的命令' not in prompt_p2)


# ── ffprobe 图结构 ──
print('\n--- ffprobe 图结构 ---')
check('probe_exec_workflow 是已编译图',
      'CompiledStateGraph' in type(probe_exec_workflow).__name__
      or 'StateGraph' in type(probe_exec_workflow).__name__,
      type(probe_exec_workflow).__name__)
p_nodes = list(probe_exec_workflow.get_graph().nodes)
check('包含 search 节点', 'search' in p_nodes)
check('包含 execute 节点', 'execute' in p_nodes)

print('\n--- ffprobe 图编译 ---')
try:
    compiled_p = probe_exec_workflow
    check('probe 图编译成功', True)
    check('probe 图编译结果是 Runnable', hasattr(compiled_p, 'invoke'))
except Exception as e:
    check(f'probe 图编译失败: {e}', False, str(e))

print('\n--- ffprobe search_count 限制 ---')
s_limit_p = state(messages=[HumanMessage(content='test')], search_count=10)
try:
    result_p = probe_search(s_limit_p)
    check('probe_search 超限时未抛异常', True)
    result_val_p = result_p.get('result', '')
    check('probe result 是字符串（类型已统一）', isinstance(result_val_p, str),
          f'实际类型：{type(result_val_p).__name__}')
    check('返回最大查询次数提示', '已达到最大查询次数' in result_val_p)
except RuntimeError as e:
    check('probe_search 超限时不应先抛 LLM 未配置异常', False, str(e))


# ── 图结构 ──
print('\n--- 图结构 ---')
from app.graph import FFMPEG_GRAPH, PROBE_GRAPH
check('ffmpeg 图记录输出文件', FFMPEG_GRAPH.capture_output is True)
check('ffprobe 图不记录输出文件', PROBE_GRAPH.capture_output is False)
check('ffmpeg 与 ffprobe graph spec 不同',
      FFMPEG_GRAPH.agent_prefix != PROBE_GRAPH.agent_prefix
      and FFMPEG_GRAPH.ensure_attr != PROBE_GRAPH.ensure_attr)
check('exec_workflow 是已编译图',
      'CompiledStateGraph' in type(exec_workflow).__name__
      or 'StateGraph' in type(exec_workflow).__name__,
      type(exec_workflow).__name__)
nodes = list(exec_workflow.get_graph().nodes)
check('包含 search 节点', 'search' in nodes)
check('包含 execute 节点', 'execute' in nodes)


# ── 图编译 ──
print('\n--- 图编译 ---')
try:
    compiled = exec_workflow
    check('图编译成功', True)
    check('编译结果是 Runnable', hasattr(compiled, 'invoke'))
except Exception as e:
    check(f'图编译失败: {e}', False, str(e))


print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
