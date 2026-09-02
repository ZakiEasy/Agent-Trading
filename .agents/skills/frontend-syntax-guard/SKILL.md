---
name: frontend-syntax-guard
description: Valide automatiquement la syntaxe JavaScript inline des fichiers HTML et des scripts frontaux pour éliminer les blocages d'interface, menus/boutons inactifs et erreurs de syntaxe (accolades, parenthèses orphelines). À activer systématiquement après chaque retouche de template HTML ou de code JavaScript.
---

# Frontend Syntax Guard

Ce skill fournit un harnais de sécurité automatique pour vérifier la syntaxe et l'intégrité de l'interface utilisateur.

## Pourquoi ce skill est vital
Dans les applications web monolithiques avec de grands templates HTML (comme Jinja2 ou HTML brut > 10 000 lignes), une simple accolade fermante en trop (`}`) ou manquante dans un bloc `<script>` provoque un `SyntaxError` bloquant. Ce crash silencieux désactive instantanément tous les écouteurs d'événements :
- Menus et onglets inaccessibles
- Modaux qui ne s'ouvrent plus
- Boutons d'action totalement inertes

## Workflow d'Utilisation

Après **chaque** modification d'un template HTML contenant des balises `<script>` ou d'un fichier JS :

1. **Lancer le script de validation embarqué** :
   ```bash
   python .agents/skills/frontend-syntax-guard/scripts/validate_html_js.py templates/index.html
   ```

2. **Interprétation du résultat** :
   - ✅ `VALIDATION RÉUSSIE : 100% syntactiquement valide` -> Vous pouvez commiter et présenter la solution.
   - ❌ `ERREUR SYNTAXE DÉTECTÉE` -> Le script affiche le bloc, le numéro de ligne dans le fichier d'origine et le token incriminé (ex: `SyntaxError: Unexpected token '}'`).
   - Corriger immédiatement l'erreur avant toute annonce à l'utilisateur.

3. **Lancer les tests d'intégrité frontend** :
   ```bash
   python -m unittest tests/test_frontend_integrity.py
   ```
