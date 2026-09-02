# Frontend Code Safety & Syntax Verification Invariant

## Règle Obligatoire
Avant d'annoncer la fin d'une tâche, de livrer du code à l'utilisateur ou d'effectuer un commit modifiant du code HTML avec JavaScript inline (`templates/*.html`), des fichiers `.js` ou `.py` :

1. **Vérification de Syntaxe Obligatoire** :
   L'assistant DOIT impérativement exécuter une vérification automatique de la syntaxe :
   - Pour les templates HTML avec JavaScript inline : exécuter `python .agents/skills/frontend-syntax-guard/scripts/validate_html_js.py <fichier.html>` ou `node --check`.
   - Pour la suite de tests globale : exécuter `python -m unittest discover -s tests`.
2. **Tolérance Zéro aux Régressions Syntaxiques** :
   Aucun changement ne doit être validé ou rapporté comme terminé si une erreur de syntaxe (`SyntaxError`), un token inattendu (accolade `}`, parenthèse, virgule) est détecté.
3. **Validation de l'Intégrité des Éléments Clés** :
   S'assurer que les fonctions JavaScript vitales (menu, onglets `switchTab`, gestionnaires de formulaires, arbitrages et modaux) restent intactes et exécutables sans crash de parser.
