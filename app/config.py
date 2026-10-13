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
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 目录（相对本文件 → 随包移动不受影响）
# ---------------------------------------------------------------------------
STUDIO_DIR = Path(__file__).resolve().parent.parent   # 工具根目录
APP_DIR = STUDIO_DIR / "app"
# 让 `import hardware` 在「python app/app.py」和「从仓库根导入」两种方式下都成立
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# 缓解显存碎片、降低 OOM 风险（Linux 有效；Windows 不支持该项，设了只会告警）
if sys.platform != "win32":
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
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


# 权重子目录名（checkpoints/ 下的布局）
MODEL_SUBDIR = "OmniVoice"

_CKPT_CANDIDATES = [
    STUDIO_DIR / "checkpoints",                              # ① 包内自带
    STUDIO_DIR.parent / "checkpoints",                       # ② 同级目录（开发态常见）
    Path(os.environ["OMNIVOICE_CHECKPOINTS"])                # ③ 环境变量指定
    if os.environ.get("OMNIVOICE_CHECKPOINTS") else None,
]
_CKPT_DIR = _first_existing(_CKPT_CANDIDATES)

MODEL_PATH = (_CKPT_DIR / MODEL_SUBDIR) if _CKPT_DIR else None

# 若本地权重不存在，回退到 HuggingFace 仓库 id（会自动联网下载）。可用环境变量覆盖。
MODEL_FALLBACK = os.environ.get("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")


def weights_ready() -> bool:
    """本地权重是否已就位（未就位时会走联网下载，慢且有失败风险）。"""
    return bool(MODEL_PATH and MODEL_PATH.exists())


def resolve_model_path() -> str:
    return str(MODEL_PATH) if (MODEL_PATH and MODEL_PATH.exists()) else MODEL_FALLBACK


# --- SenseVoice Small（唯一的转写引擎，见 app/asr.py）-----------------------
# 量化 int8 权重约 0.24 GB，纯 CPU 推理，自带中文标点。
SENSEVOICE_SUBDIR = "sensevoice-small"
SENSEVOICE_DIR_CANDIDATES = [
    (_CKPT_DIR / SENSEVOICE_SUBDIR) if _CKPT_DIR else None,          # ① 包内自带
    (STUDIO_DIR / "models" / SENSEVOICE_SUBDIR),                     # ② models/ 布局
    Path(os.environ["OMNIVOICE_SENSEVOICE_DIR"])                     # ③ 环境变量指定
    if os.environ.get("OMNIVOICE_SENSEVOICE_DIR") else None,
    # ④ 复用「创作工作室」已下载的同款官方权重，避免再占 0.24 GB
    Path(os.environ.get("LOCALAPPDATA", "")) / "VideoTranscriptWorkbench" / "models" / "sensevoice-small"
    if os.environ.get("LOCALAPPDATA") else None,
]


def resolve_sensevoice_dir() -> "Path | None":
    """返回可用的 SenseVoice 模型目录；都没找到返回 None。"""
    return _first_existing(SENSEVOICE_DIR_CANDIDATES)


# ---------------------------------------------------------------------------
# 运行参数 —— 默认全部 auto：按本机显卡 / 显存 / 平台自动适配
# 决策规则集中在 app/hardware.py，这里只写「你想强制什么」。
# ---------------------------------------------------------------------------
# 设备："auto"（cuda > xpu > mps > cpu）；也可强制 "cuda" / "cuda:1" / "xpu" / "mps" / "cpu"
DEVICE = "auto"

# 精度："auto" = Ampere 及更新用 bfloat16、老 N 卡/Xe 用 float16、CPU/MPS 用 float32
#       也可强制 "float16" / "bfloat16" / "float32"
DTYPE = "auto"

# 是否自动转写参考音频。唯一引擎是 SenseVoice Small（见 app/asr.py）：
#   True  = 参考文本留空时自动转写（识别器约 0.24 GB，纯 CPU）
#   False = 不加载识别器，必须手工填写「参考音频文本」
# 环境变量 OMNIVOICE_ASR=0 / false / no 可临时关掉，无需改文件。
ASR_ENABLED = os.environ.get("OMNIVOICE_ASR", "1").strip().lower() not in ("0", "false", "no")

# SenseVoice 推理线程数：0 = 自动（2~4，按 CPU 核数）
ASR_THREADS = 0

# 推理步数：0 = 自动（≥6 GiB→32，≥4 GiB→24，更小/CPU→16）
NUM_STEP = 0

# 长文本分段时长（秒）：0 = 自动（≥8 GiB→15s，≥4 GiB→10s，更小→6s，CPU→8s）
# 调小可显著压低峰值显存，代价是分段边界略多
AUDIO_CHUNK_DURATION = 0.0

# WebUI
HOST = "127.0.0.1"
PORT = 7865

# ---------------------------------------------------------------------------
# 适配档位（缓存）
# ---------------------------------------------------------------------------
_PROFILE = None


def profile():
    """本机适配档位（首次调用时探测硬件，之后缓存）。"""
    global _PROFILE
    if _PROFILE is None:
        import hardware  # 同目录，APP_DIR 已加入 sys.path

        _PROFILE = hardware.detect(
            device=DEVICE,
            dtype=DTYPE,
            num_step=NUM_STEP,
            audio_chunk_duration=AUDIO_CHUNK_DURATION,
        )
    return _PROFILE
