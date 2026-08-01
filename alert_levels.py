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
# Ichimoku multi-timeframes : liste "tf:range" separee par des virgules.
ICHI_TFS = os.environ.get("ICHI_TFS", "15m:1mo,60m:3mo")
ICHI_WINDOW_MIN = float(os.environ.get("ICHI_WINDOW_MIN", "16"))  # anti-doublon
# Order Blocks (Smart Money) : timeframes surveilles.
OB_TFS = [x.strip() for x in os.environ.get("OB_TFS", "15m,30m,60m,4h").split(",") if x.strip()]
OB_LOOKBACK = int(os.environ.get("OB_LOOKBACK", "120"))   # bougies scannees
OB_DISPL = float(os.environ.get("OB_DISPL", "1.0"))       # force de l'impulsion (x range moyen)


def parse_ichi_tfs():
    out = []
    for part in ICHI_TFS.split(","):
        part = part.strip()
        if not part:
            continue
        tf, rng = part.split(":")
        out.append((tf.strip(), rng.strip()))
    return out


def tf_seconds(tf):
    num, unit = int(tf[:-1]), tf[-1]
    return num * {"m": 60, "h": 3600, "d": 86400}[unit]


def fetch_series(interval, rng):
    """Retourne [(ts, close)] (bougies avec close non nul)."""
    url = f"{BASE}?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": gr.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    c = res["indicators"]["quote"][0]["close"]
    return [(ts[i], c[i]) for i in range(len(c)) if c[i] is not None]


def fetch_ohlc_series(interval, rng):
    """Retourne [(ts, high, low, close)] (bougies completes en OHLC)."""
    url = f"{BASE}?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": gr.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    h, l, c = q["high"], q["low"], q["close"]
    return [(ts[i], h[i], l[i], c[i]) for i in range(len(c))
            if None not in (h[i], l[i], c[i])]


def fetch_ohlc4(interval, rng):
    """Retourne [(ts, open, high, low, close)]."""
    url = f"{BASE}?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": gr.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    o, h, l, c = q["open"], q["high"], q["low"], q["close"]
    return [(ts[i], o[i], h[i], l[i], c[i]) for i in range(len(c))
            if None not in (o[i], h[i], l[i], c[i])]


def resample(rows, bucket_sec):
    """Agrege des bougies OHLC (ts,o,h,l,c) en bougies alignees sur bucket_sec."""
    buckets, order = {}, []
    for ts, o, h, l, c in rows:
        b = ts - (ts % bucket_sec)
        if b not in buckets:
            buckets[b] = [o, h, l, c]; order.append(b)
        else:
            agg = buckets[b]
            agg[1] = max(agg[1], h); agg[2] = min(agg[2], l); agg[3] = c
    return [(b, buckets[b][0], buckets[b][1], buckets[b][2], buckets[b][3]) for b in order]


def get_ohlc_tf(tf):
    """Bougies (ts,o,h,l,c) pour un timeframe. 4h reconstruit depuis le 1h."""
    if tf == "4h":
        return resample(fetch_ohlc4("60m", "6mo"), 4 * 3600)
    yf_tf = "60m" if tf in ("1h", "60m") else tf
    rng = {"15m": "1mo", "30m": "1mo", "60m": "3mo", "1h": "3mo"}.get(tf, "1mo")
    return fetch_ohlc4(yf_tf, rng)


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


# ---------- Ichimoku ----------
def ichimoku(rows):
    """rows: [(ts, high, low, close)]. Retourne tenkan, kijun, spanA, spanB
    (valeurs brutes non decalees, alignees sur l'index)."""
    n = len(rows)
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    tenkan = [None] * n; kijun = [None] * n
    spanA = [None] * n; spanB = [None] * n

    def mid(i, p):
        if i - p + 1 < 0:
            return None
        return (max(highs[i - p + 1:i + 1]) + min(lows[i - p + 1:i + 1])) / 2

    for i in range(n):
        tenkan[i] = mid(i, 9)
        kijun[i] = mid(i, 26)
        spanB[i] = mid(i, 52)
        if tenkan[i] is not None and kijun[i] is not None:
            spanA[i] = (tenkan[i] + kijun[i]) / 2
    return tenkan, kijun, spanA, spanB


def detect_ichimoku(rows, tf):
    n = len(rows)
    if n < 80:
        return []
    tenkan, kijun, spanA, spanB = ichimoku(rows)
    now = time.time()
    tfsec = tf_seconds(tf)

    # derniere bougie COMPLETE (close time = ts + tfsec deja passe)
    last = None
    for i in range(n - 1, -1, -1):
        if rows[i][0] + tfsec <= now:
            last = i
            break
    if last is None or last < 27:
        return []
    comp = rows[last][0] + tfsec
    if not (now - ICHI_WINDOW_MIN * 60 < comp <= now):
        return []   # rien de fraichement cloture -> pas d'alerte (anti-doublon)

    i, j = last, last - 1
    ci, cj = rows[i][3], rows[j][3]
    events = []

    # Tenkan / Kijun cross
    if None not in (tenkan[i], kijun[i], tenkan[j], kijun[j]):
        if tenkan[j] <= kijun[j] and tenkan[i] > kijun[i]:
            events.append(("Tenkan/Kijun : croisement HAUSSIER \U0001F7E2", "achat"))
        elif tenkan[j] >= kijun[j] and tenkan[i] < kijun[i]:
            events.append(("Tenkan/Kijun : croisement BAISSIER \U0001F534", "vente"))

    # Kumo breakout : nuage "actuel" = spans calcules 26 periodes avant
    ai, bi = spanA[i - 26], spanB[i - 26]
    aj, bj = spanA[j - 26], spanB[j - 26]
    if None not in (ai, bi, aj, bj):
        top_i, bot_i = max(ai, bi), min(ai, bi)
        top_j, bot_j = max(aj, bj), min(aj, bj)
        if cj <= top_j and ci > top_i:
            events.append(("Kumo : sortie du nuage par le HAUT \U0001F7E2", "achat"))
        elif cj >= bot_j and ci < bot_i:
            events.append(("Kumo : sortie du nuage par le BAS \U0001F534", "vente"))
    return events


