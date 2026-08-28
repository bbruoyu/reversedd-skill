"""反向背调报告 → 自包含 HTML 渲染器（仅展示层，核心 IP 仍在评分引擎）。

输入：
  - markdown: /synthesize 产出的报告 markdown（确定性模板 or LLM 输出，均遵循统一模板结构）
  - meta:     结构化头部信息（company/position/city/level/stage/subject/credit_code 等）
  - financials: 可选，/financials 返回的结构化财报摘要，渲染为「财务快照」卡

输出：单文件 HTML（内联 CSS，可直接保存/打开/打印）。
设计原则：解析模板结构（## 章节 / 表格 / 列表），不依赖 LLM 吐完美排版。
"""
import html
import json
import math
import re
from typing import Dict, List, Optional

LAMP_CLASS = {"🔴": "r", "🟡": "a", "🟢": "g"}
LAMP_VERDICT = {"r": "red", "a": "amber", "g": "green"}
_LAMP_COLOR = {"r": "#d8493f", "a": "#b5791a", "g": "#3b7d18"}
_LAMP_LABEL = {"r": "高风险", "a": "中风险", "g": "低风险"}

# ---------- 免责声明（按 REPORT_SPEC.md：替代「数据来源」技术过程栏，footer 重复）----------
DISCLAIMER_HTML = '''
<div class="card disclaimer">
  <p><b>免责声明</b>：本报告基于网络公开信息生成，仅供求职者个人参考使用，不构成任何就业决策的唯一依据。报告可自由分享与传播，关键项请以面试核实为准，法律责任自负。</p>
</div>'''

# 数据边界章节：免责文字直接并入边界说明的最后一段（与正文连成同一段，不另起段落/卡片）
BOUNDARY_DISCLAIMER_TEXT = "以上说明受公开检索的时效与覆盖所限，关键结论以面试核实为准；本报告仅供个人参考，不构成就业决策的唯一依据，可自由分享，责任自负。"


def _risk_value(lamp: Optional[str]) -> int:
    """灯色 -> 风险分值（1~3），用于雷达图半径。"""
    return {"r": 3, "a": 2, "g": 1}.get(lamp or "a", 2)


def _radar_svg(items: List[tuple]) -> str:
    """七维风险雷达（内联 SVG，自包含、离线可用）。items: [(维度名, lamp), ...]"""
    n = len(items)
    if n < 3:
        return ""
    cx, cy, R = 360, 200, 138
    angle = lambda i: -math.pi / 2 + i * (2 * math.pi / n)

    def pt(i, r):
        a = angle(i)
        return cx + r * math.cos(a), cy + r * math.sin(a)

    # 同心网格环
    rings = ""
    for lvl in (1, 2, 3):
        rr = R * lvl / 3
        pts = " ".join(f"{pt(i, rr)[0]:.1f},{pt(i, rr)[1]:.1f}" for i in range(n))
        rings += f'<polygon points="{pts}" fill="none" stroke="#e3e6ea" stroke-width="1"/>'
    # 轴线
    axes = ""
    for i in range(n):
        x, y = pt(i, R)
        axes += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e3e6ea" stroke-width="1"/>'
    # 数据多边形 + 端点
    data_pts, dots = [], ""
    for i, (name, lamp) in enumerate(items):
        v = _risk_value(lamp)
        x, y = pt(i, R * v / 3)
        data_pts.append(f"{x:.1f},{y:.1f}")
        dots += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{_LAMP_COLOR.get(lamp, "#b5791a")}"/>'
    data_poly = (f'<polygon points="{" ".join(data_pts)}" '
                 f'fill="rgba(15,110,86,.14)" stroke="#0f6e56" stroke-width="2"/>')
    # 维度标签（按方位定对齐）
    labels = ""
    for i, (name, lamp) in enumerate(items):
        x, y = pt(i, R + 26)
        if x < cx - 6:
            anchor = "end"
        elif x > cx + 6:
            anchor = "start"
        else:
            anchor = "middle"
        labels += (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
                   f'font-size="11.5" fill="{_LAMP_COLOR.get(lamp, "#b5791a")}" '
                   f'font-weight="600">{_esc(name)}</text>')
    legend = " ".join(
        f'<span class="badge b-{k}">{"🔴🟡🟢"[i]} {_LAMP_LABEL[k]}</span>'
        for i, k in enumerate(("r", "a", "g"))
    )
    return f'''
    <div class="radar-wrap">
      <svg viewBox="0 0 720 400" width="100%" role="img" aria-label="风险雷达图">
        {rings}{axes}{data_poly}{dots}{labels}
      </svg>
      <div class="radar-legend">{legend}</div>
    </div>'''


