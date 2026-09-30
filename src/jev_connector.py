import os
import requests
import json

API_KEY = os.getenv("TYPESAFE_JEV_API_KEY", "")


def ask_jev_confirmation(symbol, macro_state, stock_state):
    """
    Interroge l'API JEV de TypeSafe pour confirmer un signal d'achat.
    Retourne une chaîne formatée résumant l'avis de l'IA, ou None en cas d'erreur.
    """
    url = "https://api.typesafe.ai/v1/systemone"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    payload = {
        "model": "jev-latest",
        "state": {
            "symbol": symbol,
            "macro_environment": macro_state,
            "stock_technical_indicators": stock_state,
        },
        "questions": {
            "market_regime": {
                "type": "choice",
                "instructions": "En analysant les indicateurs macroéconomiques fournis (VIX, DXY, SPY_trend, regime macro), quel est le régime de marché actuel ?",
                "criteria": {
                    "tendance": "Le marché est dans une tendance claire, favorable à la prise de position (Risk-On).",
                    "range": "Le marché consolide sans direction claire.",
                    "transition": "Le marché montre des signes de retournement ou d'incertitude élevée (Risk-Off).",
                },
            },
            "setup_strength": {
                "type": "score",
                "instructions": "Quelle est la force de ce setup d'achat (mean-reversion) au vu de la baisse (drop_pct), du RSI et de l'indicateur M20/Bollinger ?",
                "criteria": ["absent", "faible", "moyen", "fort"],
            },
            "swing_context": {
                "type": "noul",
                "instructions": "Le contexte actuel de cette action combiné au régime macroéconomique est-il très favorable pour initier un trade swing à la hausse avec un fort potentiel de rebond ?",
            },
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            answers = data.get("answers", {})

            # Parsing Choice
            regime_ans = answers.get("market_regime", {})
            regime = regime_ans.get("choice", "N/A")
            regime_conf = regime_ans.get("confidence", 0) * 100

            # Parsing Score
            setup_ans = answers.get("setup_strength", {})
            setup_legend = setup_ans.get("legend", {})
            # Find the max probability to get the chosen label
            probs = setup_ans.get("probabilities", {})
            best_idx = max(probs, key=probs.get) if probs else "0"
            setup_label = setup_legend.get(best_idx, "N/A")
            setup_score = setup_ans.get("score", 0)

            # Parsing Noul
            swing_ans = answers.get("swing_context", {})
            swing_prob = swing_ans.get("noul", 0) * 100

            # Format output as dictionary
            return {
                "regime": regime.capitalize(),
                "regime_conf": round(regime_conf),
                "setup_label": setup_label.capitalize(),
                "setup_score": round(setup_score, 1),
                "swing_prob": round(swing_prob),
            }
        else:
            print(f"[JEV API Error] {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"[JEV API Exception] {e}")
        return None
