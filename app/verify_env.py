"""环境自检：确认 Python / torch / CUDA / omnivoice / 权重 是否就绪。

换机器后第一个该跑的命令。它会顺带输出「本机自动适配档位」，帮你确认
精度选择是否合理，并能识别「明明有 N 卡却用不上」这类驱动与 torch 不匹配的问题。

用法（项目根目录下）：
    runtime\\python.exe app\\verify_env.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    # Windows 控制台常是 GBK，遇到非 GBK 字符宁可变 ? 也不要崩
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

    print("=" * 64)
    print("OmniVoice Studio — 环境自检")
    print("=" * 64)
    print(f"python      : {sys.version.split()[0]}")
    print(f"executable  : {sys.executable}")

    try:
        import torch
        print(f"torch       : {torch.__version__}")
        print(f"cuda build  : {torch.version.cuda}")
        print(f"cuda avail  : {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            print(f"gpu         : {torch.cuda.get_device_name(0)}")
            free, total = torch.cuda.mem_get_info()
            print(f"vram        : {free / 1024**3:.2f} / {total / 1024**3:.2f} GiB free")
    except Exception as e:  # noqa: BLE001
        print(f"torch       : ERROR -> {e}")

    try:
        import omnivoice
        print(f"omnivoice   : {omnivoice.__file__}")
        print(f"version     : {getattr(omnivoice, '__version__', '?')}")
        print(f"has OmniVoice: {hasattr(omnivoice, 'OmniVoice')}")
    except Exception as e:  # noqa: BLE001
        print(f"omnivoice   : ERROR -> {e}")

    try:
        import config
        print(f"model path  : {config.resolve_model_path()}")
        print(f"weights ok  : {config.weights_ready()}")
        print(f"asr enabled : {config.ASR_ENABLED}")
        # 转写引擎是本项目最容易被环境忽略的一环：缺 sherpa-onnx 或缺 SenseVoice
        # 权重时不会报错，而是等到生成时才因为拿不到参考文本而失败。自检里要写明白。
        import asr as _asr
        print(f"transcribe  : {_asr.describe(detail=True)}")
        print(f"sherpa-onnx : {'已安装' if _asr.runtime_ready() else '未安装'}")
        print(f"sensevoice  : {config.resolve_sensevoice_dir() or '未找到（参考文本需手工填写）'}")
        prof = config.profile()
        print("-" * 64)
        print("本机自动适配档位：")
        print(prof.summary())
        print("-" * 64)
        for n in prof.notes:
            print(f"- {n}")
    except Exception as e:  # noqa: BLE001
        print(f"profile     : ERROR -> {e}")

    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
