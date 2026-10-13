"""OmniVoice Studio — 本地自用语音合成工具：全局配置。

路径全部**相对本工具根目录**解析，因此整个目录可以随意搬运 / 压缩分发，
解压到任意路径都能直接运行（无需改任何配置）。

模型权重查找顺序：
  1. 包内自带的 ``checkpoints/``（整合包布局，解压即用）
  2. 环境变量 ``OMNIVOICE_CHECKPOINTS`` 指向的目录
  3. 开发机上的原整合包路径（仅本机有效）
  4. 都找不到 → 回退到 HuggingFace 仓库 id（首次运行联网自动下载）
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 目录（相对本文件 → 随包移动不受影响）
# ---------------------------------------------------------------------------
STUDIO_DIR = Path(__file__).resolve().parent.parent   # 工具根目录
APP_DIR = STUDIO_DIR / "app"
VOICES_DIR = STUDIO_DIR / "voices"      # 音色库：参考音频 / prompt 缓存
OUTPUT_DIR = STUDIO_DIR / "outputs"     # 合成结果
for _d in (VOICES_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 模型权重
# ---------------------------------------------------------------------------
def _first_existing(paths):
    for p in paths:
        if p and Path(p).exists():
            return Path(p)
    return None


_CKPT_CANDIDATES = [
    STUDIO_DIR / "checkpoints",                              # ① 包内自带
    Path(os.environ["OMNIVOICE_CHECKPOINTS"])                # ② 环境变量
    if os.environ.get("OMNIVOICE_CHECKPOINTS") else None,
    Path(r"E:/OmniVoice/OmniVoice-v260717/checkpoints"),     # ③ 开发机原包
]
_CKPT_DIR = _first_existing(_CKPT_CANDIDATES)

MODEL_PATH = (_CKPT_DIR / "OmniVoice") if _CKPT_DIR else None
ASR_MODEL_PATH = (_CKPT_DIR / "whisper-large-v3-turbo") if _CKPT_DIR else None

# 若本地权重不存在，回退到 HuggingFace 仓库 id（会自动联网下载）
MODEL_FALLBACK = "k2-fsa/OmniVoice"
ASR_MODEL_FALLBACK = "openai/whisper-large-v3-turbo"


def resolve_model_path() -> str:
    return str(MODEL_PATH) if (MODEL_PATH and MODEL_PATH.exists()) else MODEL_FALLBACK


def resolve_asr_path() -> str:
    return str(ASR_MODEL_PATH) if (ASR_MODEL_PATH and ASR_MODEL_PATH.exists()) else ASR_MODEL_FALLBACK


# ---------------------------------------------------------------------------
# 运行参数
# ---------------------------------------------------------------------------
# device: "auto" 自动选择（cuda > cpu）；也可强制 "cuda" / "cpu"
DEVICE = "auto"
# 4GB 显存（GTX 1650）建议 float16；CPU 建议 float32
DTYPE = "float16"

# 是否加载 Whisper ASR（用于参考音频自动转写）
LOAD_ASR = True
# ASR 单独放 CPU：Whisper large-v3-turbo 约 1.6GB，GPU 只有 4GB 装不下
ASR_DEVICE = "cpu"

# WebUI
HOST = "127.0.0.1"
PORT = 7865
