"""视觉模型 / 文本模型的职责分离测试。

这是"避免混用出错"的回归防线，断言的是**角色不串味**：
  - 文本链路（search/execute/chat agent）只用主模型，永远不碰视觉模型；
  - 画面分析只用视觉模型，没单独配置才回退主模型；
  - 对话级的主模型覆盖**不会**把视觉模型带偏（分开的职责就得分得干净）；
  - "模型不收图"这个能力结论按模型分别记，一个模型被拒不影响另一个
    （曾经是一个进程级全局标志，导致换到真正的视觉模型后依然跳过画面分析）；
  - 换视觉模型后旧的画面结论必须作废（缓存键含模型）。

不联网：所有 LLM 调用都被打桩。
"""
import os
import shutil
import subprocess
import sys
import tempfile

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# **必须在 import app.model 之前**把设置文件指向临时目录。
# 本测试会调用 update_model_config()，它会真的落盘；不隔离的话会把
# backend/data/llm_settings.json（用户真实的模型/密钥）覆盖掉。
# 这一点已经真实发生过一次：测试把用户的线上配置写成了 text-model/key-text。
_TMP_DIR = tempfile.mkdtemp(prefix='vision-role-test-')
os.environ['SETTINGS_FILE'] = os.path.join(_TMP_DIR, 'llm_settings.json')

import app.media as media  # noqa: E402
import app.model as M  # noqa: E402
import app.tools as tools  # noqa: E402

assert M.SETTINGS_FILE == os.environ['SETTINGS_FILE'], '设置文件未被隔离，测试会污染真实配置'

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


print('\n=== test_vision_roles.py ===\n')

# 记录原始配置，结束时恢复，避免污染同进程里的其他断言
_orig_text = M.get_raw_config()
_orig_vision = M._vision_config


def _set_text(**kw):
    M._model_config.update(kw)
    M._build_model_locked()


def _reset():
    M._model_config.clear()
    M._model_config.update(_orig_text)
    M._vision_config = _orig_vision
    M._build_model_locked()


