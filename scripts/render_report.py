#!/usr/bin/env python3
"""反向背调报告 · 本地渲染 CLI

把 LLM（或任意 agent）按 `references/system_prompt.md` 模板产出的 Markdown 报告
渲染成自包含 HTML（内联 CSS + 内联 SVG 风险雷达图 + 关系穿透图）。

用法：
    python render_report.py report.md                 # 输出 report.html（同目录）
    python render_report.py report.md -o out.html     # 指定输出路径
    python render_report.py report.md --financials fin.json
            # 叠加「财务快照」卡（fin.json 用 financial_fetcher.py 生成）

依赖：仅 Python 标准库（report_renderer.py 只用 html/json/math/re/typing）。
无需网络、无需 API key、无需后端服务——纯本地离线渲染。
"""
import argparse
import json
import os
import sys

# 让脚本无论从哪个工作目录调用都能找到同目录的 report_renderer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report_renderer import render_report  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="反向背调 Markdown -> 自包含 HTML 渲染器")
    ap.add_argument("input", help="LLM 产出的报告 Markdown 路径")
    ap.add_argument("-o", "--output", help="输出 HTML 路径（默认与输入同名 .html）")
    ap.add_argument("--financials", help="上市公司财报 JSON（用 financial_fetcher.py 生成），渲染「财务快照」卡")
    args = ap.parse_args()

    in_path = args.input
    if not os.path.isfile(in_path):
        sys.exit(f"[错误] 找不到输入文件：{in_path}")
    out_path = args.output or (os.path.splitext(in_path)[0] + ".html")

    financials = None
    if args.financials:
        if not os.path.isfile(args.financials):
            sys.exit(f"[错误] 找不到财报文件：{args.financials}")
        with open(args.financials, encoding="utf-8") as _f:
            financials = json.load(_f)

    with open(in_path, encoding="utf-8") as _f:
        markdown = _f.read()
    html = render_report(markdown, financials=financials)  # meta 从 markdown 的 blockquote + 关系图 fenced 块解析
    with open(out_path, "w", encoding="utf-8") as _f:
        _f.write(html)
    print(f"[完成] 已渲染：{out_path}  ({len(html)} 字符)" + ("  （含财务快照）" if financials else ""))


if __name__ == "__main__":
    main()
