#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alerte de cassure de niveau cle pour l'OR (XAU/USD).
Verifie les bougies intraday 5 min de Yahoo et envoie une alerte Telegram
UNIQUEMENT quand le prix vient de franchir un niveau cle (resistance,
support ou pivot) dans les dernieres ~INTERVAL minutes.

Anti-spam : n'examine que les croisements survenus depuis (maintenant - INTERVAL),
donc chaque execution couvre une tranche de temps disjointe -> pas de doublon.

Reutilise les fonctions de gold_report.py (calcul des niveaux + envoi Telegram).
Env : TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
Options : --force (ignore la plage horaire), --dry-run
"""
import os, sys, json, time, argparse, urllib.request
import gold_report as gr

INTRADAY_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
                "GC=F?range=1d&interval=5m")
INTERVAL_MIN = int(os.environ.get("ALERT_INTERVAL_MIN", "15"))
# tolerance : on considere aussi qu'un niveau est "atteint" si une bougie
# le touche a moins de TOUCH_EPS points sans forcement le traverser.
TOUCH_EPS = float(os.environ.get("ALERT_TOUCH_EPS", "1.5"))


def fetch_intraday():
    req = urllib.request.Request(INTRADAY_URL, headers={"User-Agent": gr.UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    c = res["indicators"]["quote"][0]["close"]
    return [(ts[i], c[i]) for i in range(len(c)) if c[i] is not None]


def key_levels(a):
    lv = []
    for x in a["res"]:
        lv.append(("Resistance", x))
    for x in a["sup"]:
        lv.append(("Support", x))
    lv.append(("Pivot", round(a["piv"]["PP"], 1)))
    # dedoublonne les niveaux identiques
    seen, out = set(), []
    for name, val in lv:
        if val not in seen:
            seen.add(val); out.append((name, val))
    return out


def detect(candles, levels):
    cutoff = time.time() - INTERVAL_MIN * 60
    events = {}   # cle = valeur du niveau -> (name, val, direction, price)
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
                events[val] = (name, val, direction, kind, c1)
    return list(events.values())


def build_alert(events, price):
    f = lambda x: ("{:,.1f}".format(x)).replace(",", " ")
    L = ["<b>\U0001F6A8 ALERTE GOLD — niveau cle</b>"]
    L.append(f"Prix actuel : <b>{f(price)}$</b>")
    L.append("")
    for name, val, direction, kind, _ in events:
        verb = "a franchi" if kind == "cassure" else "teste"
        L.append(f"• {name} <b>{f(val)}</b> : {verb} ({direction})")
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
        candles = fetch_intraday()
    except Exception as e:
        print(f"ERREUR data: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    if len(candles) < 2:
        print("Marche ferme / pas de bougies intraday. Rien.")
        return

    levels = key_levels(a)
    events = detect(candles, levels)

    if not events:
        print("Aucun niveau cle atteint sur la periode. Rien envoye.")
        return

    msg = build_alert(events, candles[-1][1])
    if args.dry_run:
        print(msg)
    else:
        gr.tg_send(msg)


if __name__ == "__main__":
    main()
