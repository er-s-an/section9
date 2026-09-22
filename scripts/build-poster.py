#!/usr/bin/env python3
"""Build a print-ready A3 delivery poster from the live local API."""
from __future__ import annotations

import datetime as dt
import html
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "DELIVERY_POSTER.html"
BASE = "http://127.0.0.1:9019"
CONDITIONS = ["single", "muted", "swarm", "memory", "memory_jev"]
SCENARIOS = ["prompt", "cost", "loop", "composite"]
LABELS = {
    "single": "单 Agent",
    "muted": "蜂群禁言",
    "swarm": "蜂群协作",
    "memory": "蜂群 + Playbook",
    "memory_jev": "蜂群 + Playbook + Jev",
}


def get_json(path: str, base: str = BASE) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + path, timeout=6) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} returned non-object JSON")
    return value


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt_time(value: object) -> str:
    return f"{float(value):.1f}s" if isinstance(value, (int, float)) else "—"


def build_cells(scoreboard: dict) -> dict[tuple[str, str], dict]:
    cells = {(condition, scenario): [] for condition in CONDITIONS for scenario in SCENARIOS}
    for row in scoreboard.get("rows", []):
        condition = row.get("id")
        for cell in row.get("cells", []) if isinstance(row.get("cells"), list) else []:
            scenario = cell.get("scenario")
            if (condition, scenario) in cells:
                cells[(condition, scenario)] = {
                    "n": cell.get("n", 0),
                    "failures": cell.get("failures", 0),
                    "median_s": cell.get("median_s"),
                    "run_ids": cell.get("run_ids", []),
                }
    return {key: (value if isinstance(value, dict) and value else {"n": 0, "failures": 0, "median_s": None})
            for key, value in cells.items()}


def cell_html(cell: dict, condition: str) -> str:
    if condition == "memory_jev" and cell["n"] == 0:
        return '<div class="cell pending"><b>待测</b><small>Jev disabled</small></div>'
    if cell["n"] == 0:
        return '<div class="cell pending"><b>待测</b><small>未运行</small></div>'
    return (f'<div class="cell"><b>N{cell["n"]} / F{cell["failures"]}</b>'
            f'<small>中位耗时 {fmt_time(cell["median_s"])}</small></div>')