def _build_radar_from_blocks(blocks: List[List[str]]) -> str:
    """从风险雷达章节的表格块中解析 (维度, 灯色) 生成雷达图。"""
    for block in blocks:
        non_empty = [b for b in block if b.strip()]
        if non_empty and all(b.strip().startswith("|") for b in non_empty):
            headers, data = _parse_table(non_empty)
            items = []
            for r in data:
                name = r[0] if r else ""
                lamp = None
                for c in r:
                    l = _lamp_of(c)
                    if l:
                        lamp = l
                        break
                if name and lamp:
                    items.append((name, lamp))
            if len(items) >= 3:
                return _radar_svg(items)
    return ""


# ---------- 关系穿透图（数据驱动：LLM 填节点/连线，渲染器自动布局）----------
_TYPE_STYLE = {
    "person": ("#eef3f8", "#5f5e5a", "#5f5e5a"),
    "company": ("#e6f4ef", "#0f6e56", "#0f6e56"),
    "group": ("#e6f4ef", "#0f6e56", "#0f6e56"),
    "risk": ("#fdecea", "#d8493f", "#d8493f"),
}
_RISK_STYLE = {
    "r": ("#fdecea", "#d8493f", "#d8493f"),
    "a": ("#fbf2dd", "#b5791a", "#b5791a"),
    "g": ("#eef6e4", "#3b7d18", "#3b7d18"),
}


def _node_style(node: dict) -> tuple:
    risk = (node or {}).get("risk")
    if risk in _RISK_STYLE:
        return _RISK_STYLE[risk]
    t = (node or {}).get("type")
    if t in _TYPE_STYLE:
        return _TYPE_STYLE[t]
    return ("#f2f3f5", "#8a8f99", "#8a8f99")


def _build_relationship_svg(graph) -> str:
    """关系穿透图入口：按 graph.layout 选择布局。

    默认 hierarchy（上下层级 org-chart，匹配旗舰「新日月」关系穿透图风格）；
    设 layout="radial" 回退到主体居中环绕布局。
    """
    if not isinstance(graph, dict):
        return ""
    layout = str(graph.get("layout") or "hierarchy").lower()
    if layout == "radial":
        return _build_relationship_svg_radial(graph)
    return _build_relationship_svg_hierarchy(graph)


