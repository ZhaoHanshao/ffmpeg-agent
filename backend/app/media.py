"""多模态素材理解 + 用户显式参数提取。

解决的问题：执行 agent 写 ffmpeg 命令时**看不到素材本身**，只能靠用户文字描述，
于是"这张图偏暗，帮我调亮""这个视频有几个片段要剪掉"这类需求只能猜参数。

本模块做两件事：

1. `analyze_files()`：本地 ffprobe 取技术指标 → 抽取代表性画面（视频抽帧、
   音频画波形、图片直接用），交给**当前配置的多模态模型**看，产出
   "素材是什么 + 该调什么参数"的简短结论，注入执行 agent 的提示词。
   模型不支持图像时自动降级为纯文本分析（只讲技术指标），不报错。

2. `extract_explicit_params()`：识别用户直接给出的 ffmpeg 参数（如 `-crf 18`），
   作为"必须原样使用"的约束，避免被 LLM 自由发挥改掉。

不引入 Pillow：缩放/转码全部交给 ffmpeg（scale 滤镜 → mjpeg），
少一个依赖，且与项目已有的 ffmpeg 沙箱共用同一条路径。
"""
import base64
import logging
import os
import subprocess
import tempfile

from app.tools import ffmpeg_bin, VIRTUAL_SOURCES

logger = logging.getLogger(__name__)

# 送给视觉模型的图片尺寸上限：视觉 token 与图片像素成正比，
# 768 宽足够判断"偏暗/模糊/有水印/构图"这类信息。
MAX_IMAGE_WIDTH = 768
JPEG_QUALITY = 4          # ffmpeg -q:v，2(最好)~31(最差)
MAX_VIDEO_FRAMES = 3      # 抽帧数量上限
MAX_ANALYSIS_CHARS = 800  # 注入提示词的结论长度上限

# 进程内缓存：同一文件重复分析没有意义（按 路径+mtime+大小 命中）
_cache: dict = {}

# 视觉能力探测结果：None=未知，True/False=已确认。
# 一旦某次调用因图像被拒，后续直接跳过图像，不再浪费一次往返。
_vision_supported = None


def _probe_json(path: str) -> dict:
    """ffprobe 读取容器/流信息，失败返回空 dict。"""
    try:
        proc = subprocess.run(
            [ffmpeg_bin('ffprobe'), '-v', 'error', '-print_format', 'json',
             '-show_format', '-show_streams', path],
            capture_output=True, timeout=30,
        )
        import json
        return json.loads(proc.stdout.decode(errors='replace') or '{}')
    except Exception as e:  # noqa: BLE001
        logger.warning(f'ffprobe 读取 {os.path.basename(path)} 失败：{e}')
        return {}


def _summarize_probe(info: dict) -> str:
    """把 ffprobe JSON 压成几行技术指标（这些信息不必让模型"看图"猜）。"""
    parts = []
    fmt = info.get('format') or {}
    fmt_name = (fmt.get('format_name') or '').lower()
    # 单张图片在 ffprobe 里同样是 video 流，这里按容器类型区分措辞，
    # 否则会出现"视频 png"这种自相矛盾的说法。
    image_containers = ('png_pipe', 'image2', 'jpeg_pipe', 'webp_pipe', 'bmp_pipe', 'gif')
    is_image = any(c in fmt_name for c in image_containers)
    if fmt.get('duration'):
        try:
            parts.append(f'时长 {float(fmt["duration"]):.1f}s')
        except (TypeError, ValueError):
            pass
    if fmt.get('bit_rate'):
        try:
            parts.append(f'总码率 {int(fmt["bit_rate"]) // 1000} kbps')
        except (TypeError, ValueError):
            pass
    for s in info.get('streams') or []:
        kind = s.get('codec_type')
        codec = s.get('codec_name') or '?'
        if kind == 'video':
            desc = f'{"图片" if is_image else "视频"} {codec}'
            if s.get('width') and s.get('height'):
                desc += f' {s["width"]}x{s["height"]}'
            if not is_image and s.get('r_frame_rate') and s['r_frame_rate'] not in ('0/0',):
                desc += f' {s["r_frame_rate"]}fps'
            if s.get('pix_fmt'):
                desc += f' {s["pix_fmt"]}'
            parts.append(desc)
        elif kind == 'audio':
            desc = f'音频 {codec}'
            if s.get('sample_rate'):
                desc += f' {s["sample_rate"]}Hz'
            if s.get('channels'):
                desc += f' {s["channels"]}ch'
            parts.append(desc)
    return '；'.join(parts)