# ---------- Order Blocks (Smart Money) ----------
def detect_order_blocks(rows, tf):
    """OB = derniere bougie opposee avant une impulsion qui casse la structure.
    Alerte quand le prix ENTRE (mitige) pour la 1re fois dans un OB non teste."""
    n = len(rows)
    if n < 10:
        return []
    tfsec = tf_seconds(tf)
    now = time.time()
    L = None
    for i in range(n - 1, -1, -1):
        if rows[i][0] + tfsec <= now:
            L = i
            break
    if L is None or L < 5:
        return []
    comp = rows[L][0] + tfsec
    if not (now - ICHI_WINDOW_MIN * 60 < comp <= now):
        return []   # la bougie de mitigation doit venir de cloturer -> anti-doublon

    op = lambda k: rows[k][1]
    hi = lambda k: rows[k][2]
    lo = lambda k: rows[k][3]
    cl = lambda k: rows[k][4]
    overlap = lambda k, zl, zh: lo(k) <= zh and hi(k) >= zl

    start = max(1, L - OB_LOOKBACK)
    rngs = [hi(k) - lo(k) for k in range(start, L + 1)]
    avg = sum(rngs) / len(rngs) if rngs else 0
    if avg <= 0:
        return []

    events = []
    for i in range(start, L - 1):
        jmax = min(i + 3, L - 1)
        if i + 1 > jmax:
            continue
        # OB haussier (zone de demande) : bougie baissiere puis impulsion up
        if cl(i) < op(i) and cl(i + 1) > op(i + 1):
            mh = max(hi(k) for k in range(i + 1, jmax + 1))
            if mh > hi(i) and (mh - cl(i)) > OB_DISPL * avg:
                zl, zh = lo(i), hi(i)
                if not any(overlap(k, zl, zh) for k in range(i + 1, L)) and overlap(L, zl, zh):
                    events.append((tf, "haussier (demande) \U0001F7E2", "achat", zl, zh))
                    continue
        # OB baissier (zone d'offre) : bougie haussiere puis impulsion down
        if cl(i) > op(i) and cl(i + 1) < op(i + 1):
            ml = min(lo(k) for k in range(i + 1, jmax + 1))
            if ml < lo(i) and (cl(i) - ml) > OB_DISPL * avg:
                zl, zh = lo(i), hi(i)
                if not any(overlap(k, zl, zh) for k in range(i + 1, L)) and overlap(L, zl, zh):
                    events.append((tf, "baissier (offre) \U0001F534", "vente", zl, zh))

    price = cl(L)
    events.sort(key=lambda e: min(abs(price - e[3]), abs(price - e[4])))
    return events[:2]


# ---------- message ----------
def build_alert(level_events, rsi_events, ichi_events, ob_events, price):
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
    if ichi_events:
        L.append("<b>☁️ Ichimoku</b>")
        for tf, label, biais in ichi_events:
            L.append(f"• [{tf}] {label} — biais technique {biais}")
        L.append("")
    if ob_events:
        L.append("<b>\U0001F4E6 Order Block (entree du prix)</b>")
        for tf, label, biais, zl, zh in ob_events:
            L.append(f"• [{tf}] OB {label} {f(zl)}–{f(zh)} — biais {biais}")
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
        ichi_events = []
        for tf, rng in parse_ichi_tfs():
            rows = fetch_ohlc_series(tf, rng)
            for label, biais in detect_ichimoku(rows, tf):
                ichi_events.append((tf, label, biais))
        ob_events = []
        for tf in OB_TFS:
            ob_events += detect_order_blocks(get_ohlc_tf(tf), tf)
    except Exception as e:
        print(f"ERREUR data: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    if len(candles5) < 2:
        print("Marche ferme / pas de bougies. Rien.")
        return

    level_events = detect_levels(candles5, key_levels(a))
    rsi_events = detect_rsi(candles_rsi)

    if not level_events and not rsi_events and not ichi_events and not ob_events:
        print("Aucun evenement (niveau/RSI/Ichimoku/OB) sur la periode. Rien envoye.")
        return

    msg = build_alert(level_events, rsi_events, ichi_events, ob_events, candles5[-1][1])
    if args.dry_run:
        print(msg)
    else:
        gr.tg_send(msg)


if __name__ == "__main__":
    main()
