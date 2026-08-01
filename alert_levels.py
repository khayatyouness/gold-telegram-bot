#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alertes intraday pour l'OR (XAU/USD), envoyees sur Telegram :
  1) Cassure / test d'un niveau cle (resistance, support, pivot).
  2) RSI entrant en zone de surachat (>= seuil) ou survente (<= seuil).

Anti-spam : on n'examine que les evenements survenus depuis
(maintenant - INTERVAL) -> chaque execution couvre une tranche de temps
disjointe, donc chaque cassure / franchissement RSI n'alerte qu'une fois.

Reutilise gold_report.py (calcul des niveaux + envoi Telegram).
Env : TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
Options : --force (ignore la plage horaire), --dry-run
"""
import os, sys, json, time, argparse, urllib.request
import gold_report as gr

BASE = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
INTERVAL_MIN = int(os.environ.get("ALERT_INTERVAL_MIN", "15"))
TOUCH_EPS = float(os.environ.get("ALERT_TOUCH_EPS", "1.5"))
RSI_TF = os.environ.get("RSI_TF", "15m")          # timeframe du RSI intraday
RSI_OB = float(os.environ.get("RSI_OB", "70"))    # seuil surachat
RSI_OS = float(os.environ.get("RSI_OS", "30"))    # seuil survente


def fetch_series(interval, rng):
    url = f"{BASE}?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": gr.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    c = res["indicators"]["quote"][0]["close"]
    return [(ts[i], c[i]) for i in range(len(c)) if c[i] is not None]


def rsi_series(closes, period=14):
    """RSI de Wilder, aligne sur closes (None tant qu'il n'y a pas assez de data)."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


# ---------- niveaux ----------
def key_levels(a):
    lv = [("Resistance", x) for x in a["res"]]
    lv += [("Support", x) for x in a["sup"]]
    lv.append(("Pivot", round(a["piv"]["PP"], 1)))
    seen, out = set(), []
    for name, val in lv:
        if val not in seen:
            seen.add(val); out.append((name, val))
    return out


def detect_levels(candles, levels):
    cutoff = time.time() - INTERVAL_MIN * 60
    events = {}
    for i in range(1, len(candles)):
        (t0, c0), (t1, c1) = candles[i - 1], candles[i]
        if t1 < cutoff:
            continue
        lo, hi = min(c0, c1), max(c0, c1)
        for name, val in levels:
            crossed = lo <= val <= hi and c0 != c1
            touched = abs(c1 - val) <= TOUCH_EPS
            if crossed or touched:
                direction = "haussiere \U0001F7E2" if c1 >= c0 else "baissiere \U0001F534"
                kind = "cassure" if crossed else "test"
                events[val] = (name, val, direction, kind)
    return list(events.values())


# ---------- RSI ----------
def detect_rsi(candles):
    cutoff = time.time() - INTERVAL_MIN * 60
    closes = [c for _, c in candles]
    rs = rsi_series(closes)
    events = []
    for i in range(1, len(candles)):
        if candles[i][0] < cutoff:
            continue
        r0, r1 = rs[i - 1], rs[i]
        if r0 is None or r1 is None:
            continue
        if r0 < RSI_OB <= r1:
            events.append(("surachat \U0001F534", r1, "vente"))   # entree surachat
        elif r0 > RSI_OS >= r1:
            events.append(("survente \U0001F7E2", r1, "achat"))   # entree survente
    return events[-1:] if events else []   # au plus 1 (le plus recent)


# ---------- message ----------
def build_alert(level_events, rsi_events, price):
    f = lambda x: ("{:,.1f}".format(x)).replace(",", " ")
    L = ["<b>\U0001F6A8 ALERTE GOLD</b>"]
    L.append(f"Prix actuel : <b>{f(price)}$</b>")
    L.append("")
    if level_events:
        L.append("<b>\U0001F3AF Niveau cle</b>")
        for name, val, direction, kind in level_events:
            verb = "a franchi" if kind == "cassure" else "teste"
            L.append(f"• {name} <b>{f(val)}</b> : {verb} ({direction})")
        L.append("")
    if rsi_events:
        L.append(f"<b>\U0001F4C8 RSI {RSI_TF}</b>")
        for label, val, biais in rsi_events:
            L.append(f"• Zone <b>{label}</b> (RSI {val:.1f}) — biais technique {biais}")
        L.append("")
    L.append(gr.DISCLAIMER)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.force and not args.dry_run:
        h = gr.paris_hour()
        if h is not None and not (gr.HOUR_START <= h <= gr.HOUR_END):
            print(f"Hors plage horaire (Paris {h}h). Rien.")
            return

    try:
        a = gr.analyse()
        candles5 = fetch_series("5m", "1d")
        candles_rsi = fetch_series(RSI_TF, "5d")
    except Exception as e:
        print(f"ERREUR data: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    if len(candles5) < 2:
        print("Marche ferme / pas de bougies. Rien.")
        return

    level_events = detect_levels(candles5, key_levels(a))
    rsi_events = detect_rsi(candles_rsi)

    if not level_events and not rsi_events:
        print("Aucun evenement (niveau/RSI) sur la periode. Rien envoye.")
        return

    msg = build_alert(level_events, rsi_events, candles5[-1][1])
    if args.dry_run:
        print(msg)
    else:
        gr.tg_send(msg)


if __name__ == "__main__":
    main()
