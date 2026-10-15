#!/usr/bin/env python3
"""抓取 天天基金(申购状态) + 腾讯行情(场内价格/IOPV/溢价)，生成 data.json 和 index.html"""
import json, re, time, datetime, urllib.request, html, os, concurrent.futures as cf
from funds import NDX_OTC, SPX_OTC, NDX_ETF, SPX_ETF
OUT = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0"}
CN = datetime.timezone(datetime.timedelta(hours=8))

def get(url, enc="utf-8", headers=UA, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                return r.read().decode(enc, "replace")
        except Exception as e:
            err = e; time.sleep(1 + i)
    raise err

def parse_limit(s):
    """'限大额(单日投资上限10元)' -> 10 ; '1万元' -> 10000"""
    m = re.search(r"上限\s*([\d.]+)\s*(万)?元", s or "")
    if not m: return None
    v = float(m.group(1)) * (10000 if m.group(2) else 1)
    return int(v) if v == int(v) else v

def otc(item, index):
    code, name, direct = item
    rec = {"code": code, "name": name, "index": index, "direct_note": direct}
    try:
        d = json.loads(get(f"https://fundmobapi.eastmoney.com/FundMApi/FundBaseTypeInformation.ashx?FCODE={code}&deviceid=Wap&plat=Wap&product=EFund&version=2.0.0"))["Datas"] or {}
        sg = d.get("SGZT", "")
        rec.update(api_name=d.get("SHORTNAME"), sgzt=sg, shzt=d.get("SHZT"), nav=d.get("DWJZ"), nav_date=d.get("FSRQ"),
                   rate=d.get("RATE"), dtzt=d.get("DTZT"))
        lim = parse_limit(sg)
        if "暂停" in sg: rec["status"], rec["limit"] = "暂停申购", 0
        elif lim is not None: rec["status"], rec["limit"] = "限额", lim
        elif "限大额" in sg: rec["status"], rec["limit"] = "限额", None   # 天天基金未给出金额
        elif "开放" in sg: rec["status"], rec["limit"] = "开放", None
        else: rec["status"], rec["limit"] = sg or "未知", None
    except Exception as e:
        rec.update(status="抓取失败", limit=None, error=str(e))
    return rec

def etfs(codes, index):
    raw = get("https://qt.gtimg.cn/q=" + ",".join(codes), enc="gbk")
    out = []
    for line in raw.strip().split(";"):
        m = re.match(r'\s*v_(\w+)="(.*)"', line)
        if not m: continue
        f = m.group(2).split("~")
        try:
            price, iopv, nav = float(f[3]), float(f[78] or 0), float(f[81] or 0)
            prem = float(f[77]) if f[77] not in ("", None) else (price / iopv - 1) * 100 if iopv else None
            shares = float(f[72] or 0)
            out.append({"code": f[2], "name": f[1], "index": index, "price": price, "chg_pct": float(f[32]),
                        "iopv": iopv, "nav": nav, "premium": round(prem, 2) if prem is not None else None,
                        "nav_premium": round((price / nav - 1) * 100, 2) if nav else None,
                        "scale_yi": round(shares * nav / 1e8, 1) if nav else None,
                        "amount_wan": float(f[37] or 0), "quote_time": f[30]})
        except Exception as e:
            out.append({"code": m.group(1), "name": f[1] if len(f) > 1 else "", "index": index, "error": str(e)})
    return out

def main():
    with cf.ThreadPoolExecutor(8) as ex:
        ndx = list(ex.map(lambda x: otc(x, "纳指100"), NDX_OTC))
        spx = list(ex.map(lambda x: otc(x, "标普500"), SPX_OTC))
    data = {"updated": datetime.datetime.now(CN).strftime("%Y-%m-%d %H:%M"), "otc": {"纳指100": ndx, "标普500": spx},
            "etf": {"纳指100": etfs(NDX_ETF, "纳指100"), "标普500": etfs(SPX_ETF, "标普500")}}
    json.dump(data, open(f"{OUT}/data.json", "w"), ensure_ascii=False, indent=1)
    open(f"{OUT}/index.html", "w").write(render(data))
    fails = sum(r["status"] == "抓取失败" for r in ndx + spx) + sum("error" in x for v in data["etf"].values() for x in v)
    etf_n = sum(len(v) for v in data["etf"].values())
    print("ok", data["updated"], "otc", len(ndx + spx), "etf", etf_n, "failures", fails)
    if fails or etf_n < len(NDX_ETF) + len(SPX_ETF): raise SystemExit("fetch failures")

def order(r):
    s = r["status"]
    return (0 if s == "开放" else 1 if s == "限额" else 2, -(r["limit"] if r["limit"] is not None else -1) if s == "限额" else 0)

def render(d):
    e = html.escape
    parts = []
    for idx in ("纳指100", "标普500"):
        rows = sorted(d["otc"][idx], key=order)
        buyable = [r for r in rows if r["status"] in ("限额", "开放")]
        total = sum(r["limit"] or 0 for r in buyable)
        trs = "".join(
            f'<tr class="{ "ok" if r["status"]=="开放" else "lim" if r["status"]=="限额" else "no"}"><td><b>{e(r["code"])}</b><br><small>{e(r.get("api_name") or r["name"])}</small>'
            + (f'<br><small class="dn">{e(r["direct_note"])}</small>' if r["direct_note"] else "")
            + f'</td><td>{ "不限" if r["status"]=="开放" else ((f'{r["limit"]:g}元' if r["limit"] is not None else "限额<br><small>金额未披露</small>")) if r["status"]=="限额" else e(r["status"])}</td></tr>' for r in rows)
        parts.append(f'<h2>{idx} 场外基金</h2><p class="sum">可买 {len(buyable)} 只 / 共 {len(rows)} 只；代销口径单日合计约 <b>{total:g} 元</b>（A/C 是否合并计算以公告为准）</p>'
                     f'<table><tr><th>基金</th><th>今日限额</th></tr>{trs}</table>')
        ets = sorted([x for x in d["etf"][idx] if x.get("premium") is not None], key=lambda x: x["premium"])
        trs = "".join(f'<tr class="{ "ok" if x["premium"]<3 else "lim" if x["premium"]<8 else "no"}"><td><b>{e(x["code"])}</b><br><small>{e(x["name"])}</small></td>'
                      f'<td><b>{x["premium"]:.2f}%</b></td><td>{x["price"]:.3f}<br><small>{x["chg_pct"]:+.2f}%</small></td><td>{x["scale_yi"] or "-"}亿<br><small>成交{x["amount_wan"]/1e4:.2f}亿</small></td></tr>' for x in ets)
        parts.append(f'<h2>{idx} 场内 ETF 溢价</h2><table><tr><th>ETF</th><th>溢价率</th><th>现价</th><th>场内规模</th></tr>{trs}</table>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>纳指100/标普500 QDII 额度与溢价日报</title><style>
body{{font-family:-apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif;margin:0;padding:12px;background:#f6f6f3;color:#222;max-width:720px;margin:auto}}
h1{{font-size:20px;margin:8px 0}} h2{{font-size:17px;margin:22px 0 6px;border-left:4px solid #F3CB42;padding-left:8px}}
.upd{{color:#666;font-size:13px}} .sum{{font-size:13px;color:#444;margin:4px 0 8px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;font-size:14px}}
th{{background:#333;color:#fff;font-weight:500;padding:6px;text-align:left}} td:nth-child(2),th:nth-child(2){{white-space:nowrap}} td{{padding:7px 6px;border-bottom:1px solid #eee;vertical-align:top}}
tr.ok td:nth-child(2){{color:#0a7d32;font-weight:600}} tr.lim td:nth-child(2){{color:#b26b00;font-weight:600}} tr.no td{{color:#999}}
small{{color:#777}} .dn{{color:#0a5fb4}} .note{{font-size:12px;color:#777;margin-top:20px;line-height:1.6}}
</style></head><body><h1>纳指100 / 标普500：今天能买多少</h1>
<div class="upd">更新时间：{d["updated"]}（北京时间）· 每个交易日自动更新</div>
{"".join(parts)}
<div class="note">数据：场外申购状态取自天天基金（代销口径，基金公司直销限额可能更高，蓝字为公告静态值）；场内价格/IOPV/溢价取自腾讯行情（溢价=现价/IOPV-1，IOPV 由管理人按前一晚美股收盘与汇率估算，盘中会有偏差）。溢价颜色：绿&lt;3%，橙 3–8%，灰≥8%。
同一基金多平台申购合并计算，不能叠加。仅为公开数据整理，不构成投资建议。</div></body></html>'''

if __name__ == "__main__":
    main()