def _build_relationship_svg_radial(graph) -> str:
    """把结构化关系图数据渲染为内联 SVG（数据驱动，自动布局，离线可用）。

    graph = {
      "center": {"label": str, "sub": str?},
      "nodes":  [{"id": str, "label": str, "sub": str?, "type": person|company|group|risk, "risk": r|a|g?}, ...],
      "edges":  [{"from": "center"|节点id, "to": 节点id, "label": str?}, ...],
      "caption": str?   # 一句话穿透结论
    }
    布局：被背调主体居中，关联实体按环形自动排布；边缘自动裁剪到节点边框，箭头指向目标。
    """
    if not isinstance(graph, dict):
        return ""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return ""
    center = graph.get("center") or {}
    cx, cy = 360.0, 220.0
    N = len(nodes)
    if N == 1:
        pos = [(cx, cy + 150.0)]
    elif N <= 7:
        R = 165.0
        pos = [(cx + R * math.cos(-math.pi / 2 + i * 2 * math.pi / N),
                cy + R * math.sin(-math.pi / 2 + i * 2 * math.pi / N)) for i in range(N)]
    else:
        inner, outer = 118.0, 192.0
        half = (N + 1) // 2
        k = N - half
        pos = []
        for i in range(N):
            if i < half:
                ang = -math.pi / 2 + i * 2 * math.pi / half
                r = inner
            else:
                ang = -math.pi / 2 + (i - half) * (2 * math.pi / max(k, 1))
                r = outer
            pos.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))

    boxes = []
    for nd, (px, py) in zip(nodes, pos):
        label = str(nd.get("label", ""))
        sub = str(nd.get("sub", ""))
        w = max(96, min(200, len(label) * 8 + 34))
        h = 32 + (15 if sub else 0)
        boxes.append({"x": px, "y": py, "hw": w / 2.0, "hh": h / 2.0,
                      "label": label, "sub": sub, "style": _node_style(nd)})

    clabel = str(center.get("label", "被背调主体"))
    csub = str(center.get("sub", ""))
    cw = max(110, min(230, len(clabel) * 9 + 44))
    ch = 36 + (15 if csub else 0)
    center_box = {"x": cx, "y": cy, "hw": cw / 2.0, "hh": ch / 2.0,
                  "label": clabel, "sub": csub, "style": ("#ffffff", "#0f6e56", "#0f6e56")}

    idmap = {"center": center_box}
    for nd, b in zip(nodes, boxes):
        if nd.get("id"):
            idmap[str(nd["id"])] = b

    edge_svg = ""
    for e in edges:
        a = idmap.get(str(e.get("from", "")))
        b = idmap.get(str(e.get("to", "")))
        if not a or not b or a is b:
            continue
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        if dx == 0 and dy == 0:
            continue
        sa = min((a["hw"] / abs(dx) if dx else 1e9), (a["hh"] / abs(dy) if dy else 1e9))
        sb = min((b["hw"] / abs(dx) if dx else 1e9), (b["hh"] / abs(dy) if dy else 1e9))
        sx, sy = a["x"] + dx * sa, a["y"] + dy * sa
        ex, ey = b["x"] - dx * sb, b["y"] - dy * sb
        lab = str(e.get("label", ""))
        edge_svg += (f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
                     f'stroke="#8a8f99" stroke-width="1.3" marker-end="url(#arr)"/>')
        if lab:
            mx, my = (sx + ex) / 2, (sy + ey) / 2
            tw = len(lab) * 6.5 + 10
            edge_svg += (f'<rect x="{mx - tw / 2:.1f}" y="{my - 9:.1f}" width="{tw:.1f}" height="17" rx="4" '
                         f'fill="#ffffff" stroke="#e6e8eb"/>'
                         f'<text x="{mx:.1f}" y="{my + 3.5:.1f}" text-anchor="middle" font-size="10.5" fill="#5f5e5a">{_esc(lab)}</text>')

    def _node_svg(b, is_center=False):
        x, y, hw, hh = b["x"], b["y"], b["hw"], b["hh"]
        bg, st, subcol = b["style"]
        fs = 13.5 if is_center else 12.5
        fw = 700 if is_center else 600
        rect = (f'<rect x="{x - hw:.1f}" y="{y - hh:.1f}" width="{2 * hw:.1f}" height="{2 * hh:.1f}" '
                f'rx="9" fill="{bg}" stroke="{st}" stroke-width="1.4"/>')
        if b["sub"]:
            t1 = (f'<text x="{x:.1f}" y="{y - 3:.1f}" text-anchor="middle" font-size="{fs}" '
                  f'font-weight="{fw}" fill="#1f2329">{_esc(b["label"])}</text>')
            t2 = (f'<text x="{x:.1f}" y="{y + 12:.1f}" text-anchor="middle" font-size="10.5" fill="{subcol}">{_esc(b["sub"])}</text>')
        else:
            t1 = (f'<text x="{x:.1f}" y="{y + 4.5:.1f}" text-anchor="middle" font-size="{fs}" '
                  f'font-weight="{fw}" fill="#1f2329">{_esc(b["label"])}</text>')
            t2 = ""
        return rect + t1 + t2

    node_svg = "".join(_node_svg(b) for b in boxes)
    center_svg = _node_svg(center_box, True)

    minX = min([center_box["x"] - center_box["hw"]] + [b["x"] - b["hw"] for b in boxes])
    maxX = max([center_box["x"] + center_box["hw"]] + [b["x"] + b["hw"] for b in boxes])
    minY = min([center_box["y"] - center_box["hh"]] + [b["y"] - b["hh"] for b in boxes])
    maxY = max([center_box["y"] + center_box["hh"]] + [b["y"] + b["hh"] for b in boxes])
    pad = 30
    vbX, vbY = minX - pad, minY - pad
    vbW = (maxX - minX) + 2 * pad
    vbH = (maxY - minY) + 2 * pad + 34

    cap = str(graph.get("caption", ""))
    cap_svg = ""
    if cap:
        cap_svg = (f'<text x="{(vbX + vbW / 2):.1f}" y="{vbH - 12:.1f}" text-anchor="middle" '
                   f'font-size="11.5" fill="#6b7280">⚠ {_esc(cap)}</text>')

    return f'''
    <div class="relgraph-wrap">
      <svg viewBox="{vbX:.1f} {vbY:.1f} {vbW:.1f} {vbH:.1f}" width="100%" role="img" aria-label="关系穿透图">
        <defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#8a8f99" stroke-width="1.4" stroke-linecap="round"/></marker></defs>
        {edge_svg}
        {node_svg}
        {center_svg}
        {cap_svg}
      </svg>
    </div>'''


