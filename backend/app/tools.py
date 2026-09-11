from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from app.db_search import get_text, get_probe_text
from dotenv import load_dotenv
import contextlib
import os, sys, subprocess, shlex, logging, time, tempfile, threading

load_dotenv()

logger = logging.getLogger(__name__)

DOWNLOAD = os.getenv('DOWNLOAD', 'backend/download')
UPLOAD = os.getenv('UPLOAD', 'backend/upload')


class KeyedLockPool:
    """按 key 加锁的锁池（固定条带，天然有界，无需清理）。

    用于把"整个 ffmpeg 进程"的串行改成**只对同一输出文件**串行：
    并发写不同输出文件互不阻塞（原先的全局锁会让一个长转码挡住所有人的请求），
    而写同一输出文件仍然互斥，避免互相覆盖产生损坏文件。

    实现要点：
    - 固定条带数：hash 取模落到有限的锁上，map 不会随处理过的路径无限增长；
      不同路径偶发映射到同一把锁只是轻微多等一会儿，不影响正确性。
    - 一次要拿多把锁（多输出命令）时先按 key 排序，保证全局一致的加锁顺序，
      避免两个请求各持一把、互相等待造成死锁。
    """

    def __init__(self, size=64):
        self._locks = [threading.Lock() for _ in range(max(1, size))]

    def _lock_for(self, key: str):
        return self._locks[hash(key) % len(self._locks)]

    @contextlib.contextmanager
    def acquire(self, keys):
        uniq = sorted({k for k in (keys or []) if k})
        acquired = []
        try:
            for k in uniq:
                lk = self._lock_for(k)
                lk.acquire()
                acquired.append(lk)
            yield
        finally:
            for lk in reversed(acquired):
                lk.release()


# 保护"写同一个输出文件"这一临界区（不再全局串行）
_output_locks = KeyedLockPool()

# 同时在跑的 ffmpeg 进程数上限。分片加锁后并发能力变强，但本机 CPU 是有限的，
# 且 langgraph 的节点各自占一个 asyncio.to_thread 线程——无上限会让多个转码
# 互相抢 CPU 并耗尽默认线程池。设为 1 即退回"完全串行"。
try:
    MAX_CONCURRENT_FFMPEG = max(1, int(os.getenv('FFMPEG_MAX_CONCURRENCY', '4')))
except ValueError:
    MAX_CONCURRENT_FFMPEG = 4
_ffmpeg_slots = threading.Semaphore(MAX_CONCURRENT_FFMPEG)

# 单条命令最长执行时间(秒),超时强制终止,防止失控命令占死线程
try:
    EXEC_TIMEOUT = int(os.getenv('FFMPEG_TIMEOUT', '1800'))
except ValueError:
    EXEC_TIMEOUT = 1800

# 等待子进程的时间切片（秒）。既决定完成检测延迟，也决定停止/超时的响应粒度。
_WAIT_SLICE = 0.03

# ffprobe 是只读分析，正常应在秒级返回；沿用 1800s 会让"卡住的探测"把分析功能
# 挂起半小时，因此单独给一个短得多的默认超时。
try:
    PROBE_TIMEOUT = int(os.getenv('FFPROBE_TIMEOUT', '120'))
except ValueError:
    PROBE_TIMEOUT = 120

# 允许作为 -i 输入的 lavfi 虚拟源(无真实文件路径)
VIRTUAL_SOURCES = {
    'lavfi', 'testsrc', 'testsrc2', 'smptebars', 'smptehdbars', 'color', 'nullsrc',
    'rgbtestsrc', 'yuvtestsrc', 'sine', 'anoisesrc', 'aevalsrc', 'anullsrc',
    'allrgb', 'allyuv', 'pal75bars', 'pal100bars', 'gradients', 'life',
    'cellauto', 'mandelbrot', 'mptestsrc', 'haldclutsrc', 'flite',
}

