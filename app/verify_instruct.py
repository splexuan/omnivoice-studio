# -*- coding: utf-8 -*-
"""核对「附加指令」（instruct）到底能填什么、不能填什么。

直接调用模型侧的 _resolve_instruct（OmniVoice.generate 里真正用的那个），
而不是读注释 —— 界面上的提示必须和模型的实际规则一致。

顺带核对 app._CATEGORIES 里的下拉项与模型清单是否一一对应：
少一项就等于「模型支持但界面选不到」，只能靠手写指令绕过去。

用法：
    runtime\\python.exe app\\verify_instruct.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "OmniVoice"))

from omnivoice.models.omnivoice import _resolve_instruct  # noqa: E402

CASES = [
    # (说明, 指令, 期望)
    ("中文合法：单项", "男", "ok"),
    ("中文合法：多项", "青年, 低音调, 河南话", "ok"),
    ("英文合法：comma+space", "male, low pitch, indian accent", "ok"),
    ("中英混排（中文属性 + 英文口音）", "男, indian accent", "ok"),
    ("口语化描述（随手写的话）", "用缓慢温柔的语气朗读", "err"),
    ("拼错的英文", "mle, indian acent", "err"),
    ("同类别冲突：两种口音", "american accent, british accent", "err"),
    ("同类别冲突：两种性别", "男, female", "err"),
    ("方言 + 口音混用", "河南话, american accent", "err"),
    ("下拉里没有但模型支持：俄罗斯口音", "russian accent", "ok"),
    ("下拉里没有但模型支持：葡萄牙口音", "portuguese accent", "ok"),
    ("下拉里没有但模型支持：石家庄话", "石家庄话", "ok"),
    ("下拉里没有但模型支持：宁夏话", "宁夏话", "ok"),
]

print(f"{'说明':<34} {'期望':<5} {'实际':<5} 结果")
print("-" * 96)
bad = 0
for label, inst, want in CASES:
    try:
        out = _resolve_instruct(inst)
        got, detail = "ok", f"→ {out!r}"
    except ValueError as e:
        got, detail = "err", str(e).splitlines()[0][:70]
    mark = "✓" if got == want else "✗ 不符"
    if got != want:
        bad += 1
    print(f"{label:<34} {want:<5} {got:<5} {mark}  {detail}")

print("-" * 96)
print(f"共 {len(CASES)} 例，全部符合预期。" if not bad else f"有 {bad} 例不符。")

# 顺带核对：项目下拉里的值是否都在模型清单内
from omnivoice.models.omnivoice import (_INSTRUCT_VALID_EN,  # noqa: E402
                                        _INSTRUCT_VALID_ZH)
sys.path.insert(0, str(ROOT / "app"))
import app as core  # noqa: E402

have_acc = set(core._ACCENT_TO_EN)
have_dia = set(core._DIALECT_VALUES)
model_acc = {x for x in _INSTRUCT_VALID_EN if x.endswith(" accent")}
model_dia = set(_INSTRUCT_VALID_ZH) - {"男", "女", "儿童", "少年", "青年", "中年", "老年",
                                       "极低音调", "低音调", "中音调", "高音调", "极高音调", "耳语"}
have_acc_en = {core._ACCENT_TO_EN[x].lower() for x in have_acc}   # 模型侧大小写不敏感

print()
print("模型支持但下拉缺失的口音：", sorted(model_acc - have_acc_en) or "无")
print("模型支持但下拉缺失的方言：", sorted(model_dia - have_dia) or "无")
print("下拉有但模型不认的口音：", sorted(have_acc_en - model_acc) or "无")
print("下拉有但模型不认的方言：", sorted(have_dia - model_dia) or "无")
