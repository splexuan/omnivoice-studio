"""OmniVoice Studio — 本地自用语音合成工具（WebUI 主程序）。

特性：
  - 语音克隆 / 语音设计 / 自动语音
  - 长文本自动分段（由 OmniVoice 内置 chunk 逻辑处理）
  - 输出音量稳定 / 去长静音（postprocess_output）
  - 音色库：保存常用参考音色，一键复用
  - 结果自动保存到 outputs/ 并可直接下载
  - 硬件自适应：自动选 设备 / 精度 / 推理步数 / 分段时长（规则见 app/hardware.py）
    · 支持 NVIDIA CUDA（Ampere+ 用 bfloat16，老卡 float16）、Intel XPU、Apple MPS、纯 CPU
    · 显卡加载失败或显存不足时自动降级：先降步数+缩短分段，实在不行回退 CPU

用法：
    run.bat
    或  .venv\\Scripts\\python.exe app\\app.py
"""

import html
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

# 缓解显存碎片化、降低 OOM 风险（Linux 有效；Windows 不支持该项）
if sys.platform != "win32":
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# 允许直接 python app/app.py 运行（把 app 目录加入 path 以便 import config）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import soundfile as sf
import torch

import config
import hardware
import asr
from omnivoice import OmniVoice, OmniVoiceGenerationConfig, VoiceClonePrompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("omnivoice-studio")

# ---------------------------------------------------------------------------
# 语种与语音设计属性（沿用官方清单）
# ---------------------------------------------------------------------------
from omnivoice.utils.lang_map import LANG_NAMES, lang_display_name  # noqa: E402

_ALL_LANGUAGES = ["Auto"] + sorted(lang_display_name(n) for n in LANG_NAMES)

_CATEGORIES = {
    "性别 Gender": ["男 Male", "女 Female"],
    "年龄 Age": ["儿童 Child", "少年 Teenager", "青年 Young Adult", "中年 Middle-aged", "老年 Elderly"],
    "音调 Pitch": ["极低音调 Very Low", "低音调 Low", "中音调 Moderate", "高音调 High", "极高音调 Very High"],
    "风格 Style": ["耳语 Whisper"],
    # 下面两份清单以模型侧为准（omnivoice/utils/voice_design.py 的 _INSTRUCT_CATEGORIES）。
    # 之前各少了两项，等于「模型支持、但下拉里选不到」，只能靠手写指令绕过去 ——
    # 核对方式见 app/verify_instruct.py。
    "英文口音 Accent (英文)": [
        "美式 American", "英式 British", "澳式 Australian", "加拿大 Canadian",
        "印度 Indian", "韩国 Korean", "日本 Japanese", "中国 Chinese",
        "葡萄牙 Portuguese", "俄罗斯 Russian",
    ],
    "中文方言 Dialect (中文)": [
        "四川话", "陕西话", "河南话", "东北话", "贵州话", "云南话",
        "桂林话", "济南话", "甘肃话", "青岛话", "石家庄话", "宁夏话",
    ],
}

# 显示值 -> 模型期望文本（英文口音用英文，中文方言用中文）
def _to_instruct(selected: list[str]) -> str | None:
    parts = []
    for v in selected:
        if not v or v == "Auto":
            continue
        if v in _DIALECT_VALUES:
            parts.append(v)
        else:
            parts.append(_ACCENT_TO_EN.get(v, v.split(" ")[0].strip()))
    return ", ".join(parts) if parts else None


_DIALECT_VALUES = set(_CATEGORIES["中文方言 Dialect (中文)"])
_ACCENT_TO_EN = {
    "美式 American": "American accent",
    "英式 British": "British accent",
    "澳式 Australian": "Australian accent",
    "加拿大 Canadian": "Canadian accent",
    "印度 Indian": "Indian accent",
    "韩国 Korean": "Korean accent",
    "日本 Japanese": "Japanese accent",
    "中国 Chinese": "Chinese accent",
    "葡萄牙 Portuguese": "Portuguese accent",
    "俄罗斯 Russian": "Russian accent",
}
_GENDER_AGE_PITCH_STYLE = _CATEGORIES["性别 Gender"] + _CATEGORIES["年龄 Age"] + _CATEGORIES["音调 Pitch"] + _CATEGORIES["风格 Style"]


# ---------------------------------------------------------------------------
# 模型加载
# ---------------------------------------------------------------------------
_MODEL: OmniVoice | None = None
_DEVICE: str = "cpu"
_PROFILE = None          # 实际生效的档位（可能因回退而变成 CPU 档）
_LOAD_NOTE: str = ""     # 加载期的降级提示，显示到界面上
_MODEL_LOCK = threading.Lock()
_THEME = None


def active_profile():
    """实际生效的适配档位；模型尚未加载时返回探测档位。"""
    return _PROFILE if _PROFILE is not None else config.profile()


def _load_on(prof) -> OmniVoice:
    model_path = config.resolve_model_path()
    # 转写不归主模型管：本项目已整体移除 Whisper 兜底（省 1.6 GB 权重 +
    # 1.6 GB 常驻内存），参考文本一律来自 app/asr.py 的 SenseVoice 或手工填写。
    # load_asr=False 是硬约定 —— 置 True 会去下载 asr_model_name 指向的权重。
    logger.info("加载模型: %s (device=%s, dtype=%s, 转写=%s)",
                model_path, prof.device, prof.dtype, asr.describe())
    return OmniVoice.from_pretrained(
        model_path,
        device_map=prof.device,
        dtype=prof.torch_dtype,
        load_asr=False,
    )


def load_model() -> tuple[OmniVoice, str]:
    """加载模型。显卡侧失败（显存不足 / 精度不支持 / 驱动不匹配）时自动回退 CPU。

    加锁：WebUI 可能同时触发「保存音色后预热」与「用户点生成」两条路径，
    没有锁会各加载一份 2.3 GB 权重，直接 OOM。
    """
    global _MODEL, _DEVICE, _PROFILE, _LOAD_NOTE
    if _MODEL is not None:
        return _MODEL, _DEVICE

    with _MODEL_LOCK:
        if _MODEL is not None:            # 双检：等锁期间可能已被别人加载好
            return _MODEL, _DEVICE

        prof = config.profile()
        if not config.weights_ready():
            logger.warning("本地权重未找到（%s），将回退联网下载，请耐心等待…",
                           config.resolve_model_path())

        try:
            model = _load_on(prof)
        except Exception as e:  # noqa: BLE001
            if prof.backend == "cpu":
                raise
            _LOAD_NOTE = (
                f"{prof.backend.upper()} 加载失败（{type(e).__name__}: {e}），已自动回退 CPU 运行。"
                "CPU 推理会明显变慢；若原因是显存不足，可先调低推理步数/缩短参考音频再重启。"
            )
            logger.warning(_LOAD_NOTE)
            if prof.backend == "cuda":
                torch.cuda.empty_cache()
            prof = hardware.detect(device="cpu")
            model = _load_on(prof)

        _MODEL, _DEVICE, _PROFILE = model, prof.device, prof
        logger.info("模型加载完成：%s", prof.short())
        return model, prof.device


# ---------------------------------------------------------------------------
# 音色库（保存 参考音频 + 参考文本）
# ---------------------------------------------------------------------------
def list_voices() -> list[str]:
    return sorted(p.stem for p in config.VOICES_DIR.glob("*.json"))


