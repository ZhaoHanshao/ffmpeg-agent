"""构建 BGE-small-zh-v1.5 的 ONNX 运行时嵌入模型（构建期工具）。

- 从 backend/data/bge_small（PyTorch 版，已缓存）导出 fp32 model.onnx，再动态 int8 量化。
- 输出到 backend/data/bge_onnx/：model.onnx + tokenizer.json（供 tokenizers 库使用）。
- 只有本机（dev venv，含 torch）在打包前运行一次；产物 backend/data/bge_onnx 进 zip/exe，
  运行时不再需要 torch / transformers / sentence_transformers / scipy / sklearn。

用法：.venv\\Scripts\\python.exe backend\\build_bge_onnx.py
"""
import os
import shutil
import sys

CACHE = 'backend/data/bge_small'
OUT = 'backend/data/bge_onnx'


def main():
    import onnx
    import onnxruntime
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import AutoModel, AutoTokenizer

    src = os.path.abspath(CACHE)
    out = os.path.abspath(OUT)
    assert os.path.isdir(src), f'未找到 {src}，请先运行过一次应用或预先下载 BGE 模型'

    os.makedirs(out, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(src)
    model = AutoModel.from_pretrained(src)
    model.eval()

    # 导出 fp32 model.onnx（动态 batch/seq）
    profile_inputs = tokenizer(['导出示例文本，用于固定动态形状'], return_tensors='pt', padding=True, truncation=True, max_length=512)
    torch.onnx.export(
        model,
        (profile_inputs['input_ids'], profile_inputs['attention_mask']),
        os.path.join(out, 'model-fp32.onnx'),
        input_names=['input_ids', 'attention_mask'],
        output_names=['last_hidden_state'],
        dynamic_axes={
            'input_ids': {0: 'batch', 1: 'seq'},
            'attention_mask': {0: 'batch', 1: 'seq'},
            'last_hidden_state': {0: 'batch', 1: 'seq'},
        },
        opset_version=18,  # torch 2.12 dynamo 导出默认 opset 18；14 会因 LayerNormalization 无旧版本降级失败
    )

    # 验证 fp32 输出
    sess32 = onnxruntime.InferenceSession(os.path.join(out, 'model-fp32.onnx'), providers=['CPUExecutionProvider'])
    emb32 = sess32.run(None, {'input_ids': profile_inputs['input_ids'].numpy(), 'attention_mask': profile_inputs['attention_mask'].numpy()})[0]

    # 动态 int8 量化（保持模型语义，体积 ~25MB）
    quantize_dynamic(
        os.path.join(out, 'model-fp32.onnx'),
        os.path.join(out, 'model.onnx'),
        weight_type=QuantType.QInt8,
        per_channel=True,
    )

    # 校验 int8 与 fp32 输出接近
    sess8 = onnxruntime.InferenceSession(os.path.join(out, 'model.onnx'), providers=['CPUExecutionProvider'])
    out8 = sess8.run(None, {'input_ids': profile_inputs['input_ids'].numpy(), 'attention_mask': profile_inputs['attention_mask'].numpy()})[0]
    diff = float(abs(emb32 - out8).max())
    print(f'int8 与 fp32 最大绝对误差: {diff:.6f}')

    # 拷贝 tokenizer 素材
    for f in ('tokenizer.json', 'vocab.txt', 'special_tokens_map.json', 'tokenizer_config.json', 'config.json'):
        s = os.path.join(src, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(out, f))

    os.remove(os.path.join(out, 'model-fp32.onnx'))
    for stale in ('model-fp32.onnx.data', 'model-fp32.onnx.data.json'):
        p = os.path.join(out, stale)
        if os.path.isfile(p):
            os.remove(p)
    onnx.checker.check_model(os.path.join(out, 'model.onnx'))
    size_mb = os.path.getsize(os.path.join(out, 'model.onnx')) / 1e6
    print(f'输出目录: {out}  model.onnx = {size_mb:.1f} MB')


if __name__ == '__main__':
    main()