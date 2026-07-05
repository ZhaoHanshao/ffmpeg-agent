import requests
from bs4 import BeautifulSoup
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def fetch_and_chunk(url: str) -> list[Document]:
    print(f'正在获取文档: {url}')
    resp = requests.get(url, timeout=120, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; FFmpeg-Agent/1.0)'
    })
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, 'lxml')
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

    print(f'解析完成: 共 {len(docs)} 个文档片段')
    return docs