# 取值型选项(后面跟一个值),用于区分"输出文件"与"选项值",避免误重写 -t 5 / -map 0:v 等
VALUE_OPTS = {
    '-i', '-f', '-t', '-to', '-ss', '-itsoffset', '-map', '-c', '-c:v', '-c:a', '-c:s', '-c:d',
    '-codec', '-codec:v', '-codec:a', '-b', '-b:v', '-b:a', '-minrate', '-maxrate', '-bufsize',
    '-r', '-s', '-aspect', '-vf', '-af', '-filter', '-filter:v', '-filter:a', '-filter_complex',
    '-lavfi', '-vframes', '-frames:v', '-frames:a', '-q', '-qscale', '-q:v', '-q:a', '-crf',
    '-preset', '-tune', '-profile', '-profile:v', '-profile:a', '-level', '-pix_fmt', '-ac',
    '-ar', '-acodec', '-vcodec', '-scodec', '-vol', '-metadata', '-tag', '-tag:v', '-tag:a',
    '-movflags', '-fps_mode', '-fpsmax', '-g', '-keyint_min', '-sc_threshold', '-threads',
    '-x264-params', '-x265-params', '-pass', '-passlogfile', '-max_muxing_queue_size',
    '-start_number', '-vsync', '-async', '-video_size', '-framerate', '-sample_fmt',
    '-ch_layout', '-channel_layout', '-loglevel', '-progress', '-timelimit', '-duration',
    '-muxpreload', '-muxdelay', '-analyzeduration', '-probesize', '-target', '-vtag', '-atag',
    '-stream_loop', '-loop', '-rtsp_transport', '-user_agent', '-headers',
    # ── ffprobe 专有 ──
    # ffprobe 的输入是位置参数，所以这些取值选项若不登记，它们的值
    # （如 `-show_entries stream=width,height` 的 `stream=width,height`）
    # 会被当成文件输入而误拒。注意 `-show_format`/`-show_streams`/`-show_packets`
    # 是**无值**布尔选项，不能放进来。
    '-v', '-loglevel', '-of', '-print_format', '-show_entries', '-select_streams',
    '-o', '-read_intervals', '-count_frames', '-count_packets', '-show_data_hash',
    '-show_program_version', '-show_library_versions', '-show_versions', '-show_error',
}


def find_output_indexes(parts: list) -> list:
    """解析 ffmpeg 参数,返回未被取值型选项消费的裸参数下标(即输出文件位置)。

    修复旧启发式(取"最后一个非 - 开头参数")的缺陷：-t 5 / -map 0:v / -i in.mp4
    等选项值不再被误判为输出。
    """
    outputs = []
    consume_next = False
    for i, p in enumerate(parts):
        if i == 0:
            continue  # 跳过命令名
        if consume_next:
            consume_next = False
            continue
        if p.startswith('-'):
            if p in VALUE_OPTS:
                consume_next = True
            continue
        outputs.append(i)
    return outputs


def _is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def ffmpeg_bin(name: str) -> str:
    """解析 ffmpeg/ffprobe 可执行文件路径：
    - 冻结模式：优先 exe 旁 backend/bin（首跑下载目录），否则包内 _MEIPASS/ffmpeg（旧版回退）
    - dev 模式：返回裸命令名（依赖系统 PATH）
    """
    if _is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands = [
            os.path.join(exe_dir, 'backend', 'bin', f'{name}.exe'),
            os.path.join(sys._MEIPASS, 'ffmpeg', f'{name}.exe'),
        ]
        for c in cands:
            if os.path.isfile(c):
                return c
        raise RuntimeError(
            f'未找到 {name}.exe（请先完成首次运行：应用会自动下载 ffmpeg；'
            f'若自动下载失败，可手动将 ffmpeg.exe/ffprobe.exe 放到 exe 旁 backend\\bin\\ 目录）'
        )
    return name


def split_command(cmd: str) -> list:
    """按 Windows 规则拆分命令，保留路径反斜杠并去掉引用引号。"""
    parts = shlex.split(cmd, posix=False)
    result = []
    for p in parts:
        if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
            p = p[1:-1].replace('""', '"')
        result.append(p)
    return result


def _resolve_under_cwd(path: str) -> str:
    return os.path.realpath(os.path.join(os.getcwd(), path))


