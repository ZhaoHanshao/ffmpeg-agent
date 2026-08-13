import logging
import os
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# 在线抓取的 HTML 会缓存到这里;下次在线失败时用本地缓存兜底(避免抓取失败直接瘫痪)
DOC_CACHE_DIR = os.getenv('DOC_CACHE_DIR', 'backend/data')


def _cache_path(url: str) -> str:
    name = os.path.basename(urlparse(url).path) or 'docs.html'
    return os.path.join(DOC_CACHE_DIR, name)


def fetch_and_chunk(url: str) -> list[Document]:
    logger.info(f'正在获取文档: {url}')
    if url.startswith(('http://', 'https://')):
        try:
            resp = requests.get(url, timeout=120, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; FFmpeg-Agent/1.0)'
            })
            resp.raise_for_status()
            html = resp.text
            # 成功抓取时顺带写入本地缓存,作为下次离线/失败时的兜底
            try:
                os.makedirs(DOC_CACHE_DIR, exist_ok=True)
                with open(_cache_path(url), 'w', encoding='utf-8', errors='replace') as f:
                    f.write(html)
            except OSError as e:
                logger.warning(f'写入文档缓存失败(不影响本次构建): {e}')
        except Exception as e:
            local = _cache_path(url)
            if os.path.isfile(local):
                logger.warning(f'在线获取文档失败({e})，使用本地缓存 {local}')
                with open(local, 'r', encoding='utf-8', errors='replace') as f:
                    html = f.read()
            else:
                logger.error(f'在线获取文档失败且无本地缓存: {e}')
                raise
    else:
        # 本地文件（打包期预取缓存，避免运行时依赖 ffmpeg.org）
        with open(url, 'r', encoding='utf-8', errors='replace') as f:
            html = f.read()

    soup = BeautifulSoup(html, 'lxml')
    headings = soup.find_all(['h2', 'h3', 'h4'])

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    docs = []

    for heading in headings:
        h_text = heading.get_text(strip=True)
        if not h_text or 'Table of Contents' in h_text:
            continue

        title = h_text
        if heading.name != 'h2':
            prev_h2 = heading.find_previous('h2')
            if prev_h2:
                parent = prev_h2.get_text(strip=True)
                if parent and 'Table of Contents' not in parent:
                    title = f'{parent} > {h_text}'

        content_parts = []
        for sibling in heading.find_next_siblings():
            if sibling.name in ['h2', 'h3', 'h4']:
                break
            if sibling.name == 'a':
                continue
            if sibling.name == 'table':
                # 表格按行结构化输出(单元格用 | 分隔),避免 get_text 把整表糊成一行
                for tr in sibling.find_all('tr'):
                    cells = [c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])]
                    line = ' | '.join(c for c in cells if c)
                    if line:
                        content_parts.append(line)
                continue
            text = sibling.get_text(strip=True, separator=' ')
            if text:
                content_parts.append(text)

        full_text = '\n'.join(content_parts)
        if not full_text:
            continue

        if len(full_text) > 1500:
            chunks = splitter.split_text(full_text)
            for j, chunk in enumerate(chunks):
                docs.append(Document(
                    page_content=chunk,
                    metadata={'source': url, 'title': f'{title} (Part {j+1})'}
                ))
        else:
            docs.append(Document(
                page_content=full_text,
                metadata={'source': url, 'title': title}
            ))

    logger.info(f'解析完成: 共 {len(docs)} 个文档片段')
    return docs
