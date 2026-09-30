# Changelog - Trading Agent

Toutes les modifications importantes apportées au projet seront documentées dans ce fichier.

## [v2.2.0] - 2026-09-09
### Ajouté
- **Monitoring & Audit (Dashboard)** : Nouvel onglet dans l'interface (desktop et mobile) pour vérifier la santé du VPS (CPU, RAM, PostgreSQL) et s'assurer que les limites d'audit (R-Max 1.0%, Exposition 25%) sont respectées.
- **Achats par Valeur (Fractions 1€)** : Le moteur d'exécution envoie désormais des ordres au marché basés sur une valeur d'investissement brute (en Euros) à Trading212, plutôt qu'une quantité d'actions calculée et arrondie. Cela résout le problème des actions chères bloquées.

### Modifié
- **Délai du Sniper** : L'intervalle de scan du robot de trading passe de 5 minutes à **2 minutes** pour plus de réactivité.
- **Rétention des Logs** : Les logs des exécutions du robot ne sont conservés que **2 heures** en base de données pour préserver l'espace disque.
- **Affichage UI des Logs** : L'onglet `Logs Robot` n'affiche désormais que les logs générés lors du **tout dernier cycle** d'exécution, au lieu de toute la journée.
- **Base de Données** : Refonte totale pour supprimer la dépendance à Supabase (passage aux identifiants génériques PostgreSQL `DB_HOST`, `DB_USER`, etc.).

## [v2.1.0] - 2026-09-01
### Ajouté
- **Ordres Virtuels (Attendre Setup)** : L'Agent maintient le capital disponible en vérifiant manuellement toutes les 5 minutes si les critères sont validés avant d'émettre l'ordre d'achat.
- Conformité Sharia et gestion dynamique des garde-fous (OrderGuardrails).