def _is_inside_download(value: str) -> bool:
    """判断输出参数是否已经落在 DOWNLOAD 目录内（路径语义，非子串匹配）。

    兼容 `download/x.mp4`、`backend/download/x.mp4`、`./backend/download/x.mp4`
    等等价写法；裸文件名（`x.mp4`）会解析到 cwd，因此返回 False，交由调用方
    重写到 DOWNLOAD 目录——这正是期望行为（evaluated: 提示词要求只写文件名）。
    `-`（stdout 输出）不算。
    """
    v = (value or '').strip()
    if not v or v == '-':
        return False
    base = os.path.realpath(DOWNLOAD)
    real = _resolve_under_cwd(v)
    return real == base or real.startswith(base + os.sep)


def _output_lock_keys(parts: list) -> list:
    """从命令参数里取出"输出文件"的规范化路径，作为加锁 key。

    - 用 realpath 归一化，`download/a.mp4` 与 `backend/download/a.mp4` 落到同一把锁
    - stdout 输出（`-`）不产生文件，跳过
    - 解析失败时退化为空列表（不阻断执行，仍受并发闸门约束）
    """
    keys = []
    try:
        for idx in find_output_indexes(parts):
            val = parts[idx]
            if not val or val == '-':
                continue
            keys.append(os.path.normcase(os.path.realpath(os.path.join(os.getcwd(), val))))
    except Exception as e:  # noqa: BLE001 - 加锁失败不应阻断执行
        logger.warning(f'解析输出路径以加锁失败(本次不加文件锁)：{e}')
    return keys


@contextlib.contextmanager
def _ffmpeg_slot(stop_event=None):
    """限制同时在跑的 ffmpeg 进程数，并在等待期间响应停止请求。

    分片加锁后并发能力变强，但本机 CPU 有限，且每个 graph 节点都占一个
    asyncio.to_thread 线程；无上限会让多个转码互相抢 CPU 并耗尽默认线程池。
    """
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError('ffmpeg 已被用户停止')
    while not _ffmpeg_slots.acquire(timeout=0.5):
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError('ffmpeg 已被用户停止')
    try:
        yield
    finally:
        _ffmpeg_slots.release()


def _validate_input(value: str) -> bool:
    """校验 -i 输入源：
    - 虚拟源(lavfi/testsrc/color 等)放行
    - 网络协议(http/https/rtmp...)一律拒绝(防 SSRF)
    - 本地路径必须落在 UPLOAD/DOWNLOAD 目录内(防任意文件读取)
    """
    v = (value or '').strip()
    if not v:
        return False
    source = v.split('=')[0].split(':')[0]
    if source in VIRTUAL_SOURCES:
        return True
    if '://' in v:
        if v.startswith('file://'):
            v = v[len('file://'):]
        else:
            return False
    try:
        real = os.path.realpath(os.path.join(os.getcwd(), v))
    except Exception:
        return False
    for base in (os.path.realpath(UPLOAD), os.path.realpath(DOWNLOAD)):
        if real == base or real.startswith(base + os.sep):
            return True

    # 裸文件名（不含目录分隔符）按工作目录解析会落在项目根，从而被误判为越界。
    # 但提示词明确要求 agent"输出文件只写文件名"，工具又会把输出重写到 DOWNLOAD，
    # 于是 `ffmpeg -i in.mp4 out.png -i out.png ...` 这类**多步/两遍**命令必然被拒
    # （典型例子：GIF 调色板两遍法 palettegen + paletteuse）。
    # 这里对裸文件名再在 UPLOAD / DOWNLOAD 内查找一次，既修好该场景，
    # 又没有放开任意路径读取。
    is_bare = bool(v) and os.sep not in v and (not os.altsep or os.altsep not in v)
    if is_bare:
        for base in (UPLOAD, DOWNLOAD):
            base_real = os.path.realpath(base)
            cand = os.path.join(base, v)
            if os.path.isfile(cand):
                real2 = os.path.realpath(cand)
                if real2.startswith(base_real + os.sep):
                    return True
    return False


# 这些选项**可以不带参数**，其裸值（如 `-v error` 的 `error`）长得像位置参数，
# 若被当成输入会误判。真正的文件/URL 输入不会长这样。
_BARE_VALUE_TOKENS = {
    'quiet', 'panic', 'fatal', 'error', 'warning', 'info', 'verbose', 'debug', 'trace',
    'json', 'xml', 'ini', 'flat', 'csv', 'compact', 'default', 'null',
}


