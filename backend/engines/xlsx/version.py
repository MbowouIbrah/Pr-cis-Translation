"""Version du moteur XLSX.

**1.0.0** — la couverture du TEXTE est complète, et sa PRÉSENTATION suit.

Les dix endroits où Excel range du texte sont traités : chaînes partagées et
en ligne, noms d'onglets, graphiques, zones de texte, les deux formats de
commentaires, en-têtes de tableaux structurés, croisés dynamiques, formats de
nombre personnalisés et propriétés du document. `couverture()` ne déclare plus
aucun manque.

L'expansion est gérée (`expansion.py`) : une traduction plus longue que sa
source élargit sa colonne au lieu d'être coupée par sa voisine, une cellule à
retour à la ligne gagne la hauteur de ses lignes en plus, un nombre dont le
format porte un littéral traduit ne s'affiche plus en `#####`.

La 0.2.0 refusait le 1.0.0 pour cette seule raison : qui lit « 1.0.0 » dans
`/health` comprend « ce moteur rend un classeur fidèle ». C'est désormais vrai,
aux limites près que `CONTEXTE.md` énumère.
"""

__version__ = "1.0.0"
