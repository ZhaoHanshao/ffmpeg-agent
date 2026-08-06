"""首跑下载 ffmpeg.exe/ffprobe.exe 到 exe 旁 backend\bin\（冻结专用，仅用标准库）。

自动从多源下载官方 zip，解压 bin 下对应 exe；全部失败时抛出异常，
用户可手动放置 exe（tools.ffmpeg_bin 已支持回退）。
"""
import logging
import os
import tempfile
import urllib.request
import zipfile

logger = logging.getLogger(__name__)

# (优先级, 名称, zip 内 bin 路径前缀)
_SOURCES = [
    ('btbn', 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'),
    ('gyan', 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'),
]

_CHUNK = 256 * 1024


def _download(url: str, dest: str, progress=None) -> None:
    logger.info(f'下载 {url}')
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ffmpeg-agent',
        'Accept-Encoding': 'identity',
    })
    size = 0
    total = 0
    with urllib.request.urlopen(req, timeout=60) as resp:
        try:
            total = int(resp.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            total = 0
        with open(dest, 'wb') as f:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                size += len(chunk)
                if total and progress:
                    progress(min(99, int(size * 100 / total)))
    return size > 0


def _extract_exe(zip_path: str, name: str, dest: str) -> bool:
    """从 zip 中抽取 {name}.exe（优先 bin/ 目录里的）到 dest。"""
    with zipfile.ZipFile(zip_path) as z:
        candidates = []
        for info in z.infolist():
            if info.is_dir():
                continue
            base = os.path.basename(info.filename)
            if base.lower() != f'{name}.exe':
                continue
            score = 1 if '/bin/' in info.filename.replace('\\', '/') else 0
            candidates.append((score, info))
        if not candidates:
            return False
        _, info = max(candidates, key=lambda x: x[0])
        with z.open(info) as src, open(dest, 'wb') as out:
            out.write(src.read())
    return True


def ensure_ffmpeg_bin(bin_dir: str, on_progress=None):
    """确保 bin_dir 下存在 ffmpeg.exe 与 ffprobe.exe（已存在则跳过）。download 返回 bool（是否有新下载）。"""
    os.makedirs(bin_dir, exist_ok=True)
    todo = [n for n in ('ffmpeg', 'ffprobe') if not os.path.isfile(os.path.join(bin_dir, f'{n}.exe'))]
    if not todo:
        if on_progress:
            on_progress(100, 'ffmpeg 已就绪')
        return False

    errors = []
    for src_name, url in _SOURCES:
        zip_path = os.path.join(tempfile.gettempdir(), f'ffmpeg-{src_name}.zip')
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
            if on_progress:
                on_progress(5, f'正在下载 ffmpeg（{src_name}，约 100MB，可能需要几分钟）...')
            if not _download(url, zip_path, lambda p: on_progress and on_progress(
                    max(5, int(p * 0.75)), f'正在下载 ffmpeg（{src_name}，约 100MB，可能需要几分钟）...')):
                raise RuntimeError('下载内容为空')
            ok = True
            missing = []
            for n in todo:
                if os.path.isfile(os.path.join(bin_dir, f'{n}.exe')):
                    continue
                if on_progress:
                    on_progress(80, f'解压 {n}.exe ...')
                if not _extract_exe(zip_path, n, os.path.join(bin_dir, f'{n}.exe')):
                    ok = False
                    missing.append(n)
            if ok and not missing:
                if on_progress:
                    on_progress(100, 'ffmpeg/ffprobe 已就绪')
                return True
            errors.append(f'{src_name}: 缺少 {"、".join(missing)}')
        except Exception as e:
            logger.exception('下载 ffmpeg 失败 %s', src_name)
            errors.append(f'{src_name}: {e}')
        finally:
            try:
                if os.path.isfile(zip_path):
                    os.remove(zip_path)
            except OSError:
                pass

    raise RuntimeError(
        '自动下载 ffmpeg 失败（' + '；'.join(errors) + '）。'
        '请手动将 ffmpeg.exe 与 ffprobe.exe 放到 exe 旁的 backend\\bin\\ 目录后重启。'
    )