def _build_relationship_svg_hierarchy(graph) -> str:
    """上下层级 org-chart 布局（匹配旗舰「新日月」关系穿透图风格）。

    数据驱动：center 为根，edges 的 from->to 决定父子层级；渲染器自动分层、
    均分横排、画父子连线（带绿色箭头）。节点框配色沿用 _node_style（红/绿/黄/灰）。
    """
    if not isinstance(graph, dict):
        return ""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return ""
    center = graph.get("center") or {}

    by_id = {}
    for n in nodes:
        if n.get("id"):
            by_id[str(n["id"])] = n
    by_id["center"] = {
        "label": center.get("label", "被背调主体"),
        "sub": center.get("sub", ""),
        # 允许数据指定中心节点颜色（不指定则默认红框 person，向后兼容）
        "type": center.get("type", "person"),
        "risk": center.get("risk", "r"),
    }

    # 建父子关系
    children: Dict[str, List[str]] = {}
    parents: Dict[str, str] = {}
    for e in edges:
        f = str(e.get("from", "")); t = str(e.get("to", ""))
        if not t:
            continue
        children.setdefault(f, []).append(t)
        parents[t] = f
    # 无父节点挂到 center（作为第一层关联实体）
    for nid in list(by_id.keys()):
        if nid == "center":
            continue
        if nid not in parents:
            children.setdefault("center", []).append(nid)
            parents[nid] = "center"

    # BFS 计算层级深度
    depth = {"center": 0}
    queue = ["center"]
    while queue:
        cur = queue.pop(0)
        for ch in children.get(cur, []):
            if ch not in depth:
                depth[ch] = depth[cur] + 1
                queue.append(ch)
    for nid in by_id:
        depth.setdefault(nid, 1)

    layers: Dict[int, List[str]] = {}
    for nid, d in depth.items():
        layers.setdefault(d, []).append(nid)
    for d in layers:
        layers[d].sort(key=lambda x: (x != "center", x))

    W = 720
    top = 42
    step_y = 96
    layer_y = {d: top + d * step_y for d in layers}

    def box_of(nid):
        nd = by_id.get(nid, {})
        label = str(nd.get("label", ""))
        sub = str(nd.get("sub", ""))
        w = max(96, min(190, len(label) * 8 + 34))
        h = 34 + (16 if sub else 0)
        return {"label": label, "sub": sub, "hw": w / 2.0, "hh": h / 2.0,
                "style": _node_style(nd)}

    positions = {}
    for d, ids in layers.items():
        n = len(ids)
        if n == 1:
            xs = [W / 2]
        else:
            margin = 70
            gap = (W - 2 * margin) / (n - 1)
            xs = [margin + i * gap for i in range(n)]
        for nid, x in zip(ids, xs):
            b = box_of(nid)
            b["x"], b["y"] = x, layer_y[d]
            positions[nid] = b

    def _node_svg(b, is_center=False):
        x, y, hw, hh = b["x"], b["y"], b["hw"], b["hh"]
        bg, st, subcol = b["style"]
        fs = 13.5 if is_center else 12.5
        fw = 700 if is_center else 600
        rect = (f'<rect x="{x - hw:.1f}" y="{y - hh:.1f}" width="{2 * hw:.1f}" height="{2 * hh:.1f}" '
                f'rx="10" fill="{bg}" stroke="{st}" stroke-width="1.4"/>')
        if b["sub"]:
            t1 = (f'<text x="{x:.1f}" y="{y - 3:.1f}" text-anchor="middle" font-size="{fs}" '
                  f'font-weight="{fw}" fill="#1f2329">{_esc(b["label"])}</text>')
            t2 = (f'<text x="{x:.1f}" y="{y + 12:.1f}" text-anchor="middle" font-size="10.5" fill="{subcol}">{_esc(b["sub"])}</text>')
        else:
            t1 = (f'<text x="{x:.1f}" y="{y + 4.5:.1f}" text-anchor="middle" font-size="{fs}" '
                  f'font-weight="{fw}" fill="#1f2329">{_esc(b["label"])}</text>')
            t2 = ""
        return rect + t1 + t2

    edge_svg = ""
    for e in edges:
        f = str(e.get("from", "")); t = str(e.get("to", ""))
        a = positions.get(f); b = positions.get(t)
        if not a or not b or a is b:
            continue
        sx, sy = a["x"], a["y"] + a["hh"]
        ex, ey = b["x"], b["y"] - b["hh"]
        mid_y = (sy + ey) / 2
        lab = str(e.get("label", ""))
        edge_svg += (f'<path d="M{sx:.1f},{sy:.1f} C{sx:.1f},{mid_y:.1f} {ex:.1f},{mid_y:.1f} {ex:.1f},{ey:.1f}" '
                     f'fill="none" stroke="#0f6e56" stroke-width="1.3" marker-end="url(#arr)" opacity="0.9"/>')
        if lab:
            mx = (sx + ex) / 2
            tw = len(lab) * 6.5 + 10
            edge_svg += (f'<rect x="{mx - tw / 2:.1f}" y="{mid_y - 9:.1f}" width="{tw:.1f}" height="17" rx="4" '
                         f'fill="#ffffff" stroke="#e6e8eb"/>'
                         f'<text x="{mx:.1f}" y="{mid_y + 3.5:.1f}" text-anchor="middle" font-size="10.5" fill="#5f5e5a">{_esc(lab)}</text>')

    node_svg = "".join(_node_svg(positions[nid], nid == "center") for nid in positions)

    minX = min(b["x"] - b["hw"] for b in positions.values())
    maxX = max(b["x"] + b["hw"] for b in positions.values())
    minY = min(b["y"] - b["hh"] for b in positions.values())
    maxY = max(b["y"] + b["hh"] for b in positions.values())
    pad = 28
    vbX, vbY = minX - pad, minY - pad
    vbW = (maxX - minX) + 2 * pad
    cap_y = maxY + pad + 22
    vbH = (maxY - minY) + 2 * pad + 34

    cap = str(graph.get("caption", ""))
    cap_svg = ""
    if cap:
        cap_svg = (f'<text x="{(vbX + vbW / 2):.1f}" y="{cap_y:.1f}" text-anchor="middle" '
                   f'font-size="11.5" fill="#6b7280">⚠ {_esc(cap)}</text>')

    return f'''
    <div class="relgraph-wrap">
      <svg viewBox="{vbX:.1f} {vbY:.1f} {vbW:.1f} {vbH:.1f}" width="100%" role="img" aria-label="关系穿透图">
        <defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#0f6e56" stroke-width="1.4" stroke-linecap="round"/></marker></defs>
        {edge_svg}
        {node_svg}
        {cap_svg}
      </svg>
    </div>'''


