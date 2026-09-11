"""多模态素材理解与显式参数提取的测试。

分两部分：
1) 显式参数提取：纯函数，必须稳定识别用户写出的参数、且不误判普通负号
2) 素材分析：ffprobe 指标 + 抽帧/波形是否真的产出图像（不依赖 LLM 是否支持视觉——
   用打桩替换 LLM 调用，验证"支持视觉时带图、不支持时自动降级"两条路径）
"""
import os
import re
import subprocess
import sys
import shutil

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app import media  # noqa: E402
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


def _dim_ok(path, max_width):
    """用 ffprobe 确认抽出的图不超过宽度上限（避免把原图直接塞给模型）。"""
    info = media._probe_json(path)
    for s in info.get('streams') or []:
        if s.get('codec_type') == 'video' and s.get('width'):
            return int(s['width']) <= max_width
    return False


print('\n=== test_media.py ===\n')

# ── 显式参数提取 ──
print('--- extract_explicit_params ---')
cases = [
    ('转成 mp4，用 -crf 18 保证画质', ['-crf 18']),
    ('请用 -c:v libx265 -preset slow 压缩', ['-c:v libx265', '-preset slow']),
    ('加 -an 去掉音轨', ['-an']),
    ('缩放到 -s 1280x720', ['-s 1280x720']),
    ('用 -vf "scale=1280:-2" 缩放', None),          # 带引号，此处只要求不崩
    ('把亮度提高 0.1，就是 -0.1 那种', []),           # 普通负号不应误判
    ('', []),
    ('-crf 18 和 -crf 23 都试过', ['-crf 18', '-crf 23']),
]
for text, expect in cases:
    got = media.extract_explicit_params(text)
    if expect is None:
        check(f'不崩溃：{text[:28]!r}', isinstance(got, list), str(got))
    else:
        check(f'{text[:30]!r} -> {expect}', got == expect, f'实际 {got}')

# 关键：不能把时间/数字里的负号当成参数
check('负号不入参：-ss 与 -0.5 场景',
      media.extract_explicit_params('从 -ss 5 开始，亮度 -0.5') == ['-ss 5'],
      str(media.extract_explicit_params('从 -ss 5 开始，亮度 -0.5')))

# ── 素材分析：先确认能抽出图像（不打桩，只验证 ffmpeg 抽帧/波形） ──
print('\n--- 抽取图像（ffmpeg）---')
UL = tools.UPLOAD
video = os.path.join(UL, '_media_test.mp4')
audio = os.path.join(UL, '_media_test.mp3')
image = os.path.join(UL, '_media_test.png')
ff = tools.ffmpeg_bin('ffmpeg')
subprocess.run([ff, '-y', '-f', 'lavfi', '-i', 'testsrc=duration=3:size=320x240:rate=15',
                '-c:v', 'libx264', '-preset', 'ultrafast', video],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run([ff, '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', audio],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run([ff, '-y', '-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1',
                '-frames:v', '1', image],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

tmpdir = os.path.join(tools.DOWNLOAD, '_media_tmp')
shutil.rmtree(tmpdir, ignore_errors=True)
os.makedirs(tmpdir, exist_ok=True)
try:
    vimgs = media._extract_images(video, '.mp4', tmpdir)
    check(f'视频抽帧：{len(vimgs)} 张', len(vimgs) >= 1, str(vimgs))
    check('抽帧尺寸不超过上限', all(
        _dim_ok(p, media.MAX_IMAGE_WIDTH) for p in vimgs) if vimgs else False)

    aimgs = media._extract_images(audio, '.mp3', tmpdir)
    check(f'音频生成波形图：{len(aimgs)} 张', len(aimgs) == 1, str(aimgs))

    iimgs = media._extract_images(image, '.png', tmpdir)
    check(f'图片直接使用：{len(iimgs)} 张', len(iimgs) == 1, str(iimgs))

    # ffprobe 指标
    info = media._probe_json(video)
    summary = media._summarize_probe(info)
    check(f'技术指标含分辨率：{summary[:60]}', '320x240' in summary, summary)
    check('技术指标含编解码', 'h264' in summary, summary)
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

# ── 分析流程：视觉可用 / 不可用两条路径（打桩 LLM） ──
print('\n--- analyze_files（打桩 LLM）---')
calls = {'n': 0, 'had_image': False}


def fake_call(messages):
    calls['n'] += 1
    content = messages[0].content
    if isinstance(content, list):
        calls['had_image'] = any(
            isinstance(c, dict) and c.get('type') == 'image_url' for c in content)
        return '画面：测试画面\n参数建议：-crf 18\n注意：无'
    return '画面：未看图\n参数建议：-crf 23\n注意：无'


orig_call = media._call_llm
orig_flag = media._vision_supported
media._cache.clear()

# 路径 A：模型支持视觉 -> 应带上图像
media._call_llm = fake_call
media._vision_supported = None
calls.update(n=0, had_image=False)
out = media.analyze_files([video], '压缩一下')
check('视觉可用时结果非空', bool(out), repr(out[:80]))
check('视觉可用时确实传了图像', calls['had_image'])
check('结果包含技术指标前缀', '素材：' in out, out[:80])

# 路径 B：模型拒绝图像 -> 自动降级且不再重试
media._cache.clear()


def fake_call_reject(messages):
    calls['n'] += 1
    content = messages[0].content
    if isinstance(content, list):
        raise RuntimeError('This model does not support image input')
    return '画面：纯文本分析\n参数建议：-crf 20\n注意：无'


media._call_llm = fake_call_reject
media._vision_supported = None
out2 = media.analyze_files([video], '压缩一下')
check('图像被拒时自动降级为纯文本', bool(out2), repr(out2[:80]))
check('降级后标记视觉不可用', media._vision_supported is False)

# 再调一次：已知不支持视觉，不应再尝试带图
calls['n'] = 0
media._cache.clear()
out3 = media.analyze_files([video], '再压缩')
check('已知不支持视觉时不再尝试带图', calls['n'] == 1, f'调用次数={calls["n"]}')

# 缓存：同文件重复分析应命中缓存
calls['n'] = 0
media.analyze_files([video], '再压缩')
check('相同文件命中缓存（不再调用 LLM）', calls['n'] == 0, f'调用次数={calls["n"]}')

# 恢复
media._call_llm = orig_call
media._vision_supported = orig_flag
media._cache.clear()

# 不存在的文件
check('不存在的文件返回空串', media.analyze_files(['/no/such/file.mp4']) == '')

for p in (video, audio, image):
    if os.path.isfile(p):
        os.remove(p)

print(f'\n结果: {pass_count} 通过, {fail_count} 失败')
sys.exit(0 if fail_count == 0 else 1)
