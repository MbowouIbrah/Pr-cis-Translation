"""Version du moteur PDF — et clé de cache des rendus.

DEUX RÔLES, UN SEUL NUMÉRO
--------------------------
Ce numéro dit ce que vaut le moteur, ET il entre dans le nom des rendus mis en
cache. Ce n'était pas le cas : un jeton de cache (`v27`) vivait à côté d'une
version implicite, et rien ne les liait.

POURQUOI LA CLÉ DE CACHE EN DÉPEND
----------------------------------
Le résultat d'une traduction est mis en cache sous une clé faite du nom, du hash
du contenu, de la langue, de la qualité et de la sélection de pages. Aucune de
ces cinq valeurs ne bouge quand on CORRIGE LE MOTEUR. Un document déjà traduit
continuait donc à servir éternellement le PDF d'avant le correctif.

Le cache n'était pas de trop : il était MALHONNÊTE. Il prétendait que la sortie
ne dépend que de l'entrée, alors qu'elle dépend aussi du code qui la produit.
`v2_pages.json` ne stocke d'ailleurs pas que la traduction — il stocke les
DÉCISIONS du moteur (`align`, `container_bbox`, ~55 fois par page). Le rejouer,
c'est rejouer la géométrie d'une version antérieure.

QUAND L'INCRÉMENTER
-------------------
* **patch** (1.0.x) — tout correctif qui peut changer un PIXEL du rendu :
  géométrie, alignement, regroupement en paragraphes, reflow, choix de police.
  Les rendus en cache deviennent introuvables, ce qui est le but.
* **mineure** (1.x.0) — une capacité nouvelle, sans rupture d'interface.
* **majeure** (x.0.0) — le contrat d'extraction ou d'injection change.

Une modification qui ne touche ni le rendu ni le découpage (journalisation,
tests, commentaires) ne concerne aucun des trois.

Ne pas l'incrémenter quand il le fallait, c'est resservir un rendu périmé sans
que rien ne le signale — le bug est SILENCIEUX, et c'est ce qui le rend coûteux :
on croit avoir corrigé, l'utilisateur voit le contraire, et on cherche le défaut
dans le moteur alors qu'il est dans la clé.

HISTORIQUE
----------
Le suivi repart de **1.0.0** le 22/07/2026. Les jetons `v20` à `v28` qui
précèdent appartiennent à la phase de mise au point ; leurs caches ont été
purgés, aucune collision n'est possible.
"""

__version__ = "1.0.3"

#: Jeton porté par le NOM des rendus en cache (`…render.v1.0.0.pdf`).
#: DÉRIVÉ de la version : les deux ne peuvent plus diverger.
ENGINE_VERSION = f"v{__version__}"
