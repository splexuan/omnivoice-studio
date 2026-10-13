"""端到端冒烟测试：加载本地权重 -> 生成一段语音 -> 保存 wav。

先不加载 ASR（省 ~1.6GB 显存），验证主模型在 4GB 显存上是否可行。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from omnivoice import OmniVoice  # noqa: E402


def vram(tag=""):
    free, total = torch.cuda.mem_get_info()
    print(f"  [VRAM {tag}] used {(total - free) / 1024**3:.2f} / {total / 1024**3:.2f} GiB")
    sys.stdout.flush()


def main() -> int:
    mp = config.resolve_model_path()
    print(f"model path : {mp}")
    print(f"exists     : {Path(mp).exists()}")
    sys.stdout.flush()

    t0 = time.time()
    print("loading model (load_asr=False, fp16, cuda) ...")
    sys.stdout.flush()
    model = OmniVoice.from_pretrained(
        mp,
        device_map="cuda",
        dtype=torch.float16,
        load_asr=False,
    )
    print(f"loaded in {time.time() - t0:.1f}s")
    print(f"sampling_rate: {model.sampling_rate}")
    vram("after load")

    text = "你好，这是 OmniVoice 本地语音合成工具的测试。今天天气不错。"
    t1 = time.time()
    print("generating ...")
    sys.stdout.flush()
    audio = model.generate(text=text)
    cost = time.time() - t1

    wav = (np.clip(audio[0], -1.0, 1.0) * 32767).astype(np.int16)
    dur = len(wav) / model.sampling_rate
    out = config.OUTPUT_DIR / "smoke_test.wav"
    sf.write(str(out), wav, model.sampling_rate, subtype="PCM_16")

    print(f"generated in {cost:.1f}s, audio {dur:.1f}s, RTF {cost / max(dur, 1e-6):.2f}")
    print(f"saved: {out}")
    vram("after gen")
    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
