"""BGE-small-zh-v1.5 的 ONNX 运行时嵌入实现（无 torch）。

读取由 backend/build_bge_onnx.py 生成的目录（model.onnx + tokenizer.json）：
- 分词用 tokenizers 库（最快 WordPiece，无需 transformers）
- 池化 BGE 官方：CLS token（与 PyTorch 版 1_Pooling config.yaml pooling_mode_cls_token 一致）
- 输出 l2 归一化（与 2_Normalize 一致）
"""
import logging
import os
import sys

import numpy as np

logger = logging.getLogger(__name__)

MAX_SEQ = 512


def _l2_normalize(emb: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(emb, axis=-1, keepdims=True)
    norm[norm == 0] = 1.0
    return emb / norm


def resolve_model_dir():
    """按优先级找到含 model.onnx 的目录：env → 相对路径 → 冻结包内数据。"""
    cands = []
    env = os.getenv('BGE_CACHE_DIR')
    if env:
        cands.append(env)
    cands.append('backend/data/bge_onnx')
    if getattr(sys, 'frozen', False):
        cands.append(os.path.join(sys._MEIPASS, 'backend', 'data', 'bge_onnx'))
    for c in cands:
        if os.path.isfile(os.path.join(c, 'model.onnx')):
            return c
    return cands[0]


class BGEOnnxEmbedding:
    """langchain Embeddings 兼容的 ONNX 嵌入（支持 batch、原样 list[float] 输出）。"""

    def __init__(self, path=None):
        path = path or resolve_model_dir()
        self._model_path = os.path.join(path, 'model.onnx')
        self._tokenizer_path = os.path.join(path, 'tokenizer.json')
        if not (os.path.isfile(self._model_path) and os.path.isfile(self._tokenizer_path)):
            raise RuntimeError(
                f'未找到 ONNX 嵌入模型 {path}（需要 model.onnx + tokenizer.json）。'
                '请先运行 backend/build_bge_onnx.py 生成。'
            )
        self._session = None
        self._tokenizer = None
        self._logger = logger

    def _ensure_load(self):
        if self._session is not None:
            return
        import onnxruntime
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(self._tokenizer_path)
        self._tokenizer.enable_truncation(max_length=MAX_SEQ)
        pad_id = self._tokenizer.token_to_id('[PAD]')
        if pad_id is None:
            pad_id = 0
        self._tokenizer.enable_padding(pad_id=pad_id)
        self._session = onnxruntime.InferenceSession(
            self._model_path,
            providers=['CPUExecutionProvider'],
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_load()
        encoded = [self._tokenizer.encode(t) for t in texts]
        max_len = max(e.ids.__len__() for e in encoded)
        max_len = max(max_len, 1)
        input_ids = np.zeros((len(encoded), max_len), dtype=np.int64)
        attention_mask = np.zeros((len(encoded), max_len), dtype=np.int64)
        for i, e in enumerate(encoded):
            ids = e.ids
            input_ids[i, :len(ids)] = ids
            attention_mask[i, :len(ids)] = 1

        hidden = self._session.run(
            None,
            {'input_ids': input_ids, 'attention_mask': attention_mask},
        )[0]  # [B, L, H]

        cls = hidden[:, 0, :]
        cls = _l2_normalize(cls)
        return cls.astype(np.float32).tolist()

    def embed_documents(self, texts):
        return self._embed(texts)

    def embed_query(self, text):
        return self._embed([text])[0]