def _extract_images(path: str, ext: str, tmpdir: str) -> list[str]:
    """按类型抽取图像：视频抽帧、音频画波形、图片直接用。返回文件路径列表。"""
    ff = ffmpeg_bin('ffmpeg')
    scale = f'scale={MAX_IMAGE_WIDTH}:-2'
    out = []

    def _run(args, dest):
        try:
            subprocess.run([ff, '-v', 'error', '-y', *args, dest],
                           capture_output=True, timeout=60)
            return os.path.isfile(dest) and os.path.getsize(dest) > 0
        except Exception as e:  # noqa: BLE001
            logger.warning(f'抽取图像失败：{e}')
            return False

    if ext in ('.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.wmv', '.m4v', '.ts'):
        info = _probe_json(path)
        try:
            dur = float((info.get('format') or {}).get('duration') or 0)
        except (TypeError, ValueError):
            dur = 0.0
        # 在 10%/50%/90% 处取帧，避开片头片尾黑场
        stamps = [dur * r for r in (0.1, 0.5, 0.9)] if dur > 0 else [0, 1, 2]
        for i, t in enumerate(stamps[:MAX_VIDEO_FRAMES]):
            dest = os.path.join(tmpdir, f'frame{i}.jpg')
            if _run(['-ss', f'{max(0.0, t):.2f}', '-i', path, '-frames:v', '1',
                     '-vf', scale, '-q:v', str(JPEG_QUALITY)], dest):
                out.append(dest)
    elif ext in ('.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg', '.opus', '.wma'):
        # 波形图能让视觉模型看出静音段/削波/音量过低
        dest = os.path.join(tmpdir, 'wave.png')
        if _run(['-i', path, '-filter_complex',
                 'showwavespic=s=768x200:colors=#4f6ef7', '-frames:v', '1'], dest):
            out.append(dest)
    elif ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'):
        dest = os.path.join(tmpdir, 'image.jpg')
        if _run(['-i', path, '-frames:v', '1', '-vf', scale, '-q:v', str(JPEG_QUALITY)], dest):
            out.append(dest)
    return out


def _image_content(path: str) -> dict:
    """把图片编成 OpenAI 兼容的 image_url 内容块（data URL）。"""
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    mime = 'image/png' if path.lower().endswith('.png') else 'image/jpeg'
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}}


_ANALYSIS_PROMPT = (
    '你是音视频处理专家。用户想用 ffmpeg 处理下面的素材，请先看懂素材本身，'
    '再指出**应该调整哪些参数**。\n\n'
    '素材技术信息：{meta}\n'
    '用户需求：{question}\n\n'
    '请只输出以下三行，每行简短、具体：\n'
    '画面：素材内容/画质问题（偏暗、过曝、模糊、有水印、边框、分辨率过小、静音段等）；'
    '若附带图片就是素材画面或音频波形，请结合它判断。\n'
    '参数建议：给出**具体的 ffmpeg 参数与取值**（如 eq=brightness=0.06、scale=1920:-2、'
    '-crf 18），并说明为什么这么取。\n'
    '注意：写命令时必须注意的坑（如像素格式、时长对齐、音视频同步）。\n'
    '不要输出命令全文，只输出这三行。'
)


def _call_llm(messages: list) -> str:
    """调用当前配置的模型；返回文本，失败抛异常。"""
    from app.model import get_model
    m = get_model()
    if m is None:
        raise RuntimeError('LLM 未配置')
    res = m.invoke(messages)
    content = getattr(res, 'content', res)
    if isinstance(content, list):
        content = '\n'.join(
            part.get('text', '') if isinstance(part, dict) else str(part) for part in content
        )
    return str(content or '').strip()


def _is_image_rejected(exc: Exception) -> bool:
    """判断异常是否属于"模型不支持图像输入"。"""
    text = str(exc).lower()
    keys = ('image', 'vision', 'multimodal', 'content type', 'unsupported',
            'invalid content', 'does not support')
    return any(k in text for k in keys)


