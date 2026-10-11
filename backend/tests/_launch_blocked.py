"""临时启动器：可选地模拟"未安装 transformers/torch"的环境，然后启动 uvicorn。

用途：在同一个 venv（仍装着 torch/transformers）里，真实测量
「按新 requirements.txt 安装后」的后端启动耗时，而无需卸载任何包。

用法：
  python backend/tests/_launch_blocked.py            # 模拟干净依赖集（拦截）
  python backend/tests/_launch_blocked.py --baseline # 不拦截，测当前环境
"""
import sys
import os

BLOCK = {'transformers', 'torch', 'sentence_transformers'}
_BLOCKED_NAMES = 'transformers, torch, sentence_transformers'


class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in BLOCK:
            raise ImportError('BLOCKED(simulated absent): %s' % name)
        return None


# backend/ 必须进 sys.path，否则 `app.main:app` 无法解析
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if '--real' in sys.argv:
    # 真实环境（重依赖已从 venv 卸载）：不拦截任何导入
    print('[launcher] real environment: no interception, no blocking', flush=True)
elif '--baseline' in sys.argv:
    print('[launcher] baseline: no imports blocked', flush=True)
else:
    sys.meta_path.insert(0, Blocker())
    # 注意：这里不要出现被拦截包的名字，否则启动日志里会出现这些字样，
    # 干扰"进程是否真的加载了它们"的判断。
    print('[launcher] blocking heavy deps -> simulating clean dependency set', flush=True)
    print('[launcher] blocked: %s' % _BLOCKED_NAMES, flush=True)

import uvicorn  # noqa: E402

uvicorn.run('app.main:app', host='127.0.0.1', port=8000, log_level='warning')
