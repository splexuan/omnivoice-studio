# OmniVoice Studio

自用的**本地语音合成工具**，基于官方 [`k2-fsa/OmniVoice`](https://github.com/k2-fsa/OmniVoice) 最新源码（0.2.1）构建。

支持 **语音克隆 / 语音设计 / 600+ 语种 / 长文本合成 / 输出音量稳定**，全程离线运行。

> 从零搭建、常见坑、打成"解压即用"整合包的完整步骤，见 **[操作教程.md](操作教程.md)**。

---

## 快速开始

前置：已按 [操作教程.md](操作教程.md) 备好 Python 环境与模型权重。

```bash
run.bat                          # 双击，或
.venv\Scripts\python.exe app\app.py
```

浏览器打开 <http://127.0.0.1:7865>。

环境自检：

```bash
.venv\Scripts\python.exe app\verify_env.py
```

---

## 目录结构

```
omnivoice-studio/
├── app/
│   ├── app.py          WebUI 主程序（克隆 / 设计 / 音色库）
│   ├── config.py       路径与运行参数配置（随包相对路径）
│   ├── smoke_test.py   端到端冒烟测试（加载权重→生成 wav）
│   ├── clone_test.py   语音克隆验证
│   └── verify_env.py   环境自检
├── packaging/          打成"解压即用"整合包的脚本与说明
├── wheels/
│   └── _link_torch.py  复用已有 cu128 torch 的链接脚本（省下载）
├── OmniVoice/          官方源码（需自行 clone，见教程）
├── voices/             音色库（个人数据，不入库）
├── outputs/            合成结果（不入库）
├── run.bat             一键启动
├── 操作教程.md          完整操作教程
└── README.md
```

---

## 模型权重

`app/config.py` 按以下顺序查找权重目录，命中即用：

1. 本目录下的 `checkpoints/`（整合包布局）
2. 环境变量 `OMNIVOICE_CHECKPOINTS`
3. 开发机上的原整合包路径
4. 都找不到 → 回退 HuggingFace 仓库 id 联网下载

| 用途 | 仓库 id |
|------|---------|
| 主模型 | `k2-fsa/OmniVoice` |
| Whisper ASR | `openai/whisper-large-v3-turbo` |

---

## 与原整合包的关系

| 维度 | 原整合包 | 本工具 |
|------|----------|--------|
| 代码版本 | OmniVoice 0.1.2（编译为 `.pyd`，不可改） | **官方 0.2.1 源码**，可自由修改 |
| 长文本 / 音量优化 | 有 | 官方已内置，且更完善（还多了文本归一化） |
| 运行环境 | 便携 Python + torch **cu130** | 独立 venv + torch **cu128**（匹配本机驱动，GPU 可用） |
| 依赖关系 | 独立 | 复用其依赖，但**代码互不影响** |

> 原整合包里的 torch 是 `2.11.0+cu130`（CUDA 13.0），而本机驱动只到 CUDA 12.7，
> 所以它的 GPU 实际用不起来、一直跑 CPU。本工具换成 `cu128` 后才能真正调用显卡。

---

## 常见问题（详见操作教程）

- **GPU 用不了**：torch 的 CUDA 版本必须 ≤ 驱动支持的版本（`nvidia-smi` 右上角那个数字）。
- **显存不足（4GB 偏小）**：调低 *Inference Steps*、缩短文本、用 3–5 秒短参考音频，或把 `config.DEVICE` 改成 `"cpu"`。
- **首次生成较慢**：需加载 2.3GB 主模型（+1.6GB Whisper），之后常驻。
- **长文本**：自动分段串行合成 + 交叉淡入淡出，速度慢属正常。

---

## 许可与致谢

- 模型与代码：**k2-fsa/OmniVoice**（Xiaomi Corp.，Apache-2.0）
- ASR：**openai/whisper-large-v3-turbo**（MIT）
- 本仓库仅做运行时封装与界面集成。
