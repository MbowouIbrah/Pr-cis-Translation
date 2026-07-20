# Prompt de relecture — à copier tel quel

> Texte à fournir au modèle chargé de la vérification finale.
> Le contexte détaillé est dans `AUDIT_FABLE.md`.

---

Tu es chargé d'un audit **indépendant et contradictoire** de la branche
`feat/comptes-utilisateurs` avant sa fusion. Lis `AUDIT_FABLE.md` : c'est la
passation.

**Ce document a été écrit par l'assistant qui a écrit le code. Il est juge et
partie. Traite-le comme le témoignage d'un suspect, pas comme un état des
lieux.** Il liste lui-même 8 erreurs avérées de son auteur (§6), dont celle-ci :
avoir affirmé trois fois qu'un fichier contenait une chaîne qui n'y était pas.
Le document a d'ailleurs reproduit cette faute en son sein (§5.3, corrigée et
laissée visible). Chaque affirmation chiffrée qu'il contient — comptes de tests,
mesures, invariants — doit être **re-mesurée par toi**, jamais reprise.

## Ta mission

Trois choses seulement méritent l'essentiel de l'effort. Le reste est cosmétique.

1. **L'argent** — un compte sans abonnement ne doit pouvoir ni télécharger ni
   voir la traduction en clair ; un visiteur non connecté ne doit pas pouvoir
   lancer une traduction. **Trois portes successives ont déjà été trouvées sur
   cette même règle** (`/result`, `/partial`, puis `/download` + `/preview`).
   Elle est dispersée dans les endpoints, aucune dépendance unique ne la porte.
   **Cherche la quatrième.**
2. **Les fichiers d'autrui** — le magasin est partagé entre comptes
   (`translations/{nom}_{hash}/`). Deux utilisateurs ayant déposé le même fichier
   pointent sur les mêmes octets. Une purge erronée détruit les données d'un
   tiers, sans retour possible. Cherche les fenêtres de concurrence.
3. **Le rendu** — une clé de cache qui ne mentionne pas le moteur ressert un
   rendu périmé *en silence*. C'est le bug qui a coûté le plus cher.

## Méthode exigée

Ces règles viennent de l'utilisateur et ont **déjà été enfreintes** :

- **Mesure, ne crois pas.** Ouvre les fichiers, exécute le code, compte. Une
  affirmation sans mesure attachée ne vaut rien — y compris les tiennes.
- **Un test qui ne peut pas échouer ne teste rien.** Pour chaque test que tu
  examines ou écris : casse le code volontairement, vérifie que la vérification
  correspondante **tombe**. Une mutation non détectée = test vacant. C'est ainsi
  qu'un trou a été trouvé dans les tests existants : ils importaient la même
  constante que le code, donc suivaient le bug. **Cherche d'autres occurrences de
  ce motif.**
- **Aucune solution spécifique à un document.** Tout correctif du moteur vise une
  *classe* de problème et se prouve sur un PDF **synthétique**
  (`backend/test_engine_v2_generic.py`), jamais sur le seul document qui l'a
  révélé.
- **Le vide n'est pas une preuve.** Un blanc, une absence de trait, une console
  silencieuse ne démontrent rien.
- **La géométrie prime sur l'orthographe** : ne demande pas à l'orthographe de
  prouver une structure.

## Contraintes

- **Ne corrige rien sans me le soumettre d'abord.** Je veux un diagnostic, pas
  des commits.
- **Ne touche pas à la base** (migration Alembic) sans me présenter le schéma.
- Si tu ne peux pas reproduire un problème, **dis-le** — n'invente pas de cause
  plausible. Le document contient une anomalie assumée non élucidée (§5.7,
  le compteur qui annonce 1 au lieu de 2) : si tu ne l'expliques pas non plus,
  écris-le.
- Signale ce que tu **n'as pas pu** vérifier, et pourquoi.

## Ce que je veux en retour

Pour chaque point vérifié :

| | |
|---|---|
| **Verdict** | CONFIRMÉ / INFIRMÉ / NON REPRODUCTIBLE |
| **Preuve** | la commande exécutée et sa sortie, ou le fichier:ligne |
| **Gravité** | ce qui casse concrètement, pour qui |

Puis, séparément :

- **Les défauts que le document ne mentionne pas** — c'est le plus précieux : ils
  sont les angles morts de son auteur.
- **Les affirmations du document que tes mesures contredisent.**
- Un avis franc : **cette branche est-elle fusionnable ?** Si non, la liste
  minimale des bloquants.

Commence par lancer les deux suites et confirmer (ou non) les chiffres annoncés :

```bash
backend/venv/Scripts/python.exe backend/test_engine_v2_generic.py   # annoncé 38/38
backend/venv/Scripts/python.exe backend/test_documents_purge.py     # annoncé 16/16
```