def _extract_graph_from_md(sections) -> dict:
    """从报告任意章节的 ```relationship-graph / ```json 代码块解析关系图数据。
    扫描全部章节（LLM 把图块放在「关系穿透」或其它章节都应能抓到）。"""
    for sec in sections:
        text = "\n".join("\n".join(b) for b in sec["blocks"])
        m = re.search(r"```(?:relationship-graph|json)?\s*(\{.*?\})\s*```", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


def _esc(s) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def _default_generated_at() -> str:
    """生成时间：取本机日期，避免每次读文件差异。"""
    try:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return ""


# 结构化输出模板的字段顺序（参考旗舰报告 meta 布局）
META_FIELDS = [
    ("核查法律主体", "subject"),
    ("统一社会信用代码", "credit_code"),
    ("法定代表人 / 实控人", "controller"),
    ("资本市场", "capital_market"),
    ("控制权", "controlling"),
    ("体量", "scale"),
    ("岗位", "position"),
    ("城市", "city"),
    ("层级", "level"),
    ("阶段", "stage"),
    ("生成时间", "generated_at"),
    ("备注", "notes"),
]


def _build_meta_html(meta_lines, meta, company_fallback):
    """按结构化模板产出 meta 区块 HTML。

    优先级：
    1) 结构化 meta 参数字段（subject / credit_code / controller / ...）
    2) 旧 markdown blockquote 行（用全角 / ASCII 竖线分隔的多字段）
    3) 都没有时，至少输出「核查法律主体」「生成时间」两行

    长字段（>40 字）应用 class="full" 占满整行，避免溢出挤坏 2 列布局。
    """
    meta = meta or {}
    has_meta = bool(meta)  # 是否走结构化字段（后端程序化传入 meta 字典）
    items = []  # (label, value) 列表

    # 1) 仅当结构化 meta 存在时优先取（后端路径）
    if has_meta:
        for label, key in META_FIELDS:
            v = meta.get(key)
            if not v:
                if key == "subject":
                    v = company_fallback
                elif key == "generated_at":
                    v = meta.get(key) or _default_generated_at()
                else:
                    continue
            items.append((label, str(v)))

    # 2) 结构化字段为空时，从 markdown blockquote 兜底（开源/本地路径：LLM 仅输出 blockquote）
    if not has_meta and meta_lines:
        for ln in meta_lines:
            if "数据来源" in ln:
                continue
            for piece in re.split(r"[\u007c\uff5c]", ln):
                piece = piece.strip()
                if not piece:
                    continue
                # 尝试按首个"名：值"或"名="切分
                m = re.match(r"^([^：:＝=]+)[：:＝=]\s*(.*)$", piece)
                if m:
                    label, val = m.group(1).strip(), m.group(2).strip()
                    items.append((label, val))
                else:
                    items.append(("", piece))  # 无字段名前缀，原样输出

    # 3) 产出 HTML（长字段 class="full"）
    parts = []
    for label, val in items:
        cls = ' class="full"' if len(val) > 40 else ""
        if label:
            parts.append(f'<div{cls}><b>{_esc(label)}</b>：{_esc(val)}</div>')
        else:
            parts.append(f'<div{cls}>{_esc(val)}</div>')
    return "".join(parts)
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def _inline(s: str) -> str:
    """转义后处理 **加粗**（历史函数，纯文本来源请改用 _rich）。"""
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return s


def _rich(s: str) -> str:
    """纯文本 -> HTML 的完整管线：先转义 -> **加粗** -> [Lx] 来源徽章。

    关键顺序：徽章标签必须在 _esc 之后注入，否则 <span> 会被转义成
    可见文本（之前「关键证据」把 <span class="badge"> 显示成代码就是这个原因）。
    """
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return _replace_src_badge(s)


def _lamp_of(text: str) -> Optional[str]:
    for k, v in LAMP_CLASS.items():
        if k in text:
            return v
    return None


def _parse_table(block_lines: List[str]):
    """解析 markdown 表格 -> (headers, rows)。"""
    rows = []
    for ln in block_lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return [], []
    headers = rows[0]
    data = []
    for r in rows[1:]:
        # 跳过分隔行
        if all(set(c) <= set("-: ") for c in r) and any("-" in c for c in r):
            continue
        data.append(r)
    return headers, data


def _render_list(items: List[str]) -> str:
    """渲染列表，按行首灯色给左侧色条；[Lx] 来源徽章着色。"""
    out = []
    for it in items:
        lamp = _lamp_of(it)
        cls = f" q {lamp}" if lamp else " q"
        txt = _rich(it)
        out.append(f'<div class="{cls.strip()}">{txt}</div>')
    return "\n".join(out)


def _replace_src_badge(text: str) -> str:
    """将 [L1 硬数据] / [L3 口碑] / [L4 用户情报] 替换为彩色徽章。"""
    def _m(m):
        label = m.group(1)
        key = label[:2].upper()
        color = {"L1": "r", "L3": "g", "L4": "a"}.get(key, "a")
        return f'<span class="badge b-{color}">{_esc(label)}</span>'
    return re.sub(r"\[([^\]]+)\]", _m, text)


def _parse(md: str):
    """把报告 markdown 拆成 (title, meta_lines, sections)。"""
    lines = (md or "").splitlines()
    title = ""
    meta_lines: List[str] = []
    sections: List[Dict] = []
    cur = None
    buf: List[str] = []

    def _flush():
        nonlocal cur, buf
        if cur is not None:
            # 按空行把章节切成多个块（段落/表格/列表/代码块各自独立），
            # 否则混排时整段被当成一个段落，导致代码块等内容泄漏。
            blocks = []
            cb: List[str] = []
            for ln in buf:
                if ln.strip() == "":
                    if cb:
                        blocks.append(cb)
                        cb = []
                else:
                    cb.append(ln)
            if cb:
                blocks.append(cb)
            cur["blocks"] = blocks
            sections.append(cur)
        cur = None
        buf = []

    for ln in lines:
        if ln.startswith("# "):
            _flush()
            title = ln[2:].strip()
            continue
        if ln.startswith("> ") and cur is None:
            meta_lines.append(ln[2:].strip())
            continue
        if ln.startswith("## "):
            _flush()
            cur = {"heading": ln[3:].strip(), "blocks": []}
            continue
        if cur is None:
            continue
        buf.append(ln)
    _flush()
    return title, meta_lines, sections


def _is_fence(block) -> bool:
    """判断一个块是否为 fenced 代码块（首行 ``` 起、末行 ``` 止）。"""
    if not block:
        return False
    ne = [b for b in block if b.strip()]
    if not ne:
        return False
    return ne[0].strip().startswith("```") and ne[-1].strip() == "```"


def _is_graph_fence(block) -> bool:
    """关系图 JSON 代码块（```relationship-graph / ```json 且内含 { }）。"""
    if not _is_fence(block):
        return False
    text = "\n".join(block)
    return ("relationship-graph" in block[0]) or (
        block[0].strip().startswith("```json") and "{" in text
    )


def _render_blocks(blocks: List[List[str]]) -> str:
    """渲染一个 section 内的多段块（段落/表格/列表/代码块交替）。

    代码块处理：
    - 关系图 fenced 块（relationship-graph / json+对象）：已被渲染为 SVG，此处**跳过**，避免裸 JSON 泄漏；
    - 其它 fenced 块：渲染为 <pre>，不再被误当普通段落拼成乱码。
    """
    out = []
    for block in blocks:
        # 过滤空行，识别块类型
        if not block:
            continue
        non_empty = [b for b in block if b.strip()]
        if not non_empty:
            continue
        if _is_graph_fence(block):
            continue  # 关系图已转 SVG，剥离原始文本
        if _is_fence(block):
            code = "\n".join(ln.strip("`").strip() for ln in non_empty[1:-1])
            out.append(f'<pre class="code">{_esc(code)}</pre>')
            continue
        if all(b.strip().startswith("|") for b in non_empty):
            headers, data = _parse_table(non_empty)
            out.append(_render_table(headers, data))
        elif all(b.strip().startswith(("- ", "* ")) for b in non_empty):
            items = [b.strip()[2:].strip() for b in non_empty]
            out.append(_render_list(items))
        else:
            # 段落：合并连续行
            para = " ".join(b.strip() for b in non_empty)
            out.append(f'<p>{_rich(para)}</p>')
    return "\n".join(out)


def _render_table(headers, data) -> str:
    # 判断灯色列
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    rows = []
    for r in data:
        tds = []
        for i, cell in enumerate(r):
            if i < len(headers) and headers[i] in ("灯色", "信号灯"):
                lamp = _lamp_of(cell)
                if lamp:
                    tds.append(f'<td><span class="badge b-{lamp}">{_esc(cell)}</span></td>')
                else:
                    tds.append(f"<td>{_esc(cell)}</td>")
            else:
                tds.append(f"<td>{_rich(cell)}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f'<table><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def _render_financials(fin: dict) -> str:
    name = fin.get("security_name") or fin.get("company") or ""
    code = fin.get("security_code") or ""
    date = fin.get("latest_report_date") or ""
    rev = fin.get("revenue")
    npf = fin.get("net_profit")
    roe = fin.get("roe")
    eps = fin.get("eps")
    ryoy = fin.get("revenue_yoy")
    pyoy = fin.get("profit_yoy")
    audit = fin.get("audit_opinion") or "（未获取）"
    flags = fin.get("flags") or []

    def _yi(v):
        if v is None:
            return "—"
        try:
            return f"{v/1e8:.2f} 亿"
        except TypeError:
            return str(v)

    def _pct(v):
        if v is None:
            return "—"
        return f"{v:+.1f}%"

    flag_html = ""
    if flags:
        flag_html = '<div class="card" style="background:#fdecea;border-color:#f0c4bf">' + \
                    "<b>⚠ 财务信号</b>" + "".join(f"<div class='note'>• {_esc(f)}</div>" for f in flags) + "</div>"

    return f"""
    <section>
      <h2>财务快照（上市公司 · 东方财富公开数据）</h2>
      <div class="meta">
        <div><b>证券简称</b>：{_esc(name)}（{_esc(code)}）</div>
        <div><b>最新报告期</b>：{_esc(date)}</div>
        <div><b>营业收入</b>：{_yi(rev)}（同比 {_pct(ryoy)}）</div>
        <div><b>归母净利润</b>：{_yi(npf)}（同比 {_pct(pyoy)}）</div>
        <div><b>ROE</b>：{_esc(roe)}%</div>
        <div><b>EPS</b>：{_esc(eps)}</div>
        <div><b>审计意见</b>：{_esc(audit)}</div>
      </div>
      {flag_html}
    </section>
    """


# ---------- 样式（自包含，复用示例报告设计语言）----------
CSS = """
:root{
  --ink:#1f2329; --muted:#6b7280; --line:#e6e8eb; --bg:#ffffff; --bg2:#f7f8fa;
  --red:#d8493f; --redbg:#fdecea; --amber:#b5791a; --amberbg:#fbf2dd; --green:#3b7d18; --greenbg:#eef6e4;
  --brand:#0f6e56; --brandbg:#e6f4ef;
}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:#eef0f2;line-height:1.65;font-size:14px}
.page{max-width:920px;margin:24px auto;background:var(--bg);padding:40px 48px;box-shadow:0 1px 3px rgba(0,0,0,.08);border-radius:10px}
header.top{border-bottom:3px solid var(--brand);padding-bottom:18px;margin-bottom:8px}
.kicker{color:var(--brand);font-weight:600;letter-spacing:.5px;font-size:12px}
h1{font-size:24px;margin:6px 0 4px;font-weight:700}
.sub{color:var(--muted);font-size:13px}
.meta{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 24px;margin:16px 0;padding:14px 18px;background:var(--bg2);border-radius:8px;font-size:13px}
.meta b{color:var(--ink)}
.meta > div.full{grid-column:1/-1}
.verdict{display:flex;align-items:center;gap:16px;margin:18px 0;padding:18px 20px;border-radius:10px;background:var(--amberbg)}
.verdict.red{background:var(--redbg)} .verdict.green{background:var(--greenbg)} .verdict.amber{background:var(--amberbg)}
.lamp{width:54px;height:54px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:26px;flex:0 0 auto;border:2px solid}
.lamp.r{background:var(--redbg);border-color:var(--red)} .lamp.a{background:var(--amberbg);border-color:var(--amber)} .lamp.g{background:var(--greenbg);border-color:var(--green)}
.verdict .vt{font-size:18px;font-weight:700} .verdict .vd{color:#444;margin-top:2px;font-size:13px}
section{margin:30px 0}
h2{font-size:17px;margin:0 0 14px;padding-left:10px;border-left:4px solid var(--brand)}
h3{font-size:14px;margin:18px 0 8px;color:var(--brand)}
p{margin:8px 0}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}
th{background:var(--bg2);font-weight:600}
.card{background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}
.badge{display:inline-block;min-width:20px;padding:1px 8px;border-radius:20px;font-size:12px;font-weight:600;margin-right:4px}
.b-r{background:var(--redbg);color:var(--red)} .b-a{background:var(--amberbg);color:var(--amber)} .b-g{background:var(--greenbg);color:var(--green)}
.q{margin:8px 0;padding:8px 12px;border-radius:7px;background:#fff;border:1px solid var(--line)}
.q.r{border-left:4px solid var(--red)} .q.a{border-left:4px solid var(--amber)} .q.g{border-left:4px solid var(--green)}
ul{margin:8px 0;padding-left:20px} li{margin:5px 0}
.code{background:#f5f6f8;border:1px solid var(--line);border-radius:7px;padding:10px 12px;font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--ink);white-space:pre-wrap;word-break:break-word;margin:8px 0}
.radar-wrap{text-align:center;margin:6px 0 16px}
.radar-wrap svg{max-width:600px;height:auto;margin:0 auto}
.radar-legend{margin-top:8px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap}
.relgraph-wrap{text-align:center;margin:6px 0 4px}
.relgraph-wrap svg{max-width:700px;height:auto;margin:0 auto;display:block}
.disclaimer{background:#fff8e8;border-left:3px solid var(--amber);margin:14px 0}
.disclaimer p{margin:0;font-size:13px;color:#8a6d1a}
.footer{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:100%}}
"""


def render_report(markdown: str, meta: Optional[Dict] = None,
                  financials: Optional[Dict] = None) -> str:
    meta = meta or {}
    title, meta_lines, sections = _parse(markdown)

    # 头部
    company = meta.get("company") or title.replace("反向背调报告 · ", "").strip() or "未知公司"
    sub_parts = []
    for k in ("position", "city", "level", "stage"):
        if meta.get(k):
            label = {"position": "岗位", "city": "城市", "level": "层级", "stage": "阶段"}[k]
            sub_parts.append(f"{label}：{meta[k]}")
    sub = " · ".join(sub_parts)
    meta_html = _build_meta_html(meta_lines, meta, company)

    # 关系穿透图：优先取结构化字段，否则从报告任意章节解析 fenced JSON 块
    graph = meta.get("relationship_graph")
    rel_svg = _build_relationship_svg(graph) if graph else ""
    if not rel_svg:
        md_graph = _extract_graph_from_md(sections)
        if md_graph:
            rel_svg = _build_relationship_svg(md_graph)

    # 总评章节 -> verdict 区块
    verdict_html = ""
    body_sections = []
    has_rel_section = False
    for sec in sections:
        heading = sec["heading"]
        if "关系穿透" in heading or ("关系" in heading and "穿透" in heading):
            has_rel_section = True
            blocks_html = _render_blocks(sec["blocks"])  # 图块已由 _render_blocks 全局剥离
            inner = (f'<div class="card relgraph">{rel_svg}</div>' if rel_svg else "") + blocks_html
            body_sections.append(f'<section><h2>{_esc(heading)}</h2>{inner}</section>')
            continue
        blocks_html = _render_blocks(sec["blocks"])
        if "总评" in heading:
            lamp = _lamp_of(heading) or "a"
            vclass = LAMP_VERDICT.get(lamp, "amber")
            lamp_emoji = {"r": "🔴", "a": "🟡", "g": "🟢"}[lamp]
            # 标题去掉灯色 emoji 后的文案
            vt = heading.replace("🔴", "").replace("🟡", "").replace("🟢", "").replace("总评：", "").replace("总评:", "").strip() or "综合评级"
            verdict_html = f"""
      <div class="verdict {vclass}">
        <div class="lamp {lamp}">{lamp_emoji}</div>
        <div>
          <div class="vt">{_esc(vt)}</div>
          <div class="vd">{blocks_html.replace('<p>','').replace('</p>','')}</div>
        </div>
      </div>"""
        elif "风险雷达" in heading:
            radar = _build_radar_from_blocks(sec["blocks"])
            body_sections.append(f'<section><h2>{_esc(heading)}</h2>{radar}{blocks_html}</section>')
        else:
            # 数据边界章节：免责文字并入最后一段（与正文连成同一段、无独立卡片）。
            # 仅当 Markdown 正文尚未自带免责声明时才拼接，避免双重免责重复。
            fused = blocks_html
            if "数据边界" in heading and "本报告仅供个人参考" not in fused:
                idx = fused.rfind("</p>")
                if idx != -1:
                    fused = fused[:idx] + " " + BOUNDARY_DISCLAIMER_TEXT + fused[idx:]
                else:
                    fused = fused + f"<p>{BOUNDARY_DISCLAIMER_TEXT}</p>"
            body_sections.append(f'<section><h2>{_esc(heading)}</h2>{fused}</section>')

    # 有图但报告无「关系穿透」章节时，补一个章节承载图
    if rel_svg and not has_rel_section:
        body_sections.append(
            f'<section><h2>关系穿透</h2><div class="card relgraph">{rel_svg}</div></section>')

    fin_html = _render_financials(financials) if financials else ""

    # 把财务快照插到正文最前（紧接总评之后）
    body = fin_html + "\n".join(body_sections)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>反向背调报告 · {_esc(company)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
  <header class="top">
    <div class="kicker">求职者反向背调报告</div>
    <h1>{_esc(company)}</h1>
    <div class="sub">{_esc(sub)}</div>
  </header>
  {('<div class="meta">' + meta_html + '</div>') if meta_html else ''}
  {DISCLAIMER_HTML}
  {verdict_html}
  {body}
</div>
</body>
</html>"""