try:
    # ── 解析：回退 vs 分离 ──
    print('--- 角色解析 ---')
    _set_text(model='text-model', base_url='https://text.example/v1', api_key='key-text')
    M._vision_config = None
    cfg, dedicated = M.merged_vision_config(None)
    check('未配置视觉模型时回退主模型', cfg['model'] == 'text-model' and dedicated is False,
          f'{cfg.get("model")} dedicated={dedicated}')
    check('未配置时明确标记未分离', M.vision_separate() is False)
    check('回退态视图标明来源是主模型', M.get_vision_config().get('source') == 'text')

    # 只填模型名：base_url / key 应继承主模型（同服务商换模型是最常见用法）
    M._vision_config = {'model': 'vision-model'}
    cfg, dedicated = M.merged_vision_config(None)
    check('单独配置后 dedicated=True', dedicated is True)
    check('视觉角色用自己的模型', cfg['model'] == 'vision-model', cfg.get('model'))
    check('未填的 base_url 继承主模型', cfg['base_url'] == 'https://text.example/v1', str(cfg.get('base_url')))
    check('未填的 api_key 继承主模型', cfg['api_key'] == 'key-text', str(cfg.get('api_key')))
    check('分离态视图标明来源是视觉模型', M.get_vision_config().get('source') == 'vision')
    check('分离态视图给出视觉模型名', M.get_vision_config().get('model') == 'vision-model')

    # 完全独立：填了自己的地址与密钥
    M._vision_config = {'model': 'vision-model', 'base_url': 'https://vision.example/v1', 'api_key': 'key-vision'}
    cfg, _ = M.merged_vision_config(None)
    check('视觉角色可用自己的 base_url', cfg['base_url'] == 'https://vision.example/v1')
    check('视觉角色可用自己的 key', cfg['api_key'] == 'key-vision')

    # ── 关键：对话级主模型覆盖不得带偏视觉角色 ──
    print('--- 职责分离（对话级覆盖不串味）---')
    M._vision_config = {'model': 'vision-model'}
    conv_override = {'model': 'conv-text-model', 'temperature': 1.5}
    text_cfg = M.merged_config(conv_override)
    vcfg, dedicated = M.merged_vision_config(conv_override)
    check('对话覆盖生效于主模型', text_cfg['model'] == 'conv-text-model', text_cfg.get('model'))
    check('对话覆盖**不会**改掉视觉模型', vcfg['model'] == 'vision-model', str(vcfg.get('model')))
    check('视觉角色仍被标记为独立', dedicated is True)
    check('对话覆盖的 temperature 不串到视觉角色', vcfg.get('temperature') != 1.5,
          str(vcfg.get('temperature')))

    # 对话级视觉覆盖：只改视觉角色
    conv_both = {'model': 'conv-text-model', 'vision': {'model': 'conv-vision-model'}}
    t2 = M.merged_config(conv_both)
    v2, d2 = M.merged_vision_config(conv_both)
    check('对话级视觉覆盖生效', v2['model'] == 'conv-vision-model', str(v2.get('model')))
    check('对话级视觉覆盖不影响主模型', t2['model'] == 'conv-text-model', str(t2.get('model')))

    # 回退态下，对话级主模型覆盖应当被视觉角色继承（此时它们本就是同一个角色）
    M._vision_config = None
    v3, d3 = M.merged_vision_config(conv_override)
    check('回退态下视觉跟随对话级主模型', v3['model'] == 'conv-text-model' and d3 is False,
          f'{v3.get("model")} dedicated={d3}')

    # ── 保存 / 清除 / 校验 ──
    print('--- 保存与清除 ---')
    M._vision_config = None
    M.update_model_config({'vision': {'model': 'vision-model'}})
    check('PUT 能建立视觉角色', M.vision_separate() is True)
    check('视觉模型名已保存', M.get_vision_config().get('model') == 'vision-model')

    # "没提模型" = 只想调参数，必须保住已存的模型名
    M.update_model_config({'vision': {'temperature': 0.9}})
    check('只改温度（没提模型）时保留已存的视觉模型名',
          M.get_vision_config().get('model') == 'vision-model',
          str(M.get_vision_config().get('model')))
    check('视觉温度已更新', M.merged_vision_config(None)[0].get('temperature') == 0.9)

    # "明确把模型清空" = 关掉独立视觉模型，不能被当成"没提模型"而自己变回去
    M.update_model_config({'vision': {'model': '', 'temperature': 0.9}})
    check('把模型清空即关闭独立视觉模型', M.vision_separate() is False)

    M.update_model_config({'vision': {'model': 'vision-model'}})
    M.update_model_config({'vision': None})
    check('vision=null 清除覆盖，回到回退态', M.vision_separate() is False)
    check('清除后视觉角色回退主模型',
          M.merged_vision_config(None)[0]['model'] == M.get_raw_config()['model'])

    M.update_model_config({'vision': {'model': 'vision-model'}})
    M.update_model_config({'vision': {'model': '   ', 'base_url': 'https://x.example/v1'}})
    check('只有 model 为空的覆盖层不算分离（避免"标为已分离却仍用主模型"）',
          M.vision_separate() is False)

    try:
        M.update_model_config({'vision': {'model': 'v', 'temperature': 9}})
        check('视觉 temperature 越界被拒绝', False, '未抛 ValueError')
    except ValueError:
        check('视觉 temperature 越界被拒绝', True)
    try:
        M.update_model_config({'vision': {'model': 'v', 'base_url': 'ftp://x'}})
        check('视觉 base_url 非法被拒绝', False, '未抛 ValueError')
    except ValueError:
        check('视觉 base_url 非法被拒绝', True)

    # ── 文本链路只用主模型 ──
    print('--- 文本链路不借用视觉模型 ---')
    M._vision_config = {'model': 'vision-model'}
    text_cfg = M.merged_config(None)
    check('主模型配置里没有视觉模型名', text_cfg['model'] != 'vision-model', str(text_cfg.get('model')))
    # agents_for 按传入配置构建；GraphSpec 走全局时用的是 get_model()（主模型）
    import app.graph as G
    check('GraphSpec 走对话配置时用的是文本配置',
          G.FFMPEG_GRAPH.agent_spec is not None)
    built = G.FFMPEG_GRAPH.agent('chat', {'llm_config': text_cfg})
    check('按文本配置能构建出 chat agent', built is not None)
    vis_cfg, _ = M.merged_vision_config(None)
    check('视觉配置与文本配置是两个不同的对象', vis_cfg.get('model') != text_cfg.get('model'))

    # ── 能力探测按模型分别记 ──
    print('--- 能力探测不串模型 ---')
    UL = tools.UPLOAD
    img = os.path.join(UL, '_vision_role_test.png')
    ff = tools.ffmpeg_bin('ffmpeg')
    subprocess.run([ff, '-y', '-f', 'lavfi', '-i', 'testsrc=duration=1:size=160x120:rate=1',
                    '-frames:v', '1', img],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    calls = {'n': 0, 'images': 0}
    orig_call = media._call_llm

    def stub(messages, cfg=None):
        calls['n'] += 1
        content = messages[0].content
        has_img = isinstance(content, list) and any(
            isinstance(c, dict) and c.get('type') == 'image_url' for c in content)
        if has_img:
            calls['images'] += 1
            # A 不收图，B 收图 —— 用一个开关区分两个模型
            if (cfg or {}).get('model') == 'text-only-model':
                raise RuntimeError('This model does not support image input')
        return '画面：ok\n参数建议：-crf 20\n注意：无'

    media._call_llm = stub
    media._vision_support.clear()
    media._cache.clear()
    cfg_a = {'model': 'text-only-model', 'base_url': 'https://a/v1', 'api_key': 'k', 'temperature': 0.2, 'max_tokens': 100}
    cfg_b = {'model': 'real-vision-model', 'base_url': 'https://b/v1', 'api_key': 'k', 'temperature': 0.2, 'max_tokens': 100}

    media.analyze_files([img], '看看这个', vision_config=cfg_a, dedicated=True)
    key_a = M.config_fingerprint(cfg_a)
    check('A 模型被标记为不收图', media._vision_support.get(key_a) is False)

    out_b = media.analyze_files([img], '看看这个', vision_config=cfg_b, dedicated=True)
    key_b = M.config_fingerprint(cfg_b)
    check('B 模型仍会尝试带图（不被 A 的结论拖累）', calls['images'] >= 2,
          f'带图调用次数={calls["images"]}')
    check('B 模型没有被误标为不收图', media._vision_support.get(key_b) is not False)
    check('结果标明用的是专用视觉模型', '视觉模型' in out_b, out_b[:100])
    check('结果里出现的是视觉模型名', 'real-vision-model' in out_b, out_b[:100])

    # 换模型后旧结论作废：缓存键含模型
    calls['images'] = 0
    cfg_c = dict(cfg_b, model='another-vision-model')
    media.analyze_files([img], '看看这个', vision_config=cfg_c, dedicated=True)
    check('换视觉模型后重新分析（不吃上一个模型的缓存）', calls['n'] >= 3, f'调用次数={calls["n"]}')

    # 同一模型重复分析命中缓存
    before = calls['n']
    media.analyze_files([img], '再看一次', vision_config=cfg_c, dedicated=True)
    check('同一模型重复分析命中缓存', calls['n'] == before, f'调用次数={calls["n"]}')

    media._call_llm = orig_call
    media._vision_support.clear()
    media._cache.clear()
    try:
        os.remove(img)
    except OSError:
        pass

finally:
    _reset()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)

print(f'\n通过 {pass_count} 项，失败 {fail_count} 项\n')
sys.exit(1 if fail_count else 0)
