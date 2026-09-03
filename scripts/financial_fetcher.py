"""上市公司财报抓取 —— 直接调东方财富公开 API（无需密钥）。

用途：反向背调「成长发展 / 业务线风险」维度补强。
- 营收、净利润趋势（连续下滑 = 经营恶化信号）
- ROE / EPS（资本回报）
- 审计意见（保留 / 否定意见 = 重大红旗，best-effort）
- 员工人数 / 人均薪酬（best-effort，推算薪酬结构风险）

本脚本是开源包 goodfit-skill 的一部分，纯标准库、无密钥、可本地离线运行。
仅对上市公司生效：先在实体对齐环节确认目标为上市公司（拿到股票代码）后再调用。
依赖：仅 Python 标准库（urllib / json）。
"""
import json
import urllib.parse
import urllib.request

EM_BASE = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

# 主要财务指标（营收 / 净利 / ROE / EPS / 同比）
INDICATOR_REPORT = "RPT_LICO_FN_CPD"
# 审计意见（best-effort，字段可能随站点调整）
AUDIT_REPORT = "RPT_LICO_FN_AOP"


def _get_json(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://emweb.securities.eastmoney.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    # 东方财富对无效列名/参数会返回 success:false，必须显式抛出，避免静默吞错
    if data.get("success") is False:
        raise RuntimeError(f"EastMoney API 返回错误: {data.get('message')} (code={data.get('code')})")
    return data


def _sec_code(code: str) -> str:
    """接受 '600000' / 'sh600000' / 'SZ000001'，统一返回 6 位代码。"""
    code = (code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if code.startswith(p):
            return code[2:]
    return code


def _fetch_rows(report: str, code: str, columns: str, page_size: int = 8) -> list:
    params = {
        "reportName": report,
        "columns": columns,
        "filter": f'(SECURITY_CODE="{code}")',
        "pageSize": str(page_size),
        "sortColumns": "REPORTDATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    url = EM_BASE + "?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    return (data.get("result") or {}).get("data") or []


def _annual(rows: list) -> list:
    return [r for r in rows if (r.get("REPORTDATE") or "").endswith("12-31 00:00:00")]


def _fmt_yi(v) -> str:
    if v is None:
        return "未知"
    try:
        return f"{v / 1e8:.2f}亿"
    except TypeError:
        return str(v)


def fetch_financials(company: str, code: str = None) -> dict:
    """返回结构化财报摘要；无法获取时返回 None。

    code: 股票代码（6 位或带市场前缀）。由实体对齐 / 商查 MCP 提供。
    """
    if not code:
        return None
    sec = _sec_code(code)
    if not sec:
        return None

    try:
        rows = _fetch_rows(
            INDICATOR_REPORT, sec,
            "SECURITY_CODE,SECURITY_NAME_ABBR,REPORTDATE,TOTAL_OPERATE_INCOME,"
            "PARENT_NETPROFIT,WEIGHTAVG_ROE,BASIC_EPS",
        )
    except Exception:
        return None

    annuals = _annual(rows) or rows[:3]
    if not annuals:
        return None

    latest = annuals[0]
    prev = annuals[1] if len(annuals) > 1 else None

    # 同比（YoY）自行用上一年数据计算（东方财富该报表不提供 YOY 字段）
    rev_prev = prev.get("TOTAL_OPERATE_INCOME") if prev else None
    np_prev = prev.get("PARENT_NETPROFIT") if prev else None
    rev_now = latest.get("TOTAL_OPERATE_INCOME")
    np_now = latest.get("PARENT_NETPROFIT")
    revenue_yoy = (rev_now - rev_prev) / rev_prev * 100 if (rev_prev not in (None, 0) and rev_now is not None) else None
    profit_yoy = (np_now - np_prev) / np_prev * 100 if (np_prev not in (None, 0) and np_now is not None) else None

    out = {
        "company": company,
        "security_code": latest.get("SECURITY_CODE"),
        "security_name": latest.get("SECURITY_NAME_ABBR"),
        "latest_report_date": (latest.get("REPORTDATE") or "")[:10],
        "revenue": rev_now,
        "net_profit": np_now,
        "roe": latest.get("WEIGHTAVG_ROE"),
        "eps": latest.get("BASIC_EPS"),
        "revenue_yoy": revenue_yoy,
        "profit_yoy": profit_yoy,
        "audit_opinion": _fetch_audit(sec),
    }

    flags = []
    if prev:
        if rev_prev and rev_now is not None and rev_now < rev_prev:
            pct = f"（-{abs(revenue_yoy):.1f}%）" if revenue_yoy is not None else ""
            flags.append(f"最新年度营收低于上一年度（经营承压{pct}）")
        if np_prev and np_now is not None and np_now < np_prev:
            pct = f"（-{abs(profit_yoy):.1f}%）" if profit_yoy is not None else ""
            flags.append(f"最新年度净利润低于上一年度（盈利恶化{pct}）")
    if latest.get("PARENT_NETPROFIT") is not None and latest["PARENT_NETPROFIT"] < 0:
        flags.append("净利润为负（亏损）")
    if latest.get("WEIGHTAVG_ROE") is not None and latest["WEIGHTAVG_ROE"] < 5:
        flags.append(f"ROE 偏低（{latest['WEIGHTAVG_ROE']}%，资本回报弱）")

    audit = out.get("audit_opinion")
    if audit and audit not in ("标准无保留意见", "标准无保留", None):
        flags.append(f"审计意见异常：{audit}")

    out["flags"] = flags
    out["summary"] = _summarize(out)
    return out


def _fetch_audit(sec: str):
    """best-effort 审计意见；失败返回 None，不阻断主流程。"""
    try:
        rows = _fetch_rows(
            AUDIT_REPORT, sec,
            "SECURITY_CODE,REPORTDATE,AUDIT_OPINION_TYPE", page_size=2,
        )
        if rows:
            return rows[0].get("AUDIT_OPINION_TYPE")
    except Exception:
        return None
    return None


def _summarize(out: dict) -> str:
    lines = [
        f"{out['security_name']}（{out['security_code']}）最新报告期 {out['latest_report_date']}：",
        f"营业收入 {_fmt_yi(out['revenue'])}，净利润 {_fmt_yi(out['net_profit'])}，"
        f"ROE {out['roe']}%，EPS {out['eps']}。",
    ]
    if out["flags"]:
        lines.append("⚠ 财务信号：" + "；".join(out["flags"]))
    else:
        lines.append("近年度关键财务指标未见明显恶化信号（基于公开财报摘要）。")
    return "".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="上市公司财报抓取（东方财富公开 API，无密钥）")
    ap.add_argument("--company", required=True, help="公司名（用于报告标注）")
    ap.add_argument("--code", required=True, help="股票代码，如 600000 / sh600000 / 000001")
    ap.add_argument("-o", "--output", help="输出 JSON 路径（默认打印到 stdout）")
    args = ap.parse_args()

    data = fetch_financials(args.company, args.code)
    if not data:
        sys_msg = {"ok": False, "message": "未获取到财报数据（请确认代码正确且为上市公司）"}
        if args.output:
            with open(args.output, "w", encoding="utf-8") as _f:
                _f.write(json.dumps(sys_msg, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(sys_msg, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"[完成] 已写入：{args.output}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