def _is_stdin_token(val: str) -> bool:
    """`-` / `pipe:0` / `pipe:1` 等标准流，不是文件路径。"""
    v = (val or '').strip().lower()
    return v in ('-', 'pipe:0', 'pipe:1', 'pipe:2', 'pipe:')


def _output_candidate_indexes(parts: list, is_ffmpeg: bool) -> set:
    """只作为"输出"的位置参数下标。

    - ffprobe 不写文件（结果走 stdout），因此**没有**输出位置参数。
    - ffmpeg 的位置参数：位于最后一个 `-i` 之后的是输出；另外中间产物输出
      （`-i a -vf palettegen pal.png -i pal.png ...` 里的 `pal.png`）也应以输出对待。
    """
    if not is_ffmpeg:
        return set()

    positional = []
    consume_next = False
    for i, p in enumerate(parts):
        if i == 0:
            continue
        if consume_next:
            consume_next = False
            continue
        if p.startswith('-') and not _is_stdin_token(p):
            if p in VALUE_OPTS:
                consume_next = True
            continue
        positional.append((i, p))

    last_i = max((i for i, p in enumerate(parts) if p == '-i'), default=-1)
    outputs = {i for i, _ in positional if i > last_i}

    input_values = [parts[i + 1] for i, p in enumerate(parts)
                    if p == '-i' and i + 1 < len(parts)]
    for i, tok in positional:
        if i <= last_i and tok in input_values:
            outputs.add(i)
    return outputs


def _check_inputs(parts: list) -> list:
    """返回所有非法输入源列表（空列表表示全部合法）。

    覆盖面（这是安全边界，改动务必谨慎）：
    - `-i <src>`：ffmpeg 的输入
    - **普通位置参数**：ffprobe 不用 `-i`，直接把文件/URL 写成位置参数
      （如 `ffprobe -v error C:/Windows/win.ini`）。此前只检查 `-i`，导致
      ffprobe 路径上"任意本地文件读取"与 SSRF 都能绕过，属真实漏洞。
    - 属于输出的位置参数跳过，避免误拒。
    - 某 token 同时作为输入与输出时视为本命令自产的中间产物，放行
      （否则 GIF 调色板两遍法这类多步用法会被拒）。
    """
    is_ffmpeg = bool(parts) and os.path.basename(str(parts[0])).lower() in ('ffmpeg', 'ffmpeg.exe')

    # 先标记"肯定不是输入"的位置：
    #   - `-i` 的取值（已按输入校验过，避免重复校验与误判）
    #   - 输出位置参数（ffmpeg 才会写文件）
    not_input = set()
    for i, p in enumerate(parts):
        if p == '-i' and i + 1 < len(parts):
            not_input.add(i + 1)
    not_input |= _output_candidate_indexes(parts, is_ffmpeg)

    last_i = max((i for i, p in enumerate(parts) if p == '-i'), default=-1)

    candidates = []
    # 1) `-i` 输入
    for i, p in enumerate(parts):
        if p == '-i' and i + 1 < len(parts):
            candidates.append(parts[i + 1])
    # 2) 位置参数形式的输入（ffprobe 的主要写法）
    consume_next = False
    for i, p in enumerate(parts):
        if i == 0:
            continue
        if consume_next:
            consume_next = False
            continue
        if p.startswith('-') and not _is_stdin_token(p):
            if p in VALUE_OPTS:
                consume_next = True
            continue
        if i in not_input:
            continue
        if p.strip().lower() in _BARE_VALUE_TOKENS:
            continue                              # 选项裸值（-v error / -of json）
        if p.startswith(('-', 'pipe:')):
            continue
        if is_ffmpeg and i < last_i:
            continue                              # 最后一个输入之前的位置参数不是输入
        candidates.append(p)

    denied = []
    for val in candidates:
        if _is_stdin_token(val):
            continue
        if not _validate_input(val):
            denied.append(val)
    seen, out = set(), []
    for d in denied:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _resolve_bare_inputs(parts: list) -> bool:
    """把裸文件名形式的**输入**解析成 UPLOAD/DOWNLOAD 里的真实路径。

    为什么必须做：校验阶段允许"裸文件名且存在于 UPLOAD/DOWNLOAD"，但 **ffmpeg/ffprobe
    是相对进程工作目录（仓库根）解析裸文件名的**，那里并没有这个文件。于是出现
    "校验通过、执行却报 No such file"的割裂——典型场景是 GIF 调色板两遍法第二遍
    用 `-i palette.png` 引用上一步的产物。

    覆盖两种写法：
    - `-i palette.png`（ffmpeg 的输入）
    - `ffprobe -v error palette.png`（ffprobe 的位置参数输入）

    只处理裸文件名（不含路径分隔符）且确实存在于两目录之一的情况；同时属于输出
    位置的 token 跳过，避免把输出目标改写掉。
    """
    positional_idx = []
    consume_next = False
    for i, p in enumerate(parts):
        if i == 0:
            continue
        if consume_next:
            consume_next = False
            continue
        if p.startswith('-') and not _is_stdin_token(p):
            if p in VALUE_OPTS:
                consume_next = True
            continue
        positional_idx.append(i)

    output_idx = set(find_output_indexes(parts))
    targets = []
    for i, p in enumerate(parts):
        if p == '-i' and i + 1 < len(parts):
            targets.append(i + 1)
    for i in positional_idx:
        if i not in output_idx:
            targets.append(i)

    changed = False
    for ti in targets:
        val = parts[ti]
        if not val or os.sep in val or (os.altsep and os.altsep in val):
            continue
        for base in (UPLOAD, DOWNLOAD):
            cand = os.path.join(base, val)
            if os.path.isfile(cand):
                parts[ti] = cand
                changed = True
                break
    return changed