def load_voice_meta(name: str) -> dict | None:
    p = config.VOICES_DIR / f"{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _voice_rows() -> list[list[str]]:
    """音色库表格的数据行。"""
    return [[n, (load_voice_meta(n) or {}).get("ref_text", "")] for n in list_voices()]


def _unique_name(base: str) -> str:
    base = base.strip() or "voice"
    name, i = base, 1
    while (config.VOICES_DIR / f"{name}.json").exists():
        i += 1
        name = f"{base}-{i}"
    return name


# ---------------------------------------------------------------------------
# 参考音频的读取 / 裁剪 / 落盘
# ---------------------------------------------------------------------------
# 参考音频的时长是合成耗时的第一变量：编码出的序列长度随它线性增长，而单步前向
# 的耗时对序列长度是超线性的（本机实测 3s→49ms、10s→879ms、16.9s→1615ms）。
# 所以「裁短」不是缩略图式的美化，而是最有效的一次性能优化。
#
# 裁剪放在这里而不是界面层，是为了只有一处真相：写进音色库的就是最终那份音频，
# 不存在「原始文件 + 一份裁剪参数」这种会互相失配的组合。
TRIM_MIN_SECONDS = 2.0          # 低于这个长度不足以稳定克隆，界面据此警告
TRIM_RECOMMENDED = (3.0, 10.0)  # 官方建议的参考音频区间


def read_reference_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """读成 (float32 单声道波形, 采样率)。多声道取平均，**不重采样**。

    与 asr._read_16k 的区别在意图：那个只要「够识别」，这个的结果会被写回音色库
    成为正式参考音频，重采样只会平白损失质量。
    """
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return np.ascontiguousarray(mono, dtype=np.float32), int(sr)


