"""OmniVoice Studio — 生成核心（供 web/server.py 导入，本文件不含界面）。

界面只有一套：web/static/index.html（FastAPI 版）。Gradio 版已于 2026-10-14
移除，所以这里不再有 build_ui() / _CSS，也不再延迟 import gradio。

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
    由 web/server.py 导入（双击 run_web.bat 启动）；
    命令行调试：runtime\\python.exe app\\smoke_test.py 等
"""

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
    meta = json.loads(p.read_text(encoding="utf-8"))

    # 参考音频的路径容错。音色 json 里存的可能是绝对路径，而项目一旦被整体
    # 搬走（或换台机器），那条路径就失效了 —— 表现是「音色都打不开」。
    # 音色的音频本来就和 json 放在同一个目录，所以指不到就回退到同名 wav，
    # 让音色库对「搬家」免疫。这里只读不写回，避免读接口产生副作用。
    ref = meta.get("ref_audio")
    if ref and not Path(ref).exists():
        fallback = config.VOICES_DIR / f"{name}.wav"
        if fallback.exists():
            meta["ref_audio"] = str(fallback)
    return meta


def _voice_rows() -> list[list[str]]:
    """音色库表格的数据行。"""
    return [[n, (load_voice_meta(n) or {}).get("ref_text", "")] for n in list_voices()]


# 音色名**同时就是文件名**（voices/<name>.json / .wav / .pt），所以它必须是个安全的
# 文件主干。这一点在「新建音色默认用上传文件名」之后变得更要紧 —— 音色名开始直接
# 来自用户的文件名，而那可以包含任意字符，包括 `../`。
_NAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NAME_MAX = 60


def sanitize_voice_name(base: str) -> str:
    """把用户给的名字（或上传文件名）收敛成安全的文件主干。

    规则：路径分隔符与 Windows 非法字符 → 下划线；连续空白并成一个；去掉首尾的
    点与空格（Windows 会静默吃掉结尾的点/空格，留着会让「显示名 ≠ 实际文件名」）；
    超长截断。结果为空则兜底 "voice"。**中文照常保留** —— 音色名本来就多是中文。
    """
    name = _NAME_BAD.sub("_", (base or "").strip())
    name = re.sub(r"\s+", " ", name).strip(". ")
    if len(name) > _NAME_MAX:
        name = name[:_NAME_MAX].strip(". ")
    return name or "voice"


def _unique_name(base: str) -> str:
    base = sanitize_voice_name(base)
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