def _denied_input_message(denied: list) -> str:
    """区分两种被拒原因，给出能照着做的提示。

    原先一律说"请只使用 get_files 返回的文件"，但用户已在左侧把输出文件加入工作区时
    该提示就自相矛盾了——那种情况用户已经选过了，只是没被识别为输入。
    """
    names = '、'.join(denied)
    in_download = [d for d in denied if os.path.isfile(os.path.join(DOWNLOAD, os.path.basename(d)))]
    if in_download:
        return (f'拒绝执行：输入源 {names} 位于输出目录但**不在本次任务选择的文件里**。'
                f'请在左侧"已完成文件"列表点 ＋ 把它加入工作区后重试；'
                f'不要直接引用未选择的文件。')
    return ('拒绝执行：输入源不在允许目录内或使用了被禁止的网络协议：'
            + names + '。请只使用 get_files 返回的文件。')


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f'{h}:{m:02d}:{sec:02d}' if h else f'{m:02d}:{sec:02d}'


def _first_input_file(run_parts: list):
    """返回第一个 -i 参数指向的本地文件路径(用于探测时长),无则 None。"""
    for i, p in enumerate(run_parts):
        if p == '-i' and i + 1 < len(run_parts):
            val = run_parts[i + 1]
            if val in VIRTUAL_SOURCES or val.split('=')[0].split(':')[0] in VIRTUAL_SOURCES or '://' in val:
                continue
            path = os.path.join(os.getcwd(), val)
            if os.path.isfile(path):
                return path
    return None


def _probe_duration(path: str) -> float:
    """用 ffprobe 快速读取媒体时长(秒),失败返回 0。"""
    try:
        probe = ffmpeg_bin('ffprobe')
        args = [probe, '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', path]
        with tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=fout, stderr=ferr)
            proc.communicate(timeout=15)
            fout.seek(0)
            text = fout.read().decode(errors='replace').strip()
            return float(text) if text else 0.0
    except Exception:
        return 0.0


