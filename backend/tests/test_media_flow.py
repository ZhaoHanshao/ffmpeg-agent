"""端到端验证多模态素材理解是否真的串进 SSE 流并注入图谱状态。

关键：走**真实的** `_chat_response`（含真实的 _prep / _run_graph 接线），
只打桩两个外部依赖：
  - graph_fn：换成记录参数的假图谱，避免真的跑 LLM 图谱
  - media._call_llm：素材分析里唯一会联网的调用
这样验证的是生产代码的接线，而不是测试自己复刻的一套。
"""
import asyncio
import json
import os
import subprocess
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app import media  # noqa: E402
import app.main as m  # noqa: E402
from app import tools  # noqa: E402

pass_count = 0
fail_count = 0


def check(name, cond, detail=''):
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f'  PASS  {name}')
    else:
        fail_count += 1
        print(f'  FAIL  {name}' + (f'  — {detail}' if detail else ''))


print('\n=== test_media_flow.py ===\n')

src = os.path.join(tools.UPLOAD, '_flow_src.png')


def _make_fixture(path, attempts=3):
    """生成测试素材，并**确认文件真的落盘**。

    这里必须显式校验：曾出现过 ffmpeg 返回 0 但文件没写出来的情况
    （image2 muxer 在某些参数/输出环境下只打印警告），
    若不校验，后续"选中文件"就变成空列表，测试会以难以理解的方式失败。
    """
    args = [tools.ffmpeg_bin('ffmpeg'), '-y', '-f', 'lavfi', '-i',
            'testsrc=duration=1:size=200x150:rate=1', '-frames:v', '1', path]
    for _ in range(attempts):
        if os.path.isfile(path):
            os.remove(path)
        proc = subprocess.run(args, capture_output=True)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return True
        tail = proc.stderr.decode(errors='replace').strip().splitlines()
        print(f'  [fixture] ffmpeg rc={proc.returncode} 未产出文件：'
              f'{tail[-1][:80] if tail else "(无输出)"}')
    return False


if not _make_fixture(src):
    print('无法生成测试素材，跳过（环境问题，非代码缺陷）')
    sys.exit(0)

captured = {}


def fake_graph(question, progress=None, **kwargs):
    captured['graph_kwargs'] = kwargs
    captured['graph_question'] = question
    return {'output_file': '', 'result': 'ok',
            'history': [type('M', (), {'content': question})()]}


class _FakeSpec:
    """只为通过 _chat_response 的 LLM 可用性检查，不参与图谱执行。"""

    def ensure(self, state=None):
        return True


def run_chat(question, files):
    """跑一次真实的 _chat_response，返回 SSE 事件列表。"""
    captured.clear()
    media._cache.clear()
    # 直接 import 时不会触发 lifespan（那要真跑 uvicorn / TestClient），
    # 所以 _init_state 仍是 running；这里显式置为就绪，避免被前置校验挡掉。
    m._init_state['status'] = 'ok'
    # spec 用真实的 GraphSpec：_chat_response 现在通过 spec.ensure() 判断 LLM 可用性
    # （多对话改造后不再有 ensure_fn 参数）。测试只关心接线，这里给一个假的图即可。
    response = m._chat_response(
        question, files, [],
        kind='',
        graph_fn=fake_graph,
        prompt_builder=lambda st: 'prompt',
        spec=_FakeSpec(),
    )

    async def drain():
        events = []
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunk = chunk.decode('utf-8', 'replace')
            for line in str(chunk).splitlines():
                if line.startswith('data: '):
                    events.append(json.loads(line[6:]))
        return events

    return asyncio.run(drain())


# ── 打桩 LLM：第一次调用带图，返回分析文本 ──
llm_calls = {'n': 0, 'had_image': False}
orig_call = media._call_llm
orig_vision = dict(media._vision_support)


def fake_llm(messages, cfg=None):
    llm_calls['n'] += 1
    content = messages[0].content
    if isinstance(content, list):
        llm_calls['had_image'] = any(
            isinstance(c, dict) and c.get('type') == 'image_url' for c in content)
    return '画面：彩条测试图，尺寸偏小\n参数建议：scale=1280:-2, -crf 18\n注意：像素格式需为 yuv420p'


media._call_llm = fake_llm
media._vision_support.clear()

