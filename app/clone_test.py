"""语音克隆功能验证：用一段参考音频克隆音色并合成新文本。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from omnivoice import OmniVoice  # noqa: E402

REF = config.OUTPUT_DIR / "smoke_test.wav"  # 用上一步生成的 5.4s 音频当参考
REF_TEXT = "你好，这是 OmniVoice 本地语音合成工具的测试。今天天气不错。"


def main() -> int:
    if not REF.exists():
        print("缺少参考音频:", REF)
        return 1

    print("loading model ...")
    model = OmniVoice.from_pretrained(
        config.resolve_model_path(),
        device_map="cuda",
        dtype=torch.float16,
        load_asr=False,
    )

    t0 = time.time()
    print("creating voice clone prompt ...")
    prompt = model.create_voice_clone_prompt(ref_audio=str(REF), ref_text=REF_TEXT)
    print(f"prompt ready in {time.time() - t0:.1f}s")

    t1 = time.time()
    audio = model.generate(
        text="这是一段用克隆音色合成的新语音，用来验证语音克隆功能是否正常工作。",
        voice_clone_prompt=prompt,
    )
    cost = time.time() - t1

    wav = (np.clip(audio[0], -1.0, 1.0) * 32767).astype(np.int16)
    dur = len(wav) / model.sampling_rate
    out = config.OUTPUT_DIR / "test_clone.wav"
    sf.write(str(out), wav, model.sampling_rate, subtype="PCM_16")

    free, total = torch.cuda.mem_get_info()
    print(f"generated in {cost:.1f}s, audio {dur:.1f}s, RTF {cost / max(dur, 1e-6):.2f}")
    print(f"vram used {(total - free) / 1024**3:.2f} / {total / 1024**3:.2f} GiB")
    print("saved:", out)
    print("CLONE TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