def _run_binary(run_parts: list, timeout: int, label: str, stop_event=None, proc_box=None, progress_cb=None):
    """执行 ffmpeg/ffprobe 并返回 (returncode, stdout_str, stderr_str)。
    - stdin 指向空设备,避免 ffmpeg 的 Overwrite 等交互提示阻塞
    - 输出写入临时文件(避免管道写满死锁),轮询等待,超时/收到停止信号时强制 kill
    - progress_cb 提供时,增量读取 stdout(ffmpeg -progress pipe:1 输出)回调给上层
    """
    with tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
        proc = subprocess.Popen(args=run_parts, stdin=subprocess.DEVNULL, stdout=fout, stderr=ferr)
        if proc_box is not None:
            proc_box[0] = proc
        try:
            deadline = time.monotonic() + timeout
            last_pos = 0
            # 用 proc.wait(timeout=SLICE) 而不是 "poll + sleep(0.2)"：
            # 后者即使任务只用几十毫秒，也要整整多等一个 200ms 周期才被发现。
            # 实测 79ms 的命令经此函数变成 215ms（多耗 135ms），而它每次执行都会走这里。
            # 切片取 30ms：停止响应与进度刷新依然足够及时，完成延迟降到 ~0。
            while True:
                try:
                    proc.wait(timeout=_WAIT_SLICE)
                    break                      # 进程已结束
                except subprocess.TimeoutExpired:
                    pass
                if stop_event is not None and stop_event.is_set():
                    proc.kill()
                    proc.wait()
                    raise InterruptedError(f'{label} 已被用户停止')
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    ferr.seek(0)
                    err_tail = ferr.read().decode(errors='replace')[-2000:]
                    raise TimeoutError(f'{label} 执行超过 {timeout} 秒，已强制终止。{err_tail}')
                if progress_cb is not None:
                    fout.seek(0, os.SEEK_END)
                    end = fout.tell()
                    if end > last_pos:
                        fout.seek(last_pos)
                        progress_cb(fout.read(end - last_pos).decode(errors='replace'))
                        last_pos = end
            fout.seek(0)
            ferr.seek(0)
            return proc.returncode, fout.read().decode(errors='replace'), ferr.read().decode(errors='replace')
        finally:
            if proc_box is not None:
                proc_box[0] = None


def _format_docs(result) -> list:
    """把检索结果格式化成带标题上下文的文本;查询失败返回错误说明。"""
    if isinstance(result, str):
        return [result]
    contents = []
    for i, item in enumerate(result, 1):
        if not isinstance(item, dict):
            contents.append(f'来源[{i}]，{item}')
            continue
        title = (item.get('title') or '').strip()
        content = (item.get('content') or '').strip()
        header = f'来源[{i}]（{title}）' if title else f'来源[{i}]'
        contents.append(f'{header}，{content}')
    return contents


@tool
def get_command(squry: str):
    """
    根据用户的问题查询ffmpeg文档中相关的内容
    squry:用户的问题
    返回的结果为列表，包括按相关度排序的序号和具体内容
    """
    logger.info('查询知识库')
    logger.info(f'查询内容：{squry[:200]}')
    return _format_docs(get_text(squry))


@tool
def get_probe_command(squry: str):
    """
    根据用户的问题查询ffprobe文档中相关的内容
    squry:用户的问题
    返回的结果为列表，包括按相关度排序的序号和具体内容
    """
    logger.info('查询 ffprobe 知识库')
    logger.info(f'查询内容：{squry[:200]}')
    return _format_docs(get_probe_text(squry))


@tool
def get_files(config: RunnableConfig):
    """
    获取将要执行ffmpeg命令的文件
    文件数量为一个或者多个，返回结果是一个列表
    返回结果：第一个为要处理的文件，即在ffmpeg命令中 -i 后跟着的input
              第二个为处理后的文件存放的地址
    只返回用户选中的文件；未指定选中文件时返回全部
    """
    logger.info('获取文件列表')
    os.makedirs(UPLOAD, exist_ok=True)
    selected = ((config or {}).get('configurable') or {}).get('selected_files') or []
    if selected:
        files = list(dict.fromkeys(p for p in selected if os.path.isfile(p)))
    else:
        files = [os.path.join(UPLOAD, name) for name in os.listdir(UPLOAD)]
    return {
        "需要处理": files,
        "输入目录": UPLOAD,
        "输出目录": DOWNLOAD,
    }


