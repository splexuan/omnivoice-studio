"""端到端冒烟测试：按本机自动档位加载权重 -> 生成一段语音 -> 保存 wav。

默认不加载 ASR（省约 1.6 GB），只验证主模型能否在本机跑通。
换新机器时优先跑这个：它会把「设备 / 精度 / 步数」和失败原因都打出来。
"""

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from omnivoice import OmniVoice, OmniVoiceGenerationConfig  # noqa: E402


def vram(tag: str = "") -> None:
    """打印显存占用；非 CUDA 设备自动跳过。"""
    try:
        free, total = torch.cuda.mem_get_info()
        print(f"  [VRAM {tag}] used {(total - free) / 1024**3:.2f} / {total / 1024**3:.2f} GiB")
    except Exception:  # noqa: BLE001
        print(f"  [VRAM {tag}] 不适用（非 CUDA 设备）")
    sys.stdout.flush()


def main() -> int:
    prof = config.profile()
    mp = config.resolve_model_path()

    print("=" * 60)
    print("OmniVoice Studio — 冒烟测试")
    print("=" * 60)
    print(f"档位      : {prof.short()}")
    print(f"device    : {prof.device}    dtype: {prof.dtype}    steps: {prof.num_step}")
    print(f"model path: {mp}")
    print(f"exists    : {Path(mp).exists()}")
    for n in prof.notes:
        print(f"  - {n}")
    sys.stdout.flush()

    t0 = time.time()
    print(f"loading model (load_asr=False, {prof.dtype}, {prof.device}) ...")
    sys.stdout.flush()
    try:
        model = OmniVoice.from_pretrained(
            mp,
            device_map=prof.device,
            dtype=prof.torch_dtype,
            load_asr=False,
        )
    except Exception:  # noqa: BLE001
        print("模型加载失败：")
        traceback.print_exc()
        print("SMOKE TEST FAILED (load)")
        return 1
    print(f"loaded in {time.time() - t0:.1f}s")
    sr = model.sampling_rate or 24000
    print(f"sampling_rate: {sr}")
    vram("after load")

    text = "你好，这是 OmniVoice 本地语音合成工具的测试。今天天气不错。"
    t1 = time.time()
    print(f"generating (steps={prof.num_step}) ...")
    sys.stdout.flush()
    try:
        audio = model.generate(
            text=text,
            generation_config=OmniVoiceGenerationConfig(num_step=prof.num_step),
        )
    except Exception:  # noqa: BLE001
        print("生成失败：")
        traceback.print_exc()
        print("SMOKE TEST FAILED (generate)")
        return 1
    cost = time.time() - t1

    wav = (np.clip(audio[0], -1.0, 1.0) * 32767).astype(np.int16)
    dur = len(wav) / sr
    out = config.OUTPUT_DIR / "smoke_test.wav"
    sf.write(str(out), wav, sr, subtype="PCM_16")

    print(f"generated in {cost:.1f}s, audio {dur:.1f}s, RTF {cost / max(dur, 1e-6):.2f}")
    print(f"saved: {out}")
    vram("after gen")
    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