def analyze_files(files: list, question: str = '') -> str:
    """分析素材并返回可注入提示词的结论文本；无可用素材时返回空串。

    - 只分析第一个文件（多文件会让视觉调用成倍变慢，而用户需求通常围绕主素材）
    - 视觉调用失败（模型不支持图像）时自动降级为纯文本分析并缓存该结论
    """
    global _vision_supported

    files = [f for f in (files or []) if f and os.path.isfile(f)]
    if not files:
        return ''
    path = files[0]
    try:
        st = os.stat(path)
        cache_key = (os.path.abspath(path), int(st.st_mtime), st.st_size)
    except OSError:
        return ''
    if cache_key in _cache:
        return _cache[cache_key]

    ext = os.path.splitext(path)[1].lower()
    info = _probe_json(path)
    meta = _summarize_probe(info) or '（ffprobe 未能读取到信息）'

    text_prompt = _ANALYSIS_PROMPT.format(meta=meta, question=question or '（未说明）')
    result = ''

    tmpdir = tempfile.mkdtemp(prefix='dsh-media-')
    try:
        vision_attempted = False
        images = []
        if _vision_supported is not False:
            images = _extract_images(path, ext, tmpdir)
        if images:
            vision_attempted = True
            content = [{'type': 'text', 'text': text_prompt}]
            content += [_image_content(p) for p in images]
            try:
                from langchain.messages import HumanMessage
                result = _call_llm([HumanMessage(content=content)])
                _vision_supported = True
                logger.info(f'多模态分析完成（{len(images)} 张图）')
            except Exception as e:  # noqa: BLE001
                if _is_image_rejected(e):
                    logger.warning(f'当前模型不支持图像输入，降级为纯文本分析：{e}')
                    _vision_supported = False
                else:
                    logger.warning(f'多模态分析失败，降级：{e}')
        if not result:
            from langchain.messages import HumanMessage
            result = _call_llm([HumanMessage(content=text_prompt)])
            # 明确标注这次是纯文本结论，避免下游误以为"已经看过画面"
            if not vision_attempted:
                reason = '模型不支持图像输入' if _vision_supported is False else '未能抽取画面'
                result = f'（未使用画面分析：{reason}）\n{result}'
    except Exception as e:  # noqa: BLE001
        logger.warning(f'素材分析失败：{e}')
        result = ''
    finally:
        for name in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    if not result:
        return ''
    result = result.strip()[:MAX_ANALYSIS_CHARS]
    header = f'素材：{os.path.basename(path)}（{meta}）'
    out = f'{header}\n{result}'
    _cache[cache_key] = out
    return out


# ── 用户显式参数提取 ──

# 开关型参数（不需要取值）。必须在取值判断之前检查，否则会吞掉后面的普通词语，
# 例如「加 -an 去掉音轨」会把「去掉音轨」当成 -an 的值。
_PARAM_FLAGS = {'-an', '-vn', '-sn', '-dn', '-shortest', '-y', '-n', '-nostdin',
                '-copyts', '-re', '-benchmark', '-ignore_unknown'}

# 需要取值的常用参数（用于把 "-crf 18" 整体识别出来）。
# 与 tools.VALUE_OPTS 不同：这里只关心"用户明确写出的处理参数"。
_PARAM_WITH_VALUE = {
    '-crf', '-preset', '-tune', '-profile', '-level', '-pix_fmt', '-c:v', '-c:a', '-c',
    '-vcodec', '-acodec', '-codec', '-b:v', '-b:a', '-r', '-s', '-vf', '-af',
    '-filter_complex', '-filter:v', '-filter:a', '-ar', '-ac', '-q:v', '-q:a',
    '-g', '-movflags', '-maxrate', '-bufsize', '-minrate', '-threads', '-ss', '-t',
    '-to', '-map', '-f', '-aspect', '-colorspace', '-color_primaries',
    '-color_trc', '-tag:v', '-metadata', '-vsync', '-fps_mode',
}


def _strip_punct(tok: str) -> str:
    """去掉紧贴在参数上的中英文标点（用户常写「-crf 18，画质优先」）。"""
    return tok.strip('，。、；：""\'\'（）()[]【】《》,.;:!?')


def extract_explicit_params(text: str) -> list:
    """从用户文本里挑出他**明确写出**的 ffmpeg 参数，按出现顺序返回。

    只做保守识别：必须是 `-xxx` 形式，且对取值型参数要求后面紧跟一个不以 `-`
    开头的值。这样 `把 -crf 18 的片子转成 mp4` 能提出来，而普通负号不会误判。
    """
    if not text:
        return []
    tokens = text.split()
    found = []
    i = 0
    while i < len(tokens):
        bare = _strip_punct(tokens[i])
        if bare in _PARAM_FLAGS:
            # 开关型：不吃后面的词
            found.append(bare)
        elif bare in _PARAM_WITH_VALUE:
            val = ''
            if i + 1 < len(tokens):
                cand = _strip_punct(tokens[i + 1])
                if cand and not cand.startswith('-'):
                    val = cand
                    i += 1
            found.append(f'{bare} {val}'.strip() if val else bare)
        i += 1
    # 去重保序
    seen, out = set(), []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