@tool
def execute_command(command: str, config: RunnableConfig):
    """
    执行ffmpeg命令
    参数值：
    command:标准的终端ffmpeg执行命令，例如:ffmpeg -i input.mp4 output.avi
    返回中文常规执行结果
    """
    logger.info('执行命令')
    logger.info(f'原始命令：{command}')

    # 安全校验：只允许以 ffmpeg 开头的命令
    parts = split_command(command)
    cmd_name = parts[0] if parts else ''
    if cmd_name != 'ffmpeg':
        logger.info(f'拒绝执行非 ffmpeg 命令：{cmd_name}')
        return {
            'command': command,
            'command_result': f'拒绝执行非 ffmpeg 命令：{cmd_name}。请直接使用 ffmpeg 命令完成任务。',
        }

    # 输入源安全校验必须在**输出路径重写之前**做：
    # 重写会把 `out.png` 变成 `backend\download\out.png`，于是"某 token 既是 -i 输入
    # 又是本命令输出（中间产物）"就无法再按原样匹配，多步/两遍命令会被误拒。
    denied = _check_inputs(parts)
    if denied:
        return {
            'command': command,
            'command_result': _denied_input_message(denied),
        }

    # 将输出路径强制重写到 DOWNLOAD 目录(按参数语法解析,支持多输出)
    output_indexes = find_output_indexes(parts)
    if output_indexes:
        rewritten = False
        for idx in output_indexes:
            original = parts[idx]
            # 仅当路径尚未落在 DOWNLOAD 目录内时才重写。
            # 这里必须做路径层面的包含判断，而不是子串判断：
            # 旧写法 `DOWNLOAD not in original` 会把 download/out.mp4 这种
            # 已正确但未带前缀的相对路径再拼一次，也会让 upload/download_x/ 这类
            # 恰好包含同名字段的路径被误判。
            if not _is_inside_download(original):
                parts[idx] = os.path.join(DOWNLOAD, os.path.basename(original))
                rewritten = True
        if rewritten:
            command = subprocess.list2cmdline(parts)
            logger.info(f'输出路径已重写至 {DOWNLOAD}/')

    # 裸文件名输入（如两遍法的 `-i palette.png`）解析成真实路径：
    # 输出重写之后做，这样"上一步的产物"一定能被找到；
    # 中间产物若已被重写成绝对路径，此处不会再动它。
    if _resolve_bare_inputs(parts):
        command = subprocess.list2cmdline(parts)
        logger.info('输入路径已补全为实际位置')

    logger.info(f'执行命令：{command}')
    os.makedirs(DOWNLOAD, exist_ok=True)

    conf = ((config or {}).get('configurable') or {})
    stop_event = conf.get('stop_event')
    proc_box = conf.get('proc')

    # 只对"本命令将要写入的输出文件"加锁，而不是全局串行：
    # 写不同输出文件的请求可以真正并行，写同一输出文件的仍互斥（否则会互相覆盖）。
    lock_keys = _output_lock_keys(split_command(command))
    with _output_locks.acquire(lock_keys), _ffmpeg_slot(stop_event):
        try:
            run_parts = split_command(command)
            # （输入源安全校验已在重写输出路径之前完成，见上方注释）
            # 注入 -y 静默覆盖同名输出,替代"每次清空下载目录"的粗暴做法(输出文件可跨任务保留)
            if '-y' not in run_parts and '-n' not in run_parts:
                run_parts.insert(1, '-y')
            # 附加进度输出:ffmpeg -progress pipe:1 写入 stdout(临时文件),解析后实时回传前端
            outputs = find_output_indexes(run_parts)
            has_stdout_output = any(run_parts[i] == '-' for i in outputs)
            if '-progress' not in run_parts and not has_stdout_output:
                run_parts[1:1] = ['-progress', 'pipe:1', '-nostats']

            # 构建进度回调(带节流,2 秒内不重复上报)
            progress_list = conf.get('progress')
            duration = _probe_duration(_first_input_file(run_parts)) if progress_list else 0.0
            last_report = {'t': 0.0}

            def _report_progress(chunk: str):
                if progress_list is None:
                    return
                ms = None
                for line in chunk.splitlines():
                    if line.startswith('out_time_ms='):
                        try:
                            ms = int(line.split('=', 1)[1])
                        except ValueError:
                            pass
                    elif line.startswith('out_time_us='):
                        try:
                            ms = int(line.split('=', 1)[1]) // 1000
                        except ValueError:
                            pass
                if ms is None or ms - last_report['t'] < 2000:
                    return
                last_report['t'] = ms
                secs = ms / 1000
                if duration > 0:
                    pct = min(99, int(secs / duration * 100))
                    progress_list.append(f'转码中 {pct}%（{_fmt_time(secs)} / {_fmt_time(duration)}）')
                else:
                    progress_list.append(f'转码中 已处理 {_fmt_time(secs)}')

            run_parts[0] = ffmpeg_bin('ffmpeg')
            returncode, _, stderr = _run_binary(run_parts, EXEC_TIMEOUT, 'ffmpeg', stop_event, proc_box,
                                                _report_progress if progress_list is not None else None)
            if returncode == 0:
                return {'command': command, 'flag': True, 'command_result': f'{command} 执行成功'}
            else:
                return {
                    'command': command,
                    'command_result': f'{command} 执行失败：{stderr[-2000:]}',
                }
        except OSError as e:
            return {
                'command': command,
                'command_result': f'命令执行异常：{e}',
            }
        except TimeoutError as e:
            return {
                'command': command,
                'command_result': f'命令执行超时：{e}',
            }
        except InterruptedError as e:
            return {
                'command': command,
                'command_result': f'{e}',
            }


