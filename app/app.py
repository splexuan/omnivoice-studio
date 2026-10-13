"""OmniVoice Studio — 本地自用语音合成工具（WebUI 主程序）。

特性：
  - 语音克隆 / 语音设计 / 自动语音
  - 长文本自动分段（由 OmniVoice 内置 chunk 逻辑处理）
  - 输出音量稳定 / 去长静音（postprocess_output）
  - 音色库：保存常用参考音色，一键复用
  - 结果自动保存到 outputs/ 并可直接下载
  - 自动选择 GPU/CPU，4GB 显存用 float16

用法：
    run.bat
    或  .venv\\Scripts\\python.exe app\\app.py
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

# 4GB 显存下缓解碎片化，降低 OOM 风险（必须在 import torch 之前设置）
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# 允许直接 python app/app.py 运行（把 app 目录加入 path 以便 import config）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import soundfile as sf
import torch

import config
from omnivoice import OmniVoice, OmniVoiceGenerationConfig
from omnivoice.utils.common import get_best_device

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
    "英文口音 Accent (英文)": [
        "美式 American", "英式 British", "澳式 Australian", "加拿大 Canadian",
        "印度 Indian", "韩国 Korean", "日本 Japanese", "中国 Chinese",
    ],
    "中文方言 Dialect (中文)": [
        "四川话", "陕西话", "河南话", "东北话", "贵州话", "云南话",
        "桂林话", "济南话", "甘肃话", "青岛话",
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
}
_GENDER_AGE_PITCH_STYLE = _CATEGORIES["性别 Gender"] + _CATEGORIES["年龄 Age"] + _CATEGORIES["音调 Pitch"] + _CATEGORIES["风格 Style"]


# ---------------------------------------------------------------------------
# 模型加载
# ---------------------------------------------------------------------------
_MODEL: OmniVoice | None = None
_DEVICE: str = "cpu"
_THEME = None
_CSS = ""


def load_model() -> tuple[OmniVoice, str]:
    global _MODEL, _DEVICE
    if _MODEL is not None:
        return _MODEL, _DEVICE

    device = get_best_device() if config.DEVICE == "auto" else config.DEVICE
    dtype = torch.float16 if config.DTYPE == "float16" else torch.float32
    if device == "cpu":
        dtype = torch.float32  # CPU 上 fp16 反而慢

    model_path = config.resolve_model_path()
    asr_path = config.resolve_asr_path()
    logger.info("加载模型: %s (device=%s, dtype=%s)", model_path, device, dtype)

    model = OmniVoice.from_pretrained(
        model_path,
        device_map=device,
        dtype=dtype,
        load_asr=config.LOAD_ASR,
        asr_model_name=asr_path,
        asr_device=config.ASR_DEVICE,
    )
    _MODEL, _DEVICE = model, device
    logger.info("模型加载完成。")
    return model, device


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


def _unique_name(base: str) -> str:
    base = base.strip() or "voice"
    name, i = base, 1
    while (config.VOICES_DIR / f"{name}.json").exists():
        i += 1
        name = f"{base}-{i}"
    return name


# ---------------------------------------------------------------------------
# 生成核心
# ---------------------------------------------------------------------------
def _save_wav(waveform_int16: np.ndarray, sr: int, tag: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = config.OUTPUT_DIR / f"{tag}-{ts}.wav"
    sf.write(str(out), waveform_int16, sr, subtype="PCM_16")
    return str(out)


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
    mode: str,
):
    """统一生成入口，返回 (采样率, int16 波形) 与状态文本。"""
    if not text or not text.strip():
        return None, "请先输入要合成的文本。"

    model, _ = load_model()

    gen_config = OmniVoiceGenerationConfig(
        num_step=int(num_step or 32),
        guidance_scale=float(guidance_scale) if guidance_scale is not None else 2.0,
        denoise=bool(denoise),
        preprocess_prompt=bool(preprocess_prompt),
        postprocess_output=bool(postprocess_output),
    )

    lang = language if (language and language != "Auto") else None
    kw: dict = {"text": text.strip(), "language": lang, "generation_config": gen_config}

    if speed is not None and float(speed) != 1.0:
        kw["speed"] = float(speed)
    if duration is not None and float(duration) > 0:
        kw["duration"] = float(duration)

    if mode == "clone":
        if not ref_audio:
            return None, "音色克隆需要上传参考音频，或在音色库中选择一个音色。"
        kw["voice_clone_prompt"] = model.create_voice_clone_prompt(
            ref_audio=ref_audio,
            ref_text=(ref_text or None),
        )

    if instruct and instruct.strip():
        kw["instruct"] = instruct.strip()

    t0 = time.time()
    try:
        audio = model.generate(**kw)
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        return None, "显存不足（GPU 4GB 偏小）。请缩短文本、减少 Inference Steps，或改用 CPU。"
    except Exception as e:  # noqa: BLE001
        return None, f"生成失败：{type(e).__name__}: {e}"

    cost = time.time() - t0
    sr = model.sampling_rate
    wav_i16 = (np.clip(audio[0], -1.0, 1.0) * 32767).astype(np.int16)

    try:
        path = _save_wav(wav_i16, sr, mode)
        saved = f"，已保存：{Path(path).name}"
    except Exception as e:  # noqa: BLE001
        saved = f"（保存失败：{e}）"

    dur = len(wav_i16) / sr
    return (sr, wav_i16), f"完成：音频 {dur:.1f}s，耗时 {cost:.1f}s（RTF {cost / max(dur, 1e-6):.2f}）{saved}"


# ---------------------------------------------------------------------------
# WebUI
# ---------------------------------------------------------------------------
def _lang_dropdown(label="语种 Language（可选）", value="Auto"):
    import gradio as gr
    return gr.Dropdown(label=label, choices=_ALL_LANGUAGES, value=value,
                       allow_custom_value=False, interactive=True,
                       info="保持 Auto 自动识别。")


def _gen_settings():
    import gradio as gr
    with gr.Accordion("生成参数（可选）", open=False):
        sp = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="语速 Speed", info="1.0 正常；>1 更快。设了时长则忽略。")
        du = gr.Number(value=None, label="固定时长 Duration（秒）", info="留空则用语速。")
        ns = gr.Slider(4, 64, value=32, step=1, label="推理步数 Steps", info="默认 32。越低越快。")
        dn = gr.Checkbox(label="降噪 Denoise", value=True)
        gs = gr.Slider(0.0, 4.0, value=2.0, step=0.1, label="引导强度 CFG", info="默认 2.0。")
        pp = gr.Checkbox(label="预处理参考音频 Preprocess", value=True,
                         info="参考音频去静音/裁剪，并为参考文本补标点。")
        po = gr.Checkbox(label="输出后处理 Postprocess", value=True,
                         info="去除长静音、音量归一化、边缘淡入淡出——即'音量稳定'。")
    return ns, gs, dn, sp, du, pp, po


def build_ui():
    import gradio as gr

    model, device = load_model()
    sr = model.sampling_rate

    global _THEME, _CSS
    _CSS = """
    .gradio-container {max-width: 1200px !important;}
    footer {display: none !important;}
    """
    _THEME = gr.themes.Soft()

    def _wrap(text, lang, ref_audio, ref_text, instruct, ns, gs, dn, sp, du, pp, po, mode):
        return synthesize(text, lang, ref_audio, ref_text, instruct,
                          ns, gs, dn, sp, du, pp, po, mode)

    with gr.Blocks(title="OmniVoice Studio") as demo:
        gr.Markdown(
            f"# OmniVoice Studio\n"
            f"本地语音合成 · 设备 **{device}** · 采样率 **{sr} Hz** · "
            f"权重 `{Path(config.resolve_model_path()).name}`"
        )

        with gr.Tabs():
            # ---------------- 音色克隆 ----------------
            with gr.TabItem("音色克隆"):
                with gr.Row():
                    with gr.Column():
                        c_text = gr.Textbox(label="要合成的文本", lines=5,
                                            placeholder="支持长文本，会自动分段合成…")
                        c_voice = gr.Dropdown(label="从音色库选择（可选）", choices=[""] + list_voices(), value="",
                                              info="选择后自动填入下方参考音频与文本。")
                        c_ref = gr.Audio(label="参考音频（3–10 秒最佳）", type="filepath")
                        c_ref_text = gr.Textbox(label="参考音频文本（留空则用 Whisper 自动转写）", lines=2)
                        c_name = gr.Textbox(label="保存为音色（可选）", placeholder="例如：我的声音")
                        c_save = gr.Button("保存到音色库")
                        c_lang = _lang_dropdown()
                        with gr.Accordion("附加指令 Instruct（可选）", open=False):
                            c_instruct = gr.Textbox(label="Instruct", lines=2)
                        (c_ns, c_gs, c_dn, c_sp, c_du, c_pp, c_po) = _gen_settings()
                        c_btn = gr.Button("生成", variant="primary")
                    with gr.Column():
                        c_out = gr.Audio(label="合成结果", type="numpy")
                        c_status = gr.Textbox(label="状态", lines=2)

                def _on_voice_pick(name):
                    if not name:
                        return gr.update(), gr.update()
                    meta = load_voice_meta(name) or {}
                    return meta.get("ref_audio"), meta.get("ref_text", "")

                c_voice.change(_on_voice_pick, [c_voice], [c_ref, c_ref_text])

                def _on_save(ref_audio, ref_text, name):
                    if not ref_audio:
                        return gr.update(), "没有可保存的参考音频。"
                    if not name or not name.strip():
                        return gr.update(), "请填写音色名称。"
                    import shutil
                    stem = _unique_name(name)
                    src = Path(ref_audio)
                    dst = config.VOICES_DIR / f"{stem}{src.suffix or '.wav'}"
                    shutil.copy(src, dst)
                    (config.VOICES_DIR / f"{stem}.json").write_text(
                        json.dumps({"name": stem, "ref_audio": str(dst),
                                    "ref_text": ref_text or ""}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    return gr.update(choices=[""] + list_voices(), value=stem), f"已保存音色：{stem}"

                c_save.click(_on_save, [c_ref, c_ref_text, c_name], [c_voice, c_status])

                c_btn.click(
                    lambda *a: _wrap(*a, mode="clone"),
                    [c_text, c_lang, c_ref, c_ref_text, c_instruct, c_ns, c_gs, c_dn, c_sp, c_du, c_pp, c_po],
                    [c_out, c_status],
                )

            # ---------------- 语音设计 ----------------
            with gr.TabItem("语音设计"):
                with gr.Row():
                    with gr.Column():
                        d_text = gr.Textbox(label="要合成的文本", lines=5)
                        d_lang = _lang_dropdown()
                        gr.Markdown("选择说话人属性（可组合）：")
                        d_groups = []
                        for cat, choices in _CATEGORIES.items():
                            d_groups.append(gr.Dropdown(label=cat, choices=["Auto"] + choices,
                                                        value="Auto", info="仅对对应语言生效"))
                        (d_ns, d_gs, d_dn, d_sp, d_du, d_pp, d_po) = _gen_settings()
                        d_btn = gr.Button("生成", variant="primary")
                    with gr.Column():
                        d_out = gr.Audio(label="合成结果", type="numpy")
                        d_status = gr.Textbox(label="状态", lines=2)

                def _design(text, lang, ns, gs, dn, sp, du, pp, po, *groups):
                    instruct = _to_instruct(list(groups))
                    return _wrap(text, lang, None, None, instruct, ns, gs, dn, sp, du, pp, po, "design")

                d_btn.click(_design,
                            [d_text, d_lang, d_ns, d_gs, d_dn, d_sp, d_du, d_pp, d_po] + d_groups,
                            [d_out, d_status])

            # ---------------- 音色库 ----------------
            with gr.TabItem("音色库"):
                gr.Markdown("已保存的音色（存放在 `voices/`）。在「音色克隆」标签页下拉即可选用。")
                v_table = gr.Dataframe(headers=["音色", "参考文本"],
                                       value=[[n, (load_voice_meta(n) or {}).get("ref_text", "")] for n in list_voices()],
                                       interactive=False)
                v_refresh = gr.Button("刷新列表")

                def _refresh():
                    return [[n, (load_voice_meta(n) or {}).get("ref_text", "")] for n in list_voices()]

                v_refresh.click(_refresh, None, v_table)

        gr.Markdown(
            "> 提示：GTX 1650 4GB 显存下，长文本会自动分段串行合成，速度较慢属正常。"
            "若提示显存不足，可调低推理步数或缩短单次文本。"
        )

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
    )