def build_html(scoreboard: dict, checked_at: str) -> str:
    rows = scoreboard.get("rows", [])
    row_map = {row.get("id"): row for row in rows if isinstance(row, dict)}
    cells = build_cells(scoreboard)
    matrix_rows = []
    for condition in CONDITIONS:
        matrix_rows.append(
            "<tr><th>" + esc(LABELS[condition]) + "</th>" +
            "".join(f"<td>{cell_html(cells[(condition, scenario)], condition)}</td>" for scenario in SCENARIOS) +
            "</tr>"
        )
    score_line = " · ".join(
        f"{LABELS.get(key, key)} N{row.get('n', 0)} / F{row.get('failures') if row.get('failures') is not None else '—'}"
        for key, row in row_map.items() if key in CONDITIONS
    )
    payload = json.dumps({"checked_at": checked_at, "scoreboard": scoreboard,
                          "matrix": {f"{c}/{s}": cells[(c, s)] for c in CONDITIONS for s in SCENARIOS}},
                         ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Section9 / Delivery Poster</title>
<style>
@page {{ size: A3 portrait; margin: 0; }}
:root {{ --orange:#ff6500; --ink:#111; --paper:#fffdf8; --muted:#665f58; --line:#181818; }}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; background:#ddd; color:var(--ink); font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif; }}
.poster {{ width:297mm; min-height:419mm; margin:0 auto; padding:12mm; background:var(--paper); position:relative; overflow:hidden; }}
.poster:before {{ content:""; position:absolute; inset:0; pointer-events:none; background:repeating-linear-gradient(0deg,transparent 0 6mm,rgba(0,0,0,.025) 6.1mm 6.2mm); }}
.content {{ position:relative; z-index:1; }}
.kicker {{ display:flex; justify-content:space-between; text-transform:uppercase; font:700 12px/1 monospace; letter-spacing:.12em; border-bottom:3px solid var(--ink); padding-bottom:7px; }}
h1 {{ font-size:49px; line-height:.95; max-width:220mm; margin:9mm 0 5mm; letter-spacing:-.06em; }}
.lede {{ font-size:19px; line-height:1.42; max-width:236mm; margin:0 0 10mm; }}
.orange {{ color:var(--orange); }}
.strip {{ background:var(--ink); color:#fff; padding:5mm; display:grid; grid-template-columns:1fr 1fr 1fr; gap:4mm; margin-bottom:6mm; }}
.strip b {{ display:block; color:var(--orange); font:700 12px monospace; text-transform:uppercase; margin-bottom:2mm; }}
.strip span {{ font-size:15px; line-height:1.3; }}
h2 {{ font-size:24px; margin:7mm 0 3mm; border-left:8px solid var(--orange); padding-left:4mm; }}
.flow {{ display:grid; grid-template-columns:repeat(4,1fr); gap:3mm; }}
.step {{ border:2px solid var(--line); padding:4mm; min-height:35mm; }}
.step b {{ font:700 12px monospace; color:var(--orange); }}
.step strong {{ display:block; font-size:17px; margin:2mm 0; }}
.step p {{ margin:0; font-size:12px; line-height:1.35; }}
table {{ width:100%; border-collapse:collapse; table-layout:fixed; border:2px solid var(--line); }}
th,td {{ border:1px solid #555; padding:3mm; vertical-align:top; }}
thead th {{ background:var(--orange); color:#fff; font-size:14px; text-align:left; }}
tbody th {{ width:33mm; background:#f0ebe4; font-size:13px; text-align:left; }}
.cell {{ min-height:14mm; }} .cell b {{ display:block; font:700 15px monospace; }} .cell small {{ display:block; color:var(--muted); margin-top:2mm; font-size:11px; }}
.pending {{ color:#777; background:#f8f6f2; }} .pending b {{ font-family:inherit; }}
.columns {{ display:grid; grid-template-columns:1.05fr .95fr; gap:7mm; }}
.box {{ border:2px solid var(--line); padding:4mm; min-height:43mm; }}
.box h3 {{ margin:0 0 3mm; font-size:16px; }} .box ul {{ margin:0; padding-left:5mm; font-size:12px; line-height:1.45; }}
.commands {{ background:#171717; color:#fff; padding:4mm; font:12px/1.55 monospace; white-space:pre-wrap; }}
.commands b {{ color:var(--orange); }}
.footer {{ display:flex; justify-content:space-between; align-items:end; border-top:3px solid var(--ink); margin-top:8mm; padding-top:4mm; font-size:11px; }}
.qr {{ width:28mm; height:28mm; object-fit:contain; border:1px solid #aaa; }}
.draft {{ font-weight:700; color:var(--orange); }}
@media print {{ html,body {{ background:#fff; }} .poster {{ margin:0; }} }}
</style></head><body><main class="poster"><div class="content">
<div class="kicker"><span>SECTION9 / VERIFIABLE INCIDENT LAB</span><span>LOCAL DELIVERY POSTER</span></div>
<h1>把 Agent 故障响应<br><span class="orange">变成可复核的证据链。</span></h1>
<p class="lede">Section9 是一个本地故障响应实验室：故障注入改变真实业务行为，角色协作提出修复，版本 fencing 约束执行，独立验收决定是否闭环。</p>
<section class="strip"><div><b>CONTROL</b><span>SQLite WAL · revision · lease · approval</span></div><div><b>OBSERVE</b><span>OTel → Collector → Langfuse v4</span></div><div><b>MODEL BOUNDARY</b><span>Remote EvoMap Luna / not offline</span></div></section>
<h2>真实流程 / four gates</h2><div class="flow">
<div class="step"><b>01 / INJECT</b><strong>故障真实生效</strong><p>Prompt、cost、loop、composite 由 API 注入，并保留 generation、revision 与 run。</p></div>
<div class="step"><b>02 / COLLABORATE</b><strong>角色各有边界</strong><p>Sentry、diagnoser、fixer、verifier 通过 scoped worker API 协作；禁言时同伴消息被阻断，仅保留各自观测。</p></div>
<div class="step"><b>03 / FENCE</b><strong>计划不能越权</strong><p>计划绑定 hash、revision、lease 和审批；reset 或 takeover 后旧 grant 必须拒绝。</p></div>
<div class="step"><b>04 / VERIFY</b><strong>独立验收闭环</strong><p>业务语义、未受影响事实、工具终止、预算与 revision 一起检查；通过才可学习。</p></div>
</div>
<h2>实测矩阵 / live scoreboard</h2>
<table><thead><tr><th>条件</th>{''.join(f'<th>{esc(x)}</th>' for x in SCENARIOS)}</tr></thead><tbody>{''.join(matrix_rows)}</tbody></table>
<p style="font:11px monospace;color:#665f58;margin:3mm 0">API 汇总：{esc(score_line)} · checked {esc(checked_at)} · 每格显示 N=样本数 / F=非 resolved 失败数 / 中位耗时。<br>统一预算 16,000 tokens；模型、工具、数据对齐。供应商缓存未受控，小样本仅供观察；不声称蜂群更快。</p>
<div class="columns"><div class="box"><h3>安全边界 / owned evidence</h3><ul><li>OS sandbox：worker 使用本地受限进程边界与 scoped API。</li><li>Version fencing：revision、generation、hash、lease 同时约束 mutation。</li><li>Independent acceptance：verifier 不能读取受限场景真值，独立探针决定通过。</li><li>Jev：disabled；Hub OAuth：pendingauth；memory assets：local_only。</li></ul></div><div class="box"><h3>启动 / reset / acceptance</h3><div class="commands"><b>START</b> ./scripts/start.sh\n<b>RESET</b> ./scripts/reset.sh\n<b>ACCEPT</b> ./scripts/acceptance.sh\n<b>OPERATOR</b> http://127.0.0.1:9019\n<b>WORKER</b> http://127.0.0.1:9021\n<b>EVAL</b> http://127.0.0.1:9024 / worker 9026</div></div></div>
<div class="footer"><div><span class="draft">展示草稿，成员信息由用户补充</span><br>源仓库 · 本轮代码仅本机未 push。真实矩阵来自本机 API，未知状态保留为待测。</div><img class="qr" src="../artifacts/delivery/repo-qr.svg" alt="repository QR placeholder"></div>
</div></main><script type="application/json" id="poster-data">{payload}</script></body></html>'''


def main() -> None:
    checked_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    scoreboard = get_json("/api/scoreboard")
    OUT.write_text(build_html(scoreboard, checked_at), encoding="utf-8")
    print(f"wrote {OUT} checked_at={checked_at}")


if __name__ == "__main__":
    main()