def audio_duration(path: str | Path) -> float:
    """音频时长（秒）。读不出来返回 0.0 —— 界面上显示「—」即可，不该抛错。"""
    try:
        info = sf.info(str(path))
        return info.frames / max(info.samplerate, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def trim_reference_audio(waveform: np.ndarray, sr: int,
                         start: float, end: float) -> np.ndarray:
    """按秒裁剪波形；start/end 夹到合法范围内。

    选区被拖得几乎为零时退回整段 —— 宁可存长音频，也不要存出一个空文件
    让后续每一步都莫名其妙地失败。
    """
    total = len(waveform) / max(int(sr), 1)
    start = max(0.0, min(float(start), total))
    end = max(start, min(float(end), total))
    if end - start < 0.05:
        return waveform
    return np.ascontiguousarray(waveform[int(start * sr):int(end * sr)])


def write_reference_audio(waveform: np.ndarray, sr: int, dst: str | Path) -> None:
    """写成 16-bit PCM wav。

    统一成 wav 是有意的：参考音频的后续读取（转写重采样、编码）都在本地，
    16-bit PCM 在这条链路上无可闻损失，却省掉了 mp3/m4a 解码在各家的差异。
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), waveform, sr, subtype="PCM_16")


# ---------------------------------------------------------------------------
# 音色 prompt 缓存：把「参考音频 → prompt」这一步只算一次
# ---------------------------------------------------------------------------
# 「参考音频 → 音色 prompt」里有两处很贵，而且都与「要合成的文本」无关：
#   1) 没有参考文本时转写（SenseVoice 约 0.5s）
#   2) 把音频编码成 ref_audio_tokens
# 所以对音色库里的每个音色只做一次，把结果存成 voices/<name>.pt，
# 之后每次合成都直接 VoiceClonePrompt.load()（毫秒级）。官方 0.2.1 提供
# save/load 正是为此。临时上传的音频没有稳定身份，不缓存。
_PROMPT_CACHE_VERSION = 1
# 参考文本的来源标记，进缓存指纹（详见 _prompt_fingerprint）
_ASR_TAG = "sensevoice"
_PROMPT_LOCK = threading.Lock()   # 同一份参考音频的转写+编码不并发做两遍


def model_loaded() -> bool:
    return _MODEL is not None


def _voice_prompt_file(name: str) -> Path:
    return config.VOICES_DIR / f"{name}.pt"


def _voice_name_for_audio(ref_audio: str | None) -> str | None:
    """参考音频若取自音色库，返回音色名；否则 None。"""
    if not ref_audio:
        return None
    try:
        p = Path(ref_audio)
        if p.resolve().parent != config.VOICES_DIR.resolve():
            return None
    except OSError:
        return None
    return p.stem if (config.VOICES_DIR / f"{p.stem}.json").exists() else None


def _prompt_fingerprint(ref_audio: str, preprocess: bool) -> dict:
    """缓存有效性判据。刻意不含文件名：重命名音色时源文件会被改名，
    但 size/mtime 不变，缓存不该因此失效。"""
    try:
        st = Path(ref_audio).stat()
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = -1, -1
    return {"version": _PROMPT_CACHE_VERSION, "size": size, "mtime": mtime,
            "model": Path(config.resolve_model_path()).name,
            # 参考文本由哪个引擎转出。这一项是 provenance 而非配置，所以写成
            # 常量：0.2.x 早期版本用的是 Whisper large-v3-turbo（转出来没有标点），
            # 引擎一换旧缓存就必须重建，否则界面上会一直挂着旧文本。写常量还能
            # 避免「关掉 ASR」这种开关动作误伤已经算好的缓存。
            "asr": _ASR_TAG,
            "preprocess_prompt": bool(preprocess)}


def _transcribe_any(audio, what: str) -> tuple[str, str]:
    """转写一段音频，返回 (文本, 引擎名)。

    唯一引擎是 SenseVoice Small。不可用或没转出内容时返回空文本 + 说明，
    由调用方决定怎么提示 —— 这里**不**静默换引擎，因为已经没有别的引擎了。

    audio 可以是文件路径，也可以是 (波形, 采样率) —— 后者用于转写刚落盘的
    裁剪结果，省掉一次「写临时文件再读回」的往返。
    """
    if not asr.enabled():
        return "", "转写未启用"
    try:
        t0 = time.time()
        text = asr.transcribe(audio)
    except Exception as e:  # noqa: BLE001
        logger.warning("SenseVoice 转写失败（%s: %s）", type(e).__name__, e)
        return "", "SenseVoice 失败"
    if not text:
        logger.warning("SenseVoice 转写结果为空（%s 里可能没有人声）", what)
        return "", "SenseVoice 无结果"
    logger.info("SenseVoice 转写完成（%s，耗时 %.2fs）", asr.describe(), time.time() - t0)
    return text, "SenseVoice"


def _transcribe_ref(ref_audio: str) -> tuple[str, str]:
    return _transcribe_any(ref_audio, "参考音频")


def transcribe_audio(waveform: np.ndarray, sr: int) -> tuple[str, str]:
    """转写内存里的音频（例如界面上刚拖好的选区，还没落盘）。"""
    return _transcribe_any((waveform, sr), "裁剪后的参考音频")


def _prompt_text(ref_audio: str, ref_text: str | None) -> tuple[str, str]:
    """这次建 prompt 该用哪段参考文本：手填优先，没填才转写。

    返回 (文本, 文本来源)。**空文本是错误状态**，调用方必须拦下来报错，
    绝不能把 None 交给模型 —— OmniVoice 在 ``ref_text=None`` 时会就地调用
    ``load_asr_model()`` 联网下载 Whisper 权重（1.6 GB），正是本次要移除的东西。
    """
    text = (ref_text or "").strip()
    return (text, "手填") if text else _transcribe_ref(ref_audio)


def _require_text(text: str) -> None:
    """参考文本拿不到时给出可操作的报错，而不是让模型偷偷去下载 Whisper。"""
    if text:
        return
    if not asr.enabled():
        reason = asr.unavailable_reason()
    else:
        reason = "SenseVoice 没能从这段音频里转出内容（可能没有人声，"\
                 "或语种不在 zh/en/ja/ko/yue 内）"
    raise RuntimeError(
        f"{reason}。请到「音色库」给这个音色填写参考文本（或点「重新转写」），再重新生成。")


def _read_cached_prompt(name: str, ref_audio: str, ref_text: str | None,
                        preprocess: bool):
    """尝试命中缓存；命中返回 (prompt, True)，否则 (None, False)。"""
    pt = _voice_prompt_file(name)
    if not pt.exists():
        return None, False
    meta = load_voice_meta(name) or {}
    cache = meta.get("prompt_cache") or {}
    want = _prompt_fingerprint(ref_audio, preprocess)
    incoming = (ref_text or "").strip()
    # 参考文本允许两种输入都命中：空（交给 ASR），或就是缓存时用的那段文本
    if incoming not in ("", (cache.get("text") or "").strip()):
        return None, False
    if not all(cache.get(k) == want[k] for k in want):
        return None, False
    try:
        return VoiceClonePrompt.load(str(pt)), True
    except Exception as e:  # noqa: BLE001
        logger.warning("音色「%s」的 prompt 缓存已损坏，将重建：%s", name, e)
        return None, False


def _get_clone_prompt(model, ref_audio: str, ref_text: str | None, preprocess: bool):
    """返回 (prompt, 是否命中缓存)。仅对音色库里的音色启用缓存。"""
    name = _voice_name_for_audio(ref_audio)
    if name is None:            # 临时上传的音频：没有稳定身份，不缓存
        text, _source = _prompt_text(ref_audio, ref_text)
        _require_text(text)
        return model.create_voice_clone_prompt(
            ref_audio=ref_audio, ref_text=text,
            preprocess_prompt=preprocess), False

    hit, cached = _read_cached_prompt(name, ref_audio, ref_text, preprocess)
    if cached:
        return hit, True

    with _PROMPT_LOCK:
        # 双检：等锁期间可能已被另一条路径（如保存后的预热）建好
        hit, cached = _read_cached_prompt(name, ref_audio, ref_text, preprocess)
        if cached:
            return hit, True

        text, source = _prompt_text(ref_audio, ref_text)
        _require_text(text)
        prompt = model.create_voice_clone_prompt(
            ref_audio=ref_audio, ref_text=text,
            preprocess_prompt=preprocess)
        try:
            prompt.save(str(_voice_prompt_file(name)))
            meta = load_voice_meta(name) or {}
            meta["prompt_cache"] = {**_prompt_fingerprint(ref_audio, preprocess),
                                    "text": prompt.ref_text,
                                    "text_source": source}
            if not (meta.get("ref_text") or "").strip() and prompt.ref_text:
                meta["ref_text"] = prompt.ref_text   # 回填转写结果，界面上可见
            (config.VOICES_DIR / f"{name}.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("音色「%s」的 prompt 缓存写入失败（不影响本次生成）：%s", name, e)
        return prompt, False


def warm_voice_prompt(name: str) -> str:
    """保存音色后把 prompt 建好，让首次生成就直接命中缓存。

    模型未加载时不做（避免仅「保存」就触发 2.3 GB 加载）——留给首次生成，
    效果一样：转写与编码各只做一次。返回一句给界面显示的说明。
    """
    meta = load_voice_meta(name) or {}
    ref = meta.get("ref_audio")
    if not ref or not Path(ref).exists():
        return ""
    if _MODEL is None:
        if (meta.get("ref_text") or "").strip():
            return "当前未加载模型；首次生成时会顺带把这段参考音频编码并缓存。"
        return "首次生成该音色时会自动转写并缓存，之后永久复用。"

    def _worker():
        try:
            _get_clone_prompt(_MODEL, ref, meta.get("ref_text") or None, True)
        except Exception as e:  # noqa: BLE001
            logger.warning("音色「%s」缓存预热失败：%s", name, e)

    threading.Thread(target=_worker, daemon=True).start()
    return "正在后台转写并缓存参考音频，之后每次生成都会直接复用。"


def retranscribe_voice(name: str) -> tuple[str, str]:
    """用当前转写引擎重听一遍参考音频，覆盖参考文本，并作废旧的 prompt 缓存。

    为什么必须连 prompt 一起重建：参考文本被存进了 VoiceClonePrompt.ref_text，
    生成时会拼在合成文本前面（`omnivoice._combine_text`），所以只改界面上的
    文字、不重建 prompt，合成结果不会跟着变。

    模型没加载时只更新文本 + 删缓存，下次生成时自动重建——不为了点一下按钮
    就拉起 2.3 GB 权重。返回 (给界面看的话, 级别)。
    """
    meta = load_voice_meta(name)
    if meta is None:
        return f"找不到音色「{name}」。", "error"
    ref = meta.get("ref_audio")
    if not ref or not Path(ref).exists():
        return "这个音色没有可用的参考音频文件。", "error"

    text, engine = _transcribe_ref(ref)
    if not text:
        reason = (asr.unavailable_reason() if not asr.enabled()
                  else "SenseVoice 没能从这段音频里转出内容（可能没有人声）")
        return f"没有转写出内容：{reason}。", "error"

    meta["ref_text"] = text
    _voice_prompt_file(name).unlink(missing_ok=True)   # 旧 prompt 里的文本已过期
    meta.pop("prompt_cache", None)
    (config.VOICES_DIR / f"{name}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if _MODEL is None:
        return (f"已用 {engine} 重新转写并更新参考文本；"
                "下次生成时会顺带重建这个音色的缓存。", "ok")
    try:
        _get_clone_prompt(_MODEL, ref, text, True)
    except Exception as e:  # noqa: BLE001
        return (f"参考文本已更新（{engine}），但音色缓存重建失败：{e}。"
                "下次生成时会再试一次。", "warn")
    return f"已用 {engine} 重新转写，并重建了音色缓存（下次生成直接命中）。", "ok"


# ---------------------------------------------------------------------------
# 生成核心
# ---------------------------------------------------------------------------
def _save_wav(waveform_int16: np.ndarray, sr: int, tag: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = config.OUTPUT_DIR / f"{tag}-{ts}.wav"
    sf.write(str(out), waveform_int16, sr, subtype="PCM_16")
    return str(out)


def _is_oom(e: BaseException) -> bool:
    """CUDA/XPU/MPS 的 OOM 异常类型各不相同，统一按消息判断。"""
    return isinstance(e, torch.cuda.OutOfMemoryError) or "out of memory" in str(e).lower()


def _friendly_error(e: BaseException) -> str:
    """把上游又长又生的报错压成一句「照着做就行」的话。

    上游会把「合法值清单」整段倒出来（几十行），而界面已经把可选项限定住了，
    这段清单对使用者没有增量信息。完整报错仍会写进日志，不算丢失。

    例外是「附加指令」里手写的内容 —— 那里没有下拉兜着，校验只可能在这一步发生，
    所以模型给出的「你是不是想写 X」必须带出来，否则用户只知道自己写错了。
    """
    msg = str(e)
    # 以下三条是实测确认的模型规则（并非「中英文不能混用」——那是上游提示语里的
    # 说法，实测同一指令里中文属性 + 英文口音是可以通过的）。
    # 核对脚本：app/verify_instruct.py
    if "Cannot mix Chinese dialect and English accent" in msg:
        return ("中文方言与英文口音不能写在同一个指令里：方言只对中文生效，"
                "口音只对英文生效。请去掉其中一类。")
    if "Conflicting instruct items" in msg:
        return "同一类别只能选一项：例如不能同时指定两种口音或两种方言。"
    if "Unsupported instruct items" in msg:
        tips = re.findall(
            r"'([^']*)' -> '[^']*' \(unsupported; did you mean '([^']*)'\?\)", msg)
        if tips:
            hint = "；".join(f"「{a}」→ 是不是想写「{b}」" for a, b in tips)
            return f"有指令值不被模型接受：{hint}。可用值与「语音设计」的属性下拉一致。"
        return ("有指令值不被模型接受。界面下拉里的值都是可用的；"
                "若在「附加指令」里手写过内容，请对照可用值清单修改。")
    return f"生成失败：{type(e).__name__}: {msg}"


def synthesize(
    text: str,
    language: str,
    ref_audio: str | None,
    ref_text: str | None,
    instruct: str | None,
    num_step: int,
    guidance_scale: float,
    denoise: bool,
    speed: float,
    duration: float | None,
    preprocess_prompt: bool,
    postprocess_output: bool,
    chunk_duration: float,
    mode: str,
):
    """统一生成入口，返回 (采样率, int16 波形) 与状态文本。

    显存不足时不直接失败，而是自动降级重试：步数减半 → 分段时长减半，最多降到
    steps=8 / 分段 3s；仍失败才报错。
    """
    if not text or not text.strip():
        return None, "请先输入要合成的文本。", "warn"

    model, _ = load_model()
    prof = active_profile()

    gen_config = OmniVoiceGenerationConfig(
        num_step=int(num_step or prof.num_step),
        guidance_scale=float(guidance_scale) if guidance_scale is not None else 2.0,
        denoise=bool(denoise),
        preprocess_prompt=bool(preprocess_prompt),
        postprocess_output=bool(postprocess_output),
        audio_chunk_duration=float(chunk_duration or prof.audio_chunk_duration),
    )

    lang = language if (language and language != "Auto") else None
    kw: dict = {"text": text.strip(), "language": lang, "generation_config": gen_config}

    prompt_cached = False

    if speed is not None and float(speed) != 1.0:
        kw["speed"] = float(speed)
    if duration is not None and float(duration) > 0:
        kw["duration"] = float(duration)

    if mode == "clone":
        if not ref_audio:
            return None, "音色克隆需要上传参考音频，或在音色库中选择一个音色。", "warn"
        kw["voice_clone_prompt"], prompt_cached = _get_clone_prompt(
            model, ref_audio, ref_text, bool(preprocess_prompt))

    if instruct and instruct.strip():
        kw["instruct"] = instruct.strip()

    degrades: list[str] = []
    t0 = time.time()
    while True:
        try:
            audio = model.generate(**kw)
            break
        except Exception as e:  # noqa: BLE001
            if not _is_oom(e):
                logger.warning("生成失败: %s", e)   # 完整报错留在日志里
                return None, _friendly_error(e), "error"
            if prof.backend == "cuda":
                torch.cuda.empty_cache()
            if gen_config.num_step <= 8 and gen_config.audio_chunk_duration <= 3.0:
                return None, (
                    "显存不足：已自动降到最低步数与最短分段仍失败。"
                    "请缩短文本或参考音频，或把 config.DEVICE 改为 \"cpu\"，或换更小的模型配置。"
                ), "error"
            gen_config.num_step = max(8, gen_config.num_step // 2)
            gen_config.audio_chunk_duration = max(3.0, gen_config.audio_chunk_duration / 2)
            msg = (f"显存不足，自动降级重试 → steps={gen_config.num_step}、"
                   f"分段={gen_config.audio_chunk_duration:.0f}s")
            degrades.append(msg)
            logger.warning(msg)

    cost = time.time() - t0
    sr = model.sampling_rate or getattr(
        getattr(model, "feature_extractor", None), "sampling_rate", 24000)
    wav_i16 = (np.clip(audio[0], -1.0, 1.0) * 32767).astype(np.int16)

    save_failed = False
    try:
        path = _save_wav(wav_i16, sr, mode)
        saved = f"已保存到 outputs/{Path(path).name}"
    except Exception as e:  # noqa: BLE001
        save_failed = True
        saved = f"写盘失败：{e}"

    dur = len(wav_i16) / sr
    status = (f"音频 {dur:.1f}s，耗时 {cost:.1f}s，RTF {cost / max(dur, 1e-6):.2f}\n{saved}")
    if mode == "clone" and prompt_cached:
        status += "\n音色 prompt 命中缓存（未重复转写与编码参考音频）。"
    if degrades:
        status += "\n" + "；".join(degrades)
    # 有降级或写盘失败 → 用警告色；纯成功 → 成功色
    return (sr, wav_i16), status, ("warn" if (degrades or save_failed) else "ok")


# ---------------------------------------------------------------------------
# WebUI
# ---------------------------------------------------------------------------
# 设计令牌：配色不靠肉眼估，关键对都实测过 WCAG 对比度。
#   强调色/白底 5.8   白字/强调底 5.8   焦点环/白底 5.1
#   成功 8.6  警告 7.8  失败 8.0  中性 8.4（各自字符对底色）
# 状态色的色相彼此拉开 45°+，与强调色（275°）拉开 100°+，不会与"可点"混淆。
# 中性色（页面/卡片/边框/正文）不在这里定义 —— 交给 Gradio 的主题变量，
# 这样深色模式由它一并处理，不会出现「我的令牌变了、组件没变」的半暗状态。
_CSS = """
/* 设计令牌。
   中性色（页面/卡片/边框/正文）**不自己定义**，一律引用 Gradio 的变量：
   Gradio 6 会把注入 CSS 的选择器重写成 `.gradio-container … .contain <选择器>`，
   所以自己写 @media / :root 的深色覆盖会被改到容器内部而永远不匹配（实测确认）。
   让 Gradio 管中性色，深色模式才能跟着它一起正确翻转。
   这里只定义与主题无关、或必须自持的部分：强调色、语义状态色、焦点环、圆角。
*/
:root {
  --ov-accent: oklch(0.52 0.17 275);
  --ov-accent-hover: oklch(0.46 0.17 275);
  --ov-accent-soft: oklch(0.955 0.028 275);
  --ov-on-accent: oklch(1 0 0);              /* 实心按钮上的文字色 */
  --ov-focus: oklch(0.55 0.19 275);
  --ov-ok-bg: oklch(0.960 0.030 155); --ov-ok-fg: oklch(0.380 0.090 155); --ov-ok-bd: oklch(0.880 0.060 155);
  --ov-warn-bg: oklch(0.965 0.045 85); --ov-warn-fg: oklch(0.420 0.090 70); --ov-warn-bd: oklch(0.880 0.070 85);
  --ov-err-bg: oklch(0.960 0.025 25); --ov-err-fg: oklch(0.420 0.150 25);  --ov-err-bd: oklch(0.880 0.070 25);
  --ov-info-bg: oklch(0.965 0.003 265); --ov-info-fg: oklch(0.400 0.020 265); --ov-info-bd: oklch(0.890 0.006 265);
  --ov-radius-control: 8px;    /* 控件与提示条圆角：密集界面用小圆角 */
}

/* ---------- 把 Gradio 主题变量对齐到上面的令牌 ---------- */
.gradio-container {
  /* 内容量撑不起两栏：并排必然一边空一边长。单栏 + 宽度上限控制行长。 */
  max-width: 880px !important;
  margin-inline: auto !important;   /* Gradio 默认靠左，这里居中 */
  font-variant-numeric: tabular-nums;
  /* 组件之间的节奏由 .ov-stack 显式控制，这里只把 Gradio 内部用到的两档收一下 */
  --spacing-lg: 12px;
  --spacing-xl: 16px;
  /* 标签：默认是强调色药丸，读起来像按钮。静态标签用中性小字。 */
  --block-label-text-color: var(--ov-text-muted);
  --block-label-background-fill: transparent;
  --block-label-border-width: 0px;
  --block-label-shadow: none;
  --block-title-text-color: var(--ov-text);
  /* 中性色的别名：指向 Gradio 自己的变量。深浅两套由 Gradio 负责翻转，
     我们只借用，这样深色模式下文字/边框不会停在浅色值上。
     放在容器上而不是 :root，确保指向的变量在这里一定已定义。 */
  --ov-text: var(--body-text-color);
  --ov-text-muted: var(--body-text-color-subdued);
  --ov-border: var(--border-color-primary);
  /* 结构相关（与明暗无关）*/
  --block-border-width: 1px;
  --block-radius: var(--ov-radius-control);
  --block-shadow: none;
  /* 强调色：按钮等「可点」的元素用它 */
  --color-accent: var(--ov-accent);
  --color-accent-soft: var(--ov-accent-soft);
  --link-text-color: var(--ov-accent);
  --slider-color: var(--ov-accent);
  --button-primary-background-fill: var(--ov-accent);
  --button-primary-background-fill-hover: var(--ov-accent-hover);
  --button-primary-text-color: var(--ov-on-accent);
  /* 次要按钮走「描边」而不是填充：一个视图里只留一个实心动作，
     文字色引用主题变量，深浅两套都由 Gradio 保证对比度。 */
  --button-secondary-background-fill: transparent;
  --button-secondary-text-color: var(--body-text-color);
  --button-secondary-border-color: var(--border-color-primary);
  /* 破坏性动作必须一眼可辨：用自持的红/绿对，深浅两套都够亮（8.0:1） */
  --button-cancel-background-fill: var(--ov-err-bg);
  --button-cancel-background-fill-hover: var(--ov-err-bd);
  --button-cancel-text-color: var(--ov-err-fg);
  --button-cancel-border-color: var(--ov-err-bd);
}

footer { display: none !important; }

/* 离线工具：只用系统字体，不引外部字体（断网时也不会闪一下回退） */
.gradio-container {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
               "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
}

/* ---------- 顶部：标题 + 一行环境事实 ----------
   不做「大标题 + 副标题 + 一排胶囊」：每天要用的工具，首屏该留给真正要操作的东西。
   标题与环境信息并排一行，窄屏自动折行；不用胶囊也就不必给每个事实画一个框。
*/
.ov-hero { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 14px; margin: 0; }
.ov-title {
  margin: 0; font-size: 16px; line-height: 1.4; font-weight: 600;
  color: var(--ov-text); text-wrap: balance;
}
.ov-meta { margin: 0; font-size: 12.5px; line-height: 1.5; color: var(--ov-text); }
.ov-meta-k { color: var(--ov-text-muted); margin-inline-end: 3px; }
.ov-meta-sep { color: var(--ov-text-muted); margin: 0 8px; }

/* ---------- 分组靠间距，不靠框线 ----------
   组内 10px，组与组之间 20px（组内的 2 倍），层级由间距本身读出来。
   两组依旧纵向排到底：并排会让短的那一组下方留出一大块空洞。
*/
.ov-stack { gap: 10px !important; }
.ov-main-row { flex-direction: column !important; gap: 20px !important; align-items: stretch; }
/* 改成纵向之后，Gradio 的 scale 会变成「纵向」伸展，把短的那一组拉高、
   在下方留出一大块空白。这里明确按内容高度排。 */
.ov-main-row > * { flex-grow: 0 !important; flex-basis: auto !important; }
.ov-sect { margin: 0 0 2px; font-size: 13px; font-weight: 600; color: var(--ov-text); }
.ov-hint { margin: 0; font-size: 13px; line-height: 1.5; color: var(--ov-text-muted); }

/* ---------- 挤掉 Gradio 自带的那层厚内边距 ----------
   每个输入外面默认是一个 padding 很大的盒子，是「松散」的主要来源。
*/
.gradio-container .block.padded { padding: 10px !important; }
.gradio-container .form { gap: var(--spacing-lg) !important; }

/* ---------- 标签：静态文字，不做成强调色小药丸 ----------
   强调色在这个界面里代表「可点」，标签用了它就会读成按钮。
   直接压类名而不是主题变量：变量在这一版 Gradio 上不一定生效。
*/
.gradio-container .label-wrap {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 !important;
  min-height: 0 !important;
  color: var(--ov-text-muted) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
}
.gradio-container .label-wrap > span,
.gradio-container .label-wrap .icon { color: var(--ov-text-muted) !important; }



/* ---------- 键值表：比 Dataframe 轻，且带行头语义 ---------- */
.ov-kv { width: 100%; border-collapse: collapse; font-size: 13px; line-height: 1.5; }
.ov-kv th, .ov-kv td {
  padding: 4px 8px; text-align: start; vertical-align: top;
  border-bottom: 1px solid var(--ov-border);
}
.ov-kv th { width: 34%; font-weight: 400; color: var(--ov-text-muted); }
.ov-kv tr:last-child th, .ov-kv tr:last-child td { border-bottom: none; }

/* ---------- 状态提示：颜色 + 图标 + 文字标签三重线索 ---------- */
.ov-alert {
  display: flex; gap: 8px; align-items: flex-start;
  margin: 0; padding: 8px 10px;
  border-radius: var(--ov-radius-control); border: 1px solid;
  font-size: 13px; line-height: 1.5;
}
.ov-alert:empty, .ov-alert-body:empty { display: none; }
.ov-alert-icon { flex: 0 0 auto; font-weight: 700; line-height: 1.45; }
.ov-alert-body { flex: 1 1 auto; min-width: 0; }
.ov-alert-label { font-weight: 600; margin-inline-end: 6px; }
.ov-alert-body p { margin: 0; overflow-wrap: break-word; }
.ov-ok { background: var(--ov-ok-bg); border-color: var(--ov-ok-bd); color: var(--ov-ok-fg); }
.ov-warn { background: var(--ov-warn-bg); border-color: var(--ov-warn-bd); color: var(--ov-warn-fg); }
.ov-error { background: var(--ov-err-bg); border-color: var(--ov-err-bd); color: var(--ov-err-fg); }
.ov-info { background: var(--ov-info-bg); border-color: var(--ov-info-bd); color: var(--ov-info-fg); }

/* ---------- 焦点环：键盘可见，鼠标不打扰 ---------- */
.gradio-container :focus-visible {
  outline: 2px solid var(--ov-focus);
  outline-offset: 2px;
}
@media (forced-colors: active) {
  .gradio-container :focus-visible { outline-color: Highlight; }
}

/* ---------- 动效：只动会发生的那几个属性，且尊重减少动效偏好 ---------- */
@media (prefers-reduced-motion: no-preference) {
  .gradio-container button {
    transition-property: background-color, border-color, color, box-shadow, transform;
    transition-duration: 150ms;
    transition-timing-function: cubic-bezier(0.2, 0, 0, 1);
  }
  .gradio-container button:active { transform: scale(0.96); }
  .ov-alert { animation: ov-alert-in 150ms ease-out; }
  @keyframes ov-alert-in {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: none; }
  }
}


