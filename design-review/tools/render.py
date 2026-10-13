"""UI 评审用渲染脚手架：把 app.py 的真实界面跑起来，但不加载模型。

只做两件事：
  1) 打桩 load_model()，避免加载 2.3GB 权重；
  2) 调用真实的 build_ui()（同一个组件树、同一份 _CSS），起服务供截图。
其余（硬件档位、音色库数据）全部走真实代码，保证评审基于真实渲染。
"""
import sys
from pathlib import Path

APP_DIR = Path(r"C:\Game\omnivoice-studio\app")
sys.path.insert(0, str(APP_DIR))

import app as A  # noqa: E402


class FakeModel:
    sampling_rate = 24000
    feature_extractor = None

    def create_voice_clone_prompt(self, **kw):
        return None

    def generate(self, **kw):
        raise RuntimeError("render-only harness")


# 只替换模型加载，其余不动
A.load_model = lambda: (FakeModel(), "cuda")

ui = A.build_ui()

if __name__ == "__main__":
    ui.queue().launch(
        server_name="127.0.0.1",
        server_port=7866,
        show_error=True,
        theme=A._THEME,
        css=A._CSS,
        allowed_paths=[str(A.config.VOICES_DIR), str(A.config.OUTPUT_DIR)],
        quiet=True,
        inbrowser=False,
    )
