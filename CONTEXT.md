# Trading-bot — Contexte projet (état réel, vérifié)

**Maj : 2026-06-15** · Espace de travail : `C:\Users\everd\Trading-bot-dev`
(clone de `github.com/everdalsi/Trading-bot`, branche `main-revert-4` = celle déployée sur Render et qui tourne en ce moment).

## Ce que c'est
Bot de trading crypto **paper-trading** (Binance testnet pour les ordres), multi-stratégies,
avec système d'apprentissage. Tourne en continu, notifie sur Telegram (@Tradbot).
Capital simulé initial : 1000 $.

## Architecture
- `bot.py` (6090 lignes) — monolithe : boucle, stratégies, ouverture/fermeture, Telegram, apprentissage
- `ai_engine.py` — appels LLM (Groq) · `indicators.py` — RSI/MACD/EMA
- `execution_engine.py`, `data_handler.py`, `websocket_manager.py` — exécution / données / flux
- `knowledge_base.py` — RAG sur PDFs de trading (Wyckoff, VSA…)
- `memory.py` — mémoire d'apprentissage (lessons, scores, blacklist)
- `sim_portfolio_v*.json` — états de portefeuille simulé

## Faits vérifiés (important)
- **Prix = RÉELS.** Source = API Binance **mainnet** publique (`api.binance.com`, L302/372/5415).
  Les ordres sont en **testnet** → aucun argent réel.
- **Code de sortie correct.** `close_trade` (L1492) : compta juste (`cash += montant + pnl`),
  enregistre le trade, `learn_from_trade`, met à jour wins/losses + scores + blacklist.
- **3 moniteurs de sortie** : `monitor_positions` (normal), `monitor_micro_positions` (MICRO),
  `_monitor_meme_positions` (MEME). Seuils : SL = 1,5 %, TP = 6 %, + trailing.
- **Garde-fou** : `LIVE_MODE=False`, commentaire « True seulement si winrate ≥ 92 % validé ».

## LE vrai problème : pas de mesure de performance fiable
- Capture Telegram récente : `WR 0,0 % (0 trades fermés)`, **15 positions ouvertes**,
  « +16,8 % » = **PnL latent (non réalisé)**. Un gain latent n'est pas un gain.
- Historique (`sim_v7`, 1er avril) : 47 wins / 421 losses ≈ 10 % winrate — **mais faussé par des resets**.
- Conséquence : **impossible aujourd'hui de dire si le bot a un edge réel.**

## Objectif & règles
But : laisser tourner en continu (testnet) pour accumuler de **vrais trades fermés** et apprendre.
Règles pour que ça ait du sens :
1. **NE PLUS RESET** — chaque reset détruit la mesure de performance.
2. **Mesurer la perf RÉALISÉE** : equity réalisée, nb trades fermés, winrate, PnL **net de frais**, fenêtre datée.
3. Laisser tourner jusqu'à un échantillon significatif (≥ ~100 trades fermés).
4. **Rester en TESTNET.** Passage live seulement si winrate réalisé tient sur données propres, net de frais.

## Prochaines actions
- [ ] Mettre en place un suivi de perf réalisée propre (journal des trades fermés horodaté + equity réalisée)
- [ ] Confirmer que les 3 moniteurs de sortie sont bien appelés dans la boucle principale
- [ ] Vérifier la prise en compte des **frais** (sinon perf surévaluée)
- [ ] Stabiliser la branche (`main-revert-4` → `main` propre)
- [ ] Laisser tourner **sans reset** + mesurer
