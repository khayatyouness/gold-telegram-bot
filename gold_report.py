#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold (XAU/USD ~ GC=F) : analyse technique + actualites -> message Telegram.
100% STDLIB (urllib + math + xml). Aucun pip requis.
- Prix / OHLC : endpoint public Yahoo chart JSON.
- Actualites : Google News RSS (sans cle API).
- Envoi : Telegram Bot API (parse_mode HTML).

Variables d'environnement requises :
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
Options :
    --force     : envoie meme hors de la plage horaire (pour tests manuels)
    --dry-run   : affiche le message sans l'envoyer

Plage horaire : envoie uniquement si l'heure locale Europe/Paris est entre
7h et 22h inclus (gere automatiquement l'heure d'ete/hiver).
"""
import os, sys, json, argparse, urllib.request, urllib.parse
import datetime as dt
from xml.etree import ElementTree as ET

# Actif configurable (defaut : OR). Surcharge via variables d'environnement.
SYMBOL = os.environ.get("SYMBOL", "GC=F")
ASSET_NAME = os.environ.get("ASSET_NAME", "GOLD (XAU/USD)")
ASSET_EMOJI = os.environ.get("ASSET_EMOJI", "\U0001F947")   # 🥇
ASSET_SHORT = os.environ.get("ASSET_SHORT", "GOLD")
NEWS_QUERY = os.environ.get("NEWS_QUERY", "gold+price+XAUUSD+fed+dollar")

CHART_URL = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
             f"{SYMBOL}?range=1y&interval=1d")
NEWS_URL = ("https://news.google.com/rss/search?"
            f"q={NEWS_QUERY}+when:1d&hl=en-US&gl=US&ceid=US:en")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
HOUR_START, HOUR_END = 7, 22           # Europe/Paris
TZ_NAME = "Europe/Paris"
DISCLAIMER = (
    "⚠️ <i>Tahlil tiqni automatique li gharad ta3limi/i3lami. "
    "Machi conseil d'investissement. Al qarar dyalek w 3la mas'ouliytek. "
    "As-sou9 fih mukhatara.</i>"
)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------- data ----------
def fetch_ohlc():
    data = json.loads(_get(CHART_URL))
    res = data["chart"]["result"][0]
    meta = res["meta"]
    q = res["indicators"]["quote"][0]
    o, h, l, c = q["open"], q["high"], q["low"], q["close"]
    rows = [(o[i], h[i], l[i], c[i]) for i in range(len(c))
            if None not in (o[i], h[i], l[i], c[i])]
    return rows, meta.get("regularMarketPrice")


def fetch_news(max_items=3):
    """Retourne une liste de titres d'actualites recentes sur l'or."""
    try:
        raw = _get(NEWS_URL, timeout=20)
        root = ET.fromstring(raw)
        titles = []
        for item in root.iter("item"):
            t = item.findtext("title") or ""
            t = t.strip().replace("<", "(").replace(">", ")")
            if t:
                titles.append(t)
            if len(titles) >= max_items:
                break
        return titles
    except Exception:
        return []


# ---------- indicateurs (pure python) ----------
def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(rows, period=14):
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i][1], rows[i][2], rows[i - 1][3]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    a = sum(trs[:period]) / period
    for t in trs[period:]:
        a = (a * (period - 1) + t) / period
    return a


def pivots(ph, pl, pc):
    pp = (ph + pl + pc) / 3
    return dict(PP=pp, R1=2 * pp - pl, S1=2 * pp - ph,
                R2=pp + (ph - pl), S2=pp - (ph - pl),
                R3=ph + 2 * (pp - pl), S3=pl - 2 * (ph - pp))


def swing_levels(rows, window=5, lookback=60):
    d = rows[-lookback:]
    highs, lows = [], []
    for i in range(window, len(d) - window):
        seg_h = [d[j][1] for j in range(i - window, i + window + 1)]
        seg_l = [d[j][2] for j in range(i - window, i + window + 1)]
        if d[i][1] == max(seg_h):
            highs.append(round(d[i][1], 1))
        if d[i][2] == min(seg_l):
            lows.append(round(d[i][2], 1))
    return sorted(set(highs)), sorted(set(lows))


def analyse():
    rows, live = fetch_ohlc()
    if len(rows) < 60:
        raise RuntimeError("Pas assez de donnees OHLC.")
    closes = [r[3] for r in rows]
    price = float(live) if live else closes[-1]
    prev_close = closes[-2]
    ma20, ma50, ma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    rsi14 = rsi(closes)
    atr14 = atr(rows)
    chg_pct = (price / prev_close - 1) * 100
    ph, pl, pc = rows[-2][1], rows[-2][2], rows[-2][3]
    piv = pivots(ph, pl, pc)
    res_sw, sup_sw = swing_levels(rows)
    res_pool = set(res_sw + [round(piv["R1"], 1), round(piv["R2"], 1), round(piv["R3"], 1)])
    sup_pool = set(sup_sw + [round(piv["S1"], 1), round(piv["S2"], 1), round(piv["S3"], 1)])
    res = sorted([x for x in res_pool if x > price])[:3]
    sup = sorted([x for x in sup_pool if x < price], reverse=True)[:3]
    score = 0
    for ma in (ma20, ma50, ma200):
        if ma:
            score += 1 if price > ma else -1
    if ma20 and ma50:
        score += 1 if ma20 > ma50 else -1
    if rsi14 is not None:
        score += 1 if rsi14 > 55 else -1 if rsi14 < 45 else 0
    if score >= 3:
        bias, emoji = "HAUSSIER (biais achat)", "\U0001F7E2"
    elif score <= -3:
        bias, emoji = "BAISSIER (biais vente)", "\U0001F534"
    else:
        bias, emoji = "NEUTRE / range", "\U0001F7E1"
    rsi_note = ""
    if rsi14 is not None:
        rsi_note = " (surachat)" if rsi14 >= 70 else " (survente)" if rsi14 <= 30 else ""
    return dict(price=price, chg_pct=chg_pct, ma20=ma20, ma50=ma50, ma200=ma200,
                rsi14=rsi14, rsi_note=rsi_note, atr14=atr14, piv=piv,
                res=res, sup=sup, bias=bias, emoji=emoji)


# ---------- message ----------
def build_message(a, news):
    now = dt.datetime.now(dt.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    f = lambda x: ("{:,.1f}".format(x)).replace(",", " ") if x is not None else "-"
    L = [f"<b>{ASSET_EMOJI} {ASSET_NAME} — {now}</b>"]
    sign = "+" if a["chg_pct"] >= 0 else ""
    L.append(f"Prix : <b>{f(a['price'])}$</b> ({sign}{a['chg_pct']:.2f}% j)")
    L.append(f"{a['emoji']} Biais technique : <b>{a['bias']}</b>")
    L.append("")
    if news:
        L.append("<b>\U0001F4F0 Actualites (24h)</b>")
        for t in news:
            L.append(f"• {t}")
        L.append("")
    L.append("<b>\U0001F4CA Indicateurs</b>")
    L.append(f"• RSI(14) : {a['rsi14']:.1f}{a['rsi_note']}" if a["rsi14"] is not None else "• RSI(14) : -")
    L.append(f"• MA20 : {f(a['ma20'])} | MA50 : {f(a['ma50'])} | MA200 : {f(a['ma200'])}")
    L.append(f"• ATR(14) : {f(a['atr14'])}")
    L.append("")
    L.append("<b>\U0001F3AF Niveaux cles</b>")
    if a["res"]:
        L.append("Resistances : " + " | ".join(f(x) for x in a["res"]))
    L.append(f"Pivot : {f(a['piv']['PP'])}")
    if a["sup"]:
        L.append("Supports : " + " | ".join(f(x) for x in a["sup"]))
    L.append("")
    L.append("<b>\U0001F4A1 Scenarios techniques</b>")
    if a["res"] and a["sup"]:
        r1, s1 = a["res"][0], a["sup"][0]
        L.append(f"• Zone ACHAT tech. : rebond &gt; {f(s1)} → cible {f(r1)} (invalidation &lt; {f(a['sup'][-1])})")
        L.append(f"• Zone VENTE tech. : rejet &lt; {f(r1)} → cible {f(s1)} (invalidation &gt; {f(a['res'][-1])})")
    L.append("")
    L.append(DISCLAIMER)
    return "\n".join(L)


def tg_send(text):
    token = os.environ.get("TELEGRAM_TOKEN"); chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("ERREUR: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants", file=sys.stderr)
        return False
    body = urllib.parse.urlencode({
        "chat_id": chat, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        print("OK: message envoye.")
        return True
    except Exception as e:
        detail = getattr(e, "read", lambda: b"")()
        print(f"ERREUR Telegram: {e} {detail}", file=sys.stderr)
        return False


def paris_hour():
    """Heure locale Europe/Paris (gere ete/hiver), ou None si indisponible."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(TZ_NAME)).hour
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.force and not args.dry_run:
        h = paris_hour()
        if h is not None and not (HOUR_START <= h <= HOUR_END):
            print(f"Hors plage horaire (Paris {h}h). Rien envoye.")
            return

    try:
        a = analyse()
        news = fetch_news()
        msg = build_message(a, news)
    except Exception as e:
        err = (f"⚠️ {ASSET_SHORT} bot : erreur analyse ({type(e).__name__}: {e}). "
               "Prochaine tentative a la prochaine heure.")
        if args.dry_run:
            print("ERREUR:", e)
        else:
            tg_send(err)
        sys.exit(1)

    if args.dry_run:
        print(msg)
    else:
        ok = tg_send(msg)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
