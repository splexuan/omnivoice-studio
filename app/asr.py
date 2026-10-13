"""参考音频转写：SenseVoice Small（sherpa-onnx，int8）。

本项目**只用这一个**转写引擎。曾经用的 Whisper large-v3-turbo 已被整体移除
（省 1.6 GB 权重 + 1.6 GB 常驻内存），理由：

  · 权重 0.24 GB vs 1.6 GB；单次转写 0.5s 量级 vs 本机 CPU 上约 20s；
  · `use_itn=True` 时模型自带中文标点，不需要再外挂标点恢复模型。
    对比实测（voices/刘飞.mp3，16.87s），两者转出的字完全一致：
      Whisper   → 在这个信息越来越碎片化的时代我们总在追逐更快更刺激更热闹的内容…
      SenseVoice→ 在这个信息越来越碎片化的时代，我们总在追逐更快、更刺激、更热闹的内容，…
    差别只在标点：Whisper 一个没有，SenseVoice 有 10 个。

覆盖语种为 zh / en / ja / ko / yue。这之外的语种只能手工填写参考文本 ——
不再有 Whisper 兜底，转写不可用时由调用方给出明确提示，而不是静默降级。

模型查找顺序见 config.resolve_sensevoice_dir()；可用 config.ASR_ENABLED 关闭。
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import numpy as np

import config

logger = logging.getLogger("omnivoice-studio.asr")

# SenseVoice 输出里夹带的情感 / 事件标签（<|HAPPY|>、<|Speech|> 等），合成用不到
_TAG_RE = re.compile(r"<\|[^|]+\|>")
# 纯标点/符号的结果视为「没转出内容」：在静音或极短片段上 SenseVoice 会给出
# 一个孤零零的「。」或「.」，那不是一段可以参考的文本，留着它只会让音色
# 挂着一句没有意义的话。
_CONTENT_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

_RECOGNIZER = None
_RECOGNIZER_THREADS = 0
_LOCK = threading.Lock()


def _model_files(directory: Path) -> tuple[Path, Path] | None:
    """返回 (模型, tokens)。量化版优先——精度实测无差别，体积只有 1/4。"""
    for name in ("model.int8.onnx", "model_q8.onnx", "model.onnx"):
        candidate = directory / name
        if candidate.exists() and (directory / "tokens.txt").exists():
            return candidate, directory / "tokens.txt"
    return None


def model_dir() -> Path | None:
    directory = config.resolve_sensevoice_dir()
    if directory is None:
        return None
    return directory if _model_files(directory) else None


def runtime_ready() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:  # noqa: BLE001  —— 缺库 / DLL 加载失败都算不可用
        return False
    return True


def available() -> bool:
    """SenseVoice 这条链路整体是否可用（运行时 + 模型都在）。"""
    return runtime_ready() and model_dir() is not None


def enabled() -> bool:
    """本次是否使用 SenseVoice 转写 = 配置开启 且 运行时与模型都在位。

    关闭或不可用时**不**静默降级到别的引擎（已经没有别的引擎了）：调用方据此
    提示使用者手工填写参考文本。
    """
    if not config.ASR_ENABLED:
        logger.info("参考音频自动转写已关闭（config.ASR_ENABLED=False）")
        return False
    ok = available()
    if not ok:
        logger.warning("SenseVoice 不可用：%s", unavailable_reason())
    return ok


def unavailable_reason() -> str:
    """为什么用不了自动转写 —— 三种原因指向三种完全不同的处理动作，
    提示里必须说准，否则使用者会去查一个并不存在的问题。"""
    if not config.ASR_ENABLED:
        return "自动转写已在 config.py 里关闭（ASR_ENABLED=False）"
    if not runtime_ready():
        return "缺少 sherpa-onnx 运行时（pip install sherpa-onnx）"
    return "未找到 SenseVoice 模型目录（见 config.SENSEVOICE_DIR_CANDIDATES）"


def describe(detail: bool = False) -> str:
    """当前转写引擎。

    detail=False 给界面正文用：只说引擎与设备，够读懂「重新转写会用哪个引擎」；
    detail=True 给排错表与日志用：带上量化精度和实际的模型目录名——机器上有几份
    同款权重时，只有这一档能说清到底加载的是哪一份。
    """
    if not config.ASR_ENABLED:
        return "已关闭（参考文本需手工填写）"
    if not available():
        return f"不可用（{unavailable_reason()}）"
    if not detail:
        return "SenseVoice Small（CPU）"
    directory = model_dir()
    name = directory.name if directory else "sensevoice-small"
    return f"SenseVoice Small（int8，CPU，{name}）"


def short() -> str:
    """更短的一档，给顶部信息行这种横向空间紧张的地方用。"""
    if not config.ASR_ENABLED:
        return "关闭"
    return "SenseVoice" if available() else "不可用"


def _num_threads() -> int:
    import multiprocessing

    if config.ASR_THREADS > 0:
        return config.ASR_THREADS
    return max(2, min(4, multiprocessing.cpu_count()))


def _get_recognizer():
    """常驻一份识别器（约 0.24 GB）。线程数变了就重建。"""
    global _RECOGNIZER, _RECOGNIZER_THREADS
    threads = _num_threads()
    if _RECOGNIZER is not None and _RECOGNIZER_THREADS == threads:
        return _RECOGNIZER

    with _LOCK:
        if _RECOGNIZER is not None and _RECOGNIZER_THREADS == threads:
            return _RECOGNIZER
        directory = model_dir()
        if directory is None:
            raise RuntimeError(unavailable_reason())
        files = _model_files(directory)
        assert files is not None
        model_path, tokens_path = files

        import sherpa_onnx

        logger.info("加载 SenseVoice 识别器（%s，%d 线程）", model_path.name, threads)
        _RECOGNIZER = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_path),
            tokens=str(tokens_path),
            provider="cpu",
            num_threads=threads,
            sample_rate=16000,
            # ITN 打开时模型才会输出标点与规整数字，正是我们要的
            use_itn=True,
            language="",
            debug=False,
        )
        _RECOGNIZER_THREADS = threads
        return _RECOGNIZER


def reset() -> None:
    """丢掉内存里的识别器（模型被换掉时用）。"""
    global _RECOGNIZER, _RECOGNIZER_THREADS
    with _LOCK:
        _RECOGNIZER = None
        _RECOGNIZER_THREADS = 0


def _read_16k(path: str) -> np.ndarray:
    """读成 16 kHz 单声道 float32 一维数组。

    复用 OmniVoice 自己的 load_audio（soundfile + torchaudio 重采样），
    保证送进识别器的样本与送进克隆模型的是同一份。
    """
    from omnivoice.utils.audio import load_audio

    wav = load_audio(str(path), 16000)
    return np.ascontiguousarray(np.squeeze(wav).astype(np.float32))


def transcribe(audio: str | Path | tuple) -> str:
    """转写一段音频，返回带标点的文本。

    audio: 文件路径，或 (waveform, sample_rate) —— 后者按 16 kHz 约定处理。
    """
    recognizer = _get_recognizer()

    if isinstance(audio, tuple):
        samples, sample_rate = audio
        samples = np.asarray(samples, dtype=np.float32).squeeze()
        if sample_rate != 16000:
            import torch
            import torchaudio

            samples = torchaudio.functional.resample(
                torch.from_numpy(np.ascontiguousarray(samples)),
                orig_freq=int(sample_rate), new_freq=16000,
            ).numpy()
        samples = np.ascontiguousarray(samples.astype(np.float32))
    else:
        samples = _read_16k(audio)

    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    text = _TAG_RE.sub("", stream.result.text or "").strip()
    return text if _CONTENT_RE.search(text) else ""
