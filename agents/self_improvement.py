def start_self_improvement_loop(orchestrator):
    """Boucle d'auto-amélioration avec optimisation rate limit Groq - Version stable finale"""
    print("[SELF-IMPROVEMENT] Boucle démarrée avec optimisation rate limit")
    cycle = 0
    last_rate_limit = 0

    while True:
        cycle += 1
        # Utilisation explicite de datetime (importé en haut du fichier)
        now_str = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"[SELF-IMPROVEMENT] Cycle #{cycle} - {now_str}")

        try:
            crew = create_improvement_crew()
            if crew:
                result = crew.kickoff()
                print(f"[SELF-IMPROVEMENT] Cycle terminé - {result}")
                
                # Mise à jour Prometheus (tes métriques originales restent intactes)
                evolution_cycles_total.inc()
                if hasattr(performance_tracker, 'winrate_gauge'):
                    performance_tracker.winrate_gauge.set(performance_tracker.get_winrate() if hasattr(performance_tracker, 'get_winrate') else 20.0)

        except Exception as e:
            err_str = str(e).lower()
            if "rate_limit" in err_str or "ratelimit" in err_str or "429" in err_str:
                wait_seconds = min(60, 15 * (2 ** (cycle % 4)))
                print(f"[RATE LIMIT] Groq limite atteinte → pause {wait_seconds}s")
                time.sleep(wait_seconds)
                last_rate_limit = time.time()
                continue
            else:
                print(f"[SELF-IMPROVEMENT ERROR] {e}")
                evolution_errors_total.inc()

        # Délai adaptatif optimisé (ton code original)
        base_sleep = 40 if (time.time() - last_rate_limit < 600) else 25
        time.sleep(base_sleep + random.uniform(3, 12))
