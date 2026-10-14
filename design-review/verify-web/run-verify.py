"""一键跑本目录下的 playwright 验证脚本。

为什么不直接 `node xxx.js`：
  · 服务是懒加载的（加载模型前不占显存），但每次验证都得先把它起来；
  · 后台 `nohup` 起的进程不跨命令存活，容易留下孤儿或干脆没起来。
  所以把「起服务 → 等 /api/bootstrap 就绪 → 跑脚本 → 关服务」串在一次调用里。

另外：本机没有 playwright 自带的 chromium（也没下 headless shell），
脚本里统一用 `chromium.launch({ headless: true, channel: 'chrome' })` 走系统 Chrome。

用法:
    runtime\\python.exe design-review\\verify-web\\run-verify.py <脚本名> [端口]
例:
    runtime\\python.exe design-review\\verify-web\\run-verify.py verify-history-layout.js
    runtime\\python.exe design-review\\verify-web\\run-verify.py verify-instruct-select.js
"""
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # design-review/verify-web → 项目根

PY = os.path.join(ROOT, "runtime", "python.exe")
ENTRY = os.path.join(ROOT, "web", "server.py")

NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
NODE_MODULES = r"C:\Users\Administrator\.workbuddy\binaries\node\workspace\node_modules"

script = sys.argv[1] if len(sys.argv) > 1 else "verify-history-layout.js"
port = sys.argv[2] if len(sys.argv) > 2 else "7866"
SCRIPT = os.path.join(HERE, script)

if not os.path.exists(SCRIPT):
    print(f"[错误] 找不到脚本：{SCRIPT}")
    print("       可选：", ", ".join(sorted(
        f for f in os.listdir(HERE) if f.endswith(".js"))))
    sys.exit(2)

env = dict(os.environ)
env["PYTHONPATH"] = ""                 # 挡掉宿主注入的 PYTHONPATH
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

srv = subprocess.Popen([PY, "-s", ENTRY], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连，绕开代理
ready = False
for _ in range(90):
    time.sleep(1)
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/bootstrap", timeout=2) as r:
            ready = r.status == 200
        if ready:
            break
    except Exception:  # noqa: BLE001
        pass
print("服务就绪:", ready, flush=True)

rc = 1
if ready:
    nenv = dict(os.environ)
    nenv["NODE_PATH"] = NODE_MODULES
    p = subprocess.run([NODE, SCRIPT, HERE, port], cwd=ROOT, env=nenv,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(p.stdout)
    if (p.stderr or "").strip():
        print("[node stderr]", (p.stderr or "")[-1200:])
    rc = p.returncode
    print("node 退出码:", rc, flush=True)

srv.terminate()
try:
    srv.wait(timeout=10)
except Exception:  # noqa: BLE001
    srv.kill()
print("服务已停止", flush=True)
sys.exit(rc)
