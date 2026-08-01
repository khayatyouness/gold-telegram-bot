# Gold Analysis → Telegram

Bot automatique qui envoie une **analyse technique du marché de l'OR (XAU/USD)**
sur Telegram, **chaque heure de 7h à 22h (Europe/Paris)**, via **GitHub Actions**.

## Contenu de chaque message
- Prix spot + variation du jour
- Biais technique (🟢 haussier / 🔴 baissier / 🟡 neutre)
- Actualités impactantes (Google News RSS, dernières 24h)
- Indicateurs : RSI(14), MA20/50/200, ATR(14)
- Niveaux clés : résistances, pivot, supports
- Scénarios techniques ACHAT / VENTE avec niveaux d'invalidation
- ⚠️ Disclaimer (analyse éducative, **pas** un conseil d'investissement)

## Fonctionnement
- `gold_report.py` : 100% stdlib Python (aucune dépendance à installer).
  - Prix/OHLC : endpoint public Yahoo Finance (`GC=F`).
  - Actualités : Google News RSS (sans clé API).
  - Envoi : API Telegram Bot.
- `.github/workflows/gold.yml` : planification cron `0 5-21 * * *` (UTC).
  Le script filtre lui-même l'heure locale Europe/Paris (gère été/hiver),
  donc seuls les créneaux 7h–22h Paris envoient un message.

## Configuration (déjà en place)
Secrets du dépôt (Settings → Secrets and variables → Actions) :
- `TELEGRAM_TOKEN` — token du bot BotFather
- `TELEGRAM_CHAT_ID` — id du chat destinataire

## Test manuel
Onglet **Actions** → *Gold Analysis Telegram* → **Run workflow**
(envoie immédiatement, même hors plage horaire).

## Notes
- GitHub désactive les workflows planifiés après 60 jours sans activité sur le
  dépôt — pousser un commit de temps en temps suffit à les réactiver.
- Les horaires `schedule` de GitHub Actions peuvent avoir quelques minutes de
  retard en période de forte charge.