@tool
def execute_probe_command(command: str, config: RunnableConfig):
    """
    执行ffprobe命令（只读分析工具，结果输出到标准输出）
    参数值：
    command:标准的终端ffprobe执行命令，例如:ffprobe -v error -show_format input.mp4
    返回中文常规执行结果
    """
    logger.info('执行 ffprobe 命令')
    logger.info(f'原始命令：{command}')

    # 安全校验：只允许以 ffprobe 开头的命令
    parts = split_command(command)
    cmd_name = parts[0] if parts else ''
    if cmd_name != 'ffprobe':
        logger.info(f'拒绝执行非 ffprobe 命令：{cmd_name}')
        return {
            'command': command,
            'command_result': f'拒绝执行非 ffprobe 命令：{cmd_name}。请直接使用 ffprobe 命令完成任务。',
        }

    # 输入源安全校验：ffprobe 不用 `-i`，输入是位置参数，_check_inputs 已覆盖该形式。
    # 这一步曾漏掉 ffprobe 的位置参数，使任意本地文件读取与 SSRF 都能绕过。
    denied = _check_inputs(parts)
    if denied:
        return {
            'command': command,
            'command_result': _denied_input_message(denied),
        }
    # 裸文件名输入补全为真实路径（ffprobe 同样相对工作目录解析）
    if _resolve_bare_inputs(parts):
        command = subprocess.list2cmdline(parts)
        logger.info('输入路径已补全为实际位置')

    conf = ((config or {}).get('configurable') or {})
    stop_event = conf.get('stop_event')
    proc_box = conf.get('proc')

    try:
        run_parts = list(parts)
        run_parts[0] = ffmpeg_bin('ffprobe')
        returncode, stdout, stderr = _run_binary(run_parts, PROBE_TIMEOUT, 'ffprobe', stop_event, proc_box)
        output = stdout.strip()
        if returncode == 0:
            return {
                'command': command,
                'flag': True,
                'command_result': output or f'{command} 执行成功',
            }
        # 失败时保留已有的 stdout：ffprobe 常先打印部分结果再报错
        # （例如某个 stream 有问题），这些内容比只看 stderr 更有用。
        detail = output or stderr[-2000:]
        if output and stderr.strip():
            detail = f'{output}\n[stderr] {stderr[-1000:]}'
        return {
            'command': command,
            'command_result': f'{command} 执行失败：{detail}',
        }
    except OSError as e:
        return {
            'command': command,
            'command_result': f'命令执行异常：{e}',
        }
    except TimeoutError as e:
        return {
            'command': command,
            'command_result': f'命令执行超时：{e}',
        }
    except InterruptedError as e:
        return {
            'command': command,
            'command_result': f'{e}',
        }