"""
# 语义级别 → (文字标签, 图标)。标签与图标都写出来，颜色不是唯一线索。
_LEVELS = {
    "ok": ("成功", "✓"),
    "warn": ("注意", "!"),
    "error": ("失败", "✕"),
    "info": ("提示", "i"),
}


def _alert(text: str, level: str = "info") -> str:
    """把状态文本渲染成一条带语义色的提示。

    无障碍：颜色之外还有图标 + 「成功/注意/失败」文字；错误用 role=alert
    （紧急，会打断朗读），其余用 role=status（礼貌播报，不打断）。
    """
    if not text:
        return ""
    label, icon = _LEVELS.get(level, _LEVELS["info"])
    role = "alert" if level == "error" else "status"
    body = "".join(
        f"<p>{html.escape(line)}</p>"
        for line in str(text).splitlines() if line.strip()
    )
    return (
        f'<div class="ov-alert ov-{level}" role="{role}">'
        f'<span class="ov-alert-icon" aria-hidden="true">{icon}</span>'
        f'<div class="ov-alert-body">'
        f'<span class="ov-alert-label">{label}</span>{body}</div>'
        f"</div>"
    )


def _lang_dropdown(label="语种", value="Auto"):
    """语种选择。600+ 项必须可搜索，否则纯下拉没法用。"""
    import gradio as gr
    return gr.Dropdown(label=label, choices=_ALL_LANGUAGES, value=value,
                       allow_custom_value=False, interactive=True, filterable=True,
                       info="保持 Auto 自动识别，或用关键词搜索。")


def _gen_settings(prof):
    """生成参数控件（折叠区）。默认值来自本机适配档位。

    排成两列网格而不是一列竖排：8 个控件竖着排会把折叠区拉成一屏半，
    每次改一个参数都要滚半天。
    """
    import gradio as gr
    with gr.Accordion("生成参数（可选，不改也能用）", open=False):
        with gr.Row():
            sp = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="语速",
                           info="1.0 正常；>1 更快。设了固定时长则忽略。")
            du = gr.Number(value=None, label="固定时长（秒）", info="留空则按语速估算。")
        with gr.Row():
            ns = gr.Slider(4, 64, value=prof.num_step, step=1, label="推理步数",
                           info=f"本机自动档 {prof.num_step}。越低越快、越省显存。")
            gs = gr.Slider(0.0, 4.0, value=2.0, step=0.1, label="引导强度 CFG",
                           info="默认 2.0；越高越贴近提示，也越机械。")
        with gr.Row():
            cd = gr.Slider(3, 30, value=prof.audio_chunk_duration, step=1, label="长文本分段（秒）",
                           info=f"本机自动档 {prof.audio_chunk_duration:.0f}s。调小可显著降低峰值显存。")
        with gr.Row():
            dn = gr.Checkbox(label="降噪", value=True)
            pp = gr.Checkbox(label="预处理参考音频", value=True,
                             info="去静音、裁剪，并为参考文本补标点。")
            po = gr.Checkbox(label="输出后处理", value=True,
                             info="去长静音、音量归一化、边缘淡入淡出。")
    return ns, gs, dn, sp, du, pp, po, cd


def build_ui():
    import gradio as gr

    model, device = load_model()
    prof = active_profile()
    sr = model.sampling_rate or getattr(
        getattr(model, "feature_extractor", None), "sampling_rate", 24000)

    global _THEME
    _THEME = gr.themes.Soft(
        primary_hue=gr.themes.colors.indigo,   # 强调色相与状态色（绿/琥珀/红）拉开 100°+
        neutral_hue=gr.themes.colors.slate,
        # 这是个每天要盯着的操作型工具，按「数据密集型工具」定档：
        # 字号要够读（中文 14px 起步），但间距/圆角/内边距一律收紧，
        # 靠紧凑的一致节奏体现精致，而不是靠留白。
        radius_size=gr.themes.sizes.radius_md,
        spacing_size=gr.themes.sizes.spacing_md,
        text_size=gr.themes.sizes.text_md,
    )

    def _wrap(text, lang, ref_audio, ref_text, instruct, ns, gs, dn, sp, du, pp, po, cd, mode):
        audio, status, level = synthesize(text, lang, ref_audio, ref_text, instruct,
                                          ns, gs, dn, sp, du, pp, po, cd, mode)
        return audio, _alert(status, level)

    def _short_gpu(name: str) -> str:
        """压短显卡名。顶部这一行只要「一眼看个大概」，全名在运行环境里有。"""
        for junk in ("NVIDIA ", "GeForce ", "AMD ", "Radeon ", "Intel(R) ", "Intel "):
            name = name.replace(junk, "")
        return name if len(name) <= 22 else name[:21] + "…"

    def _meta_line() -> str:
        """顶部一行环境事实：交代「这台机器现在什么状态」，但不占首屏纵深。"""
        items = [
            ("设备", device),
            ("显卡", _short_gpu(prof.device_name)),
            ("显存", f"{prof.vram_gib:.1f}G" if prof.vram_gib else "不适用"),
            ("精度", prof.dtype),
            ("步数", str(prof.num_step)),
            ("采样率", f"{sr / 1000:.0f} kHz"),
            ("转写", asr.short()),
            ("权重", Path(config.resolve_model_path()).name),
        ]
        sep = '<span class="ov-meta-sep">·</span>'
        return sep.join(
            f'<span class="ov-meta-k">{html.escape(k)}</span>{html.escape(str(v))}'
            for k, v in items
        )

    with gr.Blocks(title="OmniVoice Studio") as demo:
        # 页面唯一的 h1；标题与环境事实并排一行，把首屏留给真正要操作的东西
        gr.HTML(
            '<div class="ov-hero">'
            '<h1 class="ov-title">OmniVoice Studio</h1>'
            f'<p class="ov-meta">{_meta_line()}</p>'
            "</div>"
        )
        if _LOAD_NOTE:
            gr.HTML(_alert(_LOAD_NOTE, "warn"))

        with gr.Tabs():
            # ---------------- 音色克隆 ----------------
            with gr.TabItem("音色克隆"):
                with gr.Row(elem_classes=["ov-main-row"]):
                    # 左：输入。顺序＝使用顺序，必填在前、可选在后
                    with gr.Column(scale=3, elem_classes=["ov-stack"]):
                        c_lang = _lang_dropdown()
                        c_text = gr.Textbox(label="要合成的文本", lines=4,
                                            placeholder="支持长文本，会自动分段合成…")
                        with gr.Accordion("音色来源（参考音频）", open=True):
                            c_voice = gr.Dropdown(
                                label="从音色库选择", choices=[""] + list_voices(), value="",
                                filterable=True,
                                info="选定后自动填入下面的参考音频与参考文本。")
                            # 用文件选择器而不是 gr.Audio：后者的拖放区自带 ~300px 高度，
                            # CSS 压不动（高度由组件内部撑起，min-height:0 无效），
                            # 而这里只需要一个「选个音频文件」的动作。试听在「音色库」里。
                            # 高度要放得下图标 + 三行提示文字，压太狠会把文字裁掉
                            c_ref = gr.File(label="参考音频（3–10 秒最佳）", height=126,
                                            file_types=["audio"], type="filepath")
                            c_ref_text = gr.Textbox(
                                label="参考音频文本", lines=2,
                                placeholder="留空则自动转写（SenseVoice，约 0.5 秒，自带标点）")
                            with gr.Row():
                                c_name = gr.Textbox(label="存为音色", scale=2,
                                                    placeholder="例如：我的声音")
                                c_save = gr.Button("保存到音色库", variant="secondary",
                                                   scale=1, interactive=False)
                            c_save_status = gr.HTML()
                        # 主操作紧跟必填项，不等折叠区展开就能点到
                        c_btn = gr.Button("生成语音", variant="primary", size="lg")
                        (c_ns, c_gs, c_dn, c_sp, c_du, c_pp, c_po, c_cd) = _gen_settings(prof)
                        with gr.Accordion("附加指令（可选）", open=False):
                            c_instruct = gr.Textbox(
                                label="Instruct", lines=2,
                                placeholder="例：男, 河南话　或　female, british accent",
                                info="可自由填写，多项用逗号分隔；可用的值就是语音设计的属性。"
                                     "方言与英文口音不能混用，同一类别只能写一项。")
                    # 右：结果。宽屏下粘住，左栏滚长表单时结果不会跑出视野
                    with gr.Column(scale=2, elem_classes=["ov-stack"]):
                        c_out = gr.Audio(label="合成结果", type="numpy",
                                         buttons=["download"])
                        gr.HTML('<p class="ov-hint">生成后可直接试听、下载；'
                                'wav 同时会存到 outputs/。</p>')
                        c_status = gr.HTML()

                def _on_voice_pick(name):
                    meta = load_voice_meta(name) if name else None
                    if not meta:
                        return gr.update(), gr.update()
                    return meta.get("ref_audio"), meta.get("ref_text", "")

                def _toggle_save(ref_audio):
                    # 没有参考音频时保存按钮不可用，省掉一次「点了才知道不行」
                    return gr.update(interactive=bool(ref_audio))

                c_voice.change(_on_voice_pick, [c_voice], [c_ref, c_ref_text])
                c_ref.change(_toggle_save, [c_ref], [c_save])

                def _on_save(ref_audio, ref_text, name):
                    if not ref_audio:
                        return gr.update(), _alert("还没有参考音频，先上传或从音色库选一个。", "warn")
                    if not name or not name.strip():
                        return gr.update(), _alert("请先填写音色名称。", "warn")
                    import shutil
                    stem = _unique_name(name)
                    src = Path(ref_audio)
                    dst = config.VOICES_DIR / f"{stem}{src.suffix or '.wav'}"
                    shutil.copy(src, dst)
                    (config.VOICES_DIR / f"{stem}.json").write_text(
                        json.dumps({"name": stem, "ref_audio": str(dst),
                                    "ref_text": ref_text or ""}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    # 模型已就绪时顺手把 prompt 也建好，之后每次合成都省掉转写与编码
                    note = warm_voice_prompt(stem)
                    return (gr.update(choices=[""] + list_voices(), value=stem),
                            _alert(f"已保存音色「{stem}」。{note}"
                                   "可到「音色库」标签页试听或删除。", "ok"))

                c_save.click(_on_save, [c_ref, c_ref_text, c_name], [c_voice, c_save_status])

                c_btn.click(
                    lambda *a: _wrap(*a, mode="clone"),
                    [c_text, c_lang, c_ref, c_ref_text, c_instruct,
                     c_ns, c_gs, c_dn, c_sp, c_du, c_pp, c_po, c_cd],
                    [c_out, c_status],
                    show_progress="full",
                )

            # ---------------- 语音设计 ----------------
            with gr.TabItem("语音设计"):
                with gr.Row(elem_classes=["ov-main-row"]):
                    with gr.Column(scale=3, elem_classes=["ov-stack"]):
                        d_lang = _lang_dropdown()
                        d_text = gr.Textbox(label="要合成的文本", lines=6)

                        def _preview(*groups):
                            """把「选了哪些属性」翻译成实际发出去的指令，避免这一步变黑盒。

                            实测模型规则（供排错参考）：每个类别最多一项；同一指令里
                            中文属性和英文口音可以共存；值必须落在模型内置清单内。
                            界面的下拉已经把选项限死，所以正常操作不会踩到这些错。
                            """
                            inst = _to_instruct([g for g in groups if g and g != "Auto"])
                            if not inst:
                                return ('<p class="ov-hint">未选择任何属性：'
                                        "模型会自行挑选一个音色。</p>")
                            return ('<p class="ov-hint">将发送的指令：'
                                    f"<code>{html.escape(inst)}</code></p>")

                        gr.HTML('<p class="ov-sect">说话人属性</p>'
                                '<p class="ov-hint">可自由组合，留 Auto 表示不指定。'
                                "英文口音只对英文生效，中文方言只对中文生效。</p>")
                        d_groups = []
                        _cats = list(_CATEGORIES.items())
                        # 两列网格：六个下拉竖排会把主操作顶出首屏
                        for _i in range(0, len(_cats), 3):
                            with gr.Row():
                                for _cat, _ch in _cats[_i:_i + 3]:
                                    d_groups.append(gr.Dropdown(
                                        label=_cat, choices=["Auto"] + _ch, value="Auto"))
                        d_preview = gr.HTML(value=_preview(*(["Auto"] * len(d_groups))))
                        d_btn = gr.Button("生成语音", variant="primary", size="lg")
                        (d_ns, d_gs, d_dn, d_sp, d_du, d_pp, d_po, d_cd) = _gen_settings(prof)
                    with gr.Column(scale=2, elem_classes=["ov-stack"]):
                        d_out = gr.Audio(label="合成结果", type="numpy",
                                         buttons=["download"])
                        gr.HTML('<p class="ov-hint">生成后可直接试听、下载；'
                                'wav 同时会存到 outputs/。</p>')
                        d_status = gr.HTML()

                def _design(text, lang, ns, gs, dn, sp, du, pp, po, cd, *groups):
                    instruct = _to_instruct(list(groups))
                    return _wrap(text, lang, None, None, instruct,
                                 ns, gs, dn, sp, du, pp, po, cd, "design")

                for _g in d_groups:
                    _g.change(_preview, d_groups, d_preview)

                d_btn.click(_design,
                            [d_text, d_lang, d_ns, d_gs, d_dn, d_sp, d_du, d_pp, d_po, d_cd] + d_groups,
                            [d_out, d_status],
                            show_progress="full")

            # ---------------- 音色库 ----------------
            with gr.TabItem("音色库"):
                gr.HTML('<p class="ov-hint">已保存的音色存放在 voices/，'
                        "在「音色克隆」里可直接选用。</p>")
                with gr.Row(elem_classes=["ov-main-row"]):
                    with gr.Column(scale=2, elem_classes=["ov-stack"]):
                        v_pick = gr.Dropdown(label="选择音色", choices=list_voices(),
                                             value=None, filterable=True)
                        v_preview = gr.Audio(label="参考音频试听", type="filepath",
                                             interactive=False)
                        v_text = gr.Textbox(label="参考文本", lines=2, interactive=False)
                        # 换引擎 / 转写结果不满意时，就地重听一遍。会连带重建音色缓存
                        # （文本被写进了 prompt，只改文字不改 prompt 是不生效的）。
                        v_retrans_btn = gr.Button("重新转写参考文本", variant="secondary",
                                                  size="sm", interactive=False)
                        with gr.Row():
                            v_rename = gr.Textbox(label="重命名为", scale=2,
                                                  placeholder="输入新名称，留空不生效")
                            v_rename_btn = gr.Button("重命名", variant="secondary",
                                                     scale=1, interactive=False)
                        v_status = gr.HTML()
                    with gr.Column(scale=3, elem_classes=["ov-stack"]):
                        v_table = gr.Dataframe(headers=["音色", "参考文本"],
                                               value=_voice_rows(), interactive=False)
                        v_bulk = gr.CheckboxGroup(label="批量选择（可多选）", choices=list_voices())
                        # 破坏性操作：红底按钮 + 必须先勾确认，且提示里点名删了哪些
                        v_confirm = gr.Checkbox(label="确认删除所选音色（含参考音频，不可恢复）")
                        with gr.Row():
                            v_refresh = gr.Button("刷新列表", variant="secondary")
                            v_del_sel = gr.Button("删除所选", variant="stop", interactive=False)

                # 所有「改动了音色库」的操作都返回同一套状态，避免各处不同步
                _LIB_OUT = [c_voice, v_pick, v_table, v_preview, v_text,
                            v_rename, v_rename_btn, v_retrans_btn, v_bulk, v_confirm,
                            v_del_sel, v_status]

                def _lib_state(status: str = "", level: str = "info",
                               pick: str | None = None, text: str = ""):
                    names = list_voices()
                    return (
                        gr.update(choices=[""] + names, value=""),   # 克隆页的下拉
                        gr.update(choices=names, value=pick),        # 本页的下拉
                        _voice_rows(),                               # 表格
                        gr.update(value=None),                       # 试听
                        gr.update(value=text),                       # 参考文本
                        gr.update(value=""),                         # 重命名输入
                        gr.update(interactive=bool(pick)),           # 重命名按钮
                        gr.update(interactive=bool(pick)),           # 重新转写按钮
                        gr.update(choices=names, value=[]),          # 批量选择
                        gr.update(value=False),                      # 确认勾选
                        gr.update(interactive=False),                # 删除按钮
                        _alert(status, level),
                    )

                def _pick(name):
                    meta = load_voice_meta(name) if name else None
                    if not meta:
                        return (gr.update(), gr.update(),
                                gr.update(interactive=False), gr.update(interactive=False))
                    return (meta.get("ref_audio"), meta.get("ref_text", ""),
                            gr.update(interactive=True), gr.update(interactive=True))

                def _sync_del(picked, confirmed):
                    # 没选任何音色、或没勾确认，都不让点删除
                    return gr.update(interactive=bool(picked) and bool(confirmed))

                def _rename(old, new):
                    if not old:
                        return _lib_state("先在上面的下拉里选一个音色。", "warn")
                    new = (new or "").strip()
                    if not new:
                        return _lib_state("请输入新的音色名称。", "warn", pick=old)
                    if new == old:
                        return _lib_state("新旧名称相同，未做修改。", "warn", pick=old)
                    meta = load_voice_meta(old) or {}
                    src = Path(meta["ref_audio"]) if meta.get("ref_audio") else None
                    stem = _unique_name(new)
                    try:
                        moved: Path | None = None
                        if src and src.exists():
                            moved = config.VOICES_DIR / f"{stem}{src.suffix or '.wav'}"
                            src.replace(moved)
                        (config.VOICES_DIR / f"{old}.json").unlink(missing_ok=True)
                        (config.VOICES_DIR / f"{stem}.json").write_text(
                            json.dumps({"name": stem,
                                        "ref_audio": str(moved) if moved else "",
                                        "ref_text": meta.get("ref_text", "")},
                                       ensure_ascii=False, indent=2),
                            encoding="utf-8")
                        # prompt 缓存的指纹只看 size/mtime，改名不影响有效性，跟着搬过去即可
                        old_pt = _voice_prompt_file(old)
                        if old_pt.exists():
                            old_pt.replace(_voice_prompt_file(stem))
                    except Exception as e:  # noqa: BLE001
                        return _lib_state(f"重命名失败：{e}", "error", pick=old)
                    extra = "" if stem == new else f"（「{new}」已存在，故改名为「{stem}」）"
                    return _lib_state(f"已把「{old}」重命名为「{stem}」{extra}", "ok", pick=stem)

                def _delete_selected(picked, confirmed):
                    picked = list(picked or [])
                    if not picked:
                        return _lib_state("先在下面勾选要删除的音色。", "warn")
                    if not confirmed:
                        return _lib_state("请先勾选「确认删除」再执行。", "warn")
                    removed, failed = [], []
                    for n in picked:
                        try:
                            meta = load_voice_meta(n) or {}
                            for target in (meta.get("ref_audio"),
                                           config.VOICES_DIR / f"{n}.json",
                                           _voice_prompt_file(n)):
                                if target:
                                    Path(target).unlink(missing_ok=True)
                            removed.append(n)
                        except Exception as e:  # noqa: BLE001
                            failed.append(f"{n}（{e}）")
                    if failed:
                        return _lib_state(
                            f"已删除 {len(removed)} 个；{len(failed)} 个失败：" + "；".join(failed),
                            "error")
                    return _lib_state(
                        f"已删除 {len(removed)} 个音色及其参考音频：{'、'.join(removed)}", "ok")

                def _retranscribe(name):
                    if not name:
                        return _lib_state("先在上面的下拉里选一个音色。", "warn")
                    msg, level = retranscribe_voice(name)
                    meta = load_voice_meta(name) or {}
                    return _lib_state(msg, level, pick=name,
                                      text=meta.get("ref_text", ""))

                v_pick.change(_pick, [v_pick], [v_preview, v_text, v_rename_btn, v_retrans_btn])
                v_bulk.change(_sync_del, [v_bulk, v_confirm], [v_del_sel])
                v_confirm.change(_sync_del, [v_bulk, v_confirm], [v_del_sel])
                v_refresh.click(lambda: _lib_state(), None, _LIB_OUT)
                v_rename_btn.click(_rename, [v_pick, v_rename], _LIB_OUT)
                v_retrans_btn.click(_retranscribe, [v_pick], _LIB_OUT)
                v_del_sel.click(_delete_selected, [v_bulk, v_confirm], _LIB_OUT)

        # 诊断信息放最后：属于「出问题时才看」的内容，不该占着首屏
        with gr.Accordion("运行环境与排错", open=False):
            gr.HTML('<p class="ov-hint">当前档位：'
                    f"<code>{html.escape(prof.short())}</code>。"
                    "长文本会自动分段串行合成，段数越多越慢；若提示显存不足，"
                    "可调低推理步数或「长文本分段」，或把 config.DEVICE 设为 \"cpu\"。</p>")
            _rows = "".join(
                f"<tr><th scope=\"row\">{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
                for k, v in list(prof.rows()) + [("参考音频转写", asr.describe(detail=True))]
            )
            gr.HTML(f'<table class="ov-kv">{_rows}</table>')
            if prof.notes:
                _notes = "".join(f"<p class=\"ov-hint\">· {html.escape(n)}</p>" for n in prof.notes)
                gr.HTML(_notes)

    return demo


if __name__ == "__main__":
    load_model()
    ui = build_ui()
    ui.queue().launch(
        server_name=config.HOST,
        server_port=config.PORT,
        show_error=True,
        theme=_THEME,
        css=_CSS,
        # 音色库里存的是 voices/ 下的文件，生成结果在 outputs/：
        # 都要放行，否则「从音色库选择」把路径回填给参考音频控件时会被拒绝
        allowed_paths=[str(config.VOICES_DIR), str(config.OUTPUT_DIR)],
    )
