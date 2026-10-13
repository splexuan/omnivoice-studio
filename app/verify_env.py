"""环境自检：确认 Python / torch / CUDA / omnivoice 是否就绪。

用法（在 omnivoice-studio 目录下）：
    .venv\\Scripts\\python.exe app\\verify_env.py
"""

import sys


def main() -> int:
    print("=" * 60)
    print("OmniVoice Studio — 环境自检")
    print("=" * 60)
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
        print(f"has OmniVoice: {hasattr(omnivoice, 'OmniVoice')}")
    except Exception as e:  # noqa: BLE001
        print(f"omnivoice   : ERROR -> {e}")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