try:
    # ── 1) 有文件：应触发分析并把结论传给图谱 ──
    print('--- 选中文件 ---')
    events = run_chat('把这张图转成 mp4，用 -crf 18', [src])
    kinds = [e.get('event') for e in events]
    texts = [e.get('text', '') for e in events if e.get('event') == 'status']
    kwargs = captured.get('graph_kwargs', {})

    check('流以 job 事件开头', kinds and kinds[0] == 'job', str(kinds[:4]))
    check('推送了"正在理解素材画面"状态', any('理解素材' in t for t in texts), str(texts))
    check('推送了分析完成状态', any('素材分析' in t for t in texts), str(texts))
    check('分析确实带了图像', llm_calls['had_image'])
    check('分析结论进入了图谱状态',
          str(kwargs.get('media_analysis', '')).startswith('素材：'),
          repr(kwargs.get('media_analysis'))[:90])
    check('结论里含模型给出的参数建议',
          'scale=1280:-2' in str(kwargs.get('media_analysis', '')))
    check('图谱收到了 stop_event', 'stop_event' in kwargs)
    check('图谱收到了 proc_box', 'proc_box' in kwargs)
    check('用户显式参数 -crf 18 被提取并传入',
          kwargs.get('explicit_params') == ['-crf 18'],
          str(kwargs.get('explicit_params')))
    check('图谱收到的仍是原始问题', captured.get('graph_question') == '把这张图转成 mp4，用 -crf 18')

    # ── 2) 无文件：不做素材分析，省一次模型调用 ──
    print('\n--- 未选中文件 ---')
    llm_calls['n'] = 0
    events2 = run_chat('ffmpeg 怎么裁剪视频', [])
    texts2 = [e.get('text', '') for e in events2 if e.get('event') == 'status']
    kwargs2 = captured.get('graph_kwargs', {})
    check('未选文件时不推送素材相关状态',
          not any('素材' in t or '理解' in t for t in texts2), str(texts2))
    check('未选文件时不调用素材分析模型', llm_calls['n'] == 0, f'调用 {llm_calls["n"]} 次')
    check('未选文件时 media_analysis 为空', not kwargs2.get('media_analysis'))

    # ── 3) 分析失败不应阻断问答 ──
    print('\n--- 分析失败容错 ---')

    def boom(messages):
        raise RuntimeError('vision endpoint exploded')

    media._call_llm = boom
    events3 = run_chat('转成 mp4', [src])
    kinds3 = [e.get('event') for e in events3]
    kwargs3 = captured.get('graph_kwargs', {})
    check('分析抛错后仍继续走到图谱', 'meta' in kinds3, str(kinds3))
    check('分析抛错时 media_analysis 为空', not kwargs3.get('media_analysis'),
          repr(kwargs3.get('media_analysis'))[:80])
    check('分析抛错时仍把显式参数传下去（不因分析失败丢失）',
          'explicit_params' in kwargs3)

    # ── 4) 模型不支持图像：应降级为纯文本且不报错 ──
    print('\n--- 模型不支持图像时降级 ---')
    media._vision_support.clear()

    def reject_images(messages, cfg=None):
        content = messages[0].content
        if isinstance(content, list):
            raise RuntimeError('this model does not support image input')
        return '画面：未看图\n参数建议：-crf 20\n注意：无'

    media._call_llm = reject_images
    events4 = run_chat('压缩一下', [src])
    kwargs4 = captured.get('graph_kwargs', {})
    analysis4 = str(kwargs4.get('media_analysis', ''))
    check('不支持图像时降级成功且仍有结论', analysis4.startswith('素材：'), repr(analysis4)[:80])
    # 注意：模型"拒绝图像"这一路是**尝试过**视觉的，因此不应加"未使用画面分析"标注；
    # 该标注只用于一开始就没图可发的情况。
    check('拒绝图像时不误标"未使用画面分析"', '未使用画面分析' not in analysis4, analysis4[:120])
    check('降级后仍带上了技术指标（图片而非视频）', '图片 png' in analysis4, analysis4[:80])
    check('已记录该模型不收图（后续不再重试）',
          False in media._vision_support.values(), str(media._vision_support))

    # ── 5) 已知不支持视觉时，应标注未使用画面分析 ──
    print('\n--- 已知不支持视觉（避免重复尝试）---')
    media._cache.clear()
    events5 = run_chat('再压缩一次', [src])
    analysis5 = str(captured.get('graph_kwargs', {}).get('media_analysis', ''))
    check('已知不支持视觉时标注"未使用画面分析"',
          '未使用画面分析' in analysis5, analysis5[:120])
finally:
    media._call_llm = orig_call
    media._vision_support.clear(); media._vision_support.update(orig_vision)
    media._cache.clear()
    if os.path.isfile(src):
        os.remove(src)

print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
