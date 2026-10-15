"""Version du moteur XLSX.

**0.2.0** — la couverture du TEXTE est complète, la FIDÉLITÉ ne l'est pas.

Les dix endroits où Excel range du texte sont traités : chaînes partagées et
en ligne, noms d'onglets, graphiques, zones de texte, les deux formats de
commentaires, en-têtes de tableaux structurés, croisés dynamiques, formats de
nombre personnalisés et propriétés du document. `couverture()` ne déclare plus
aucun manque.

POURQUOI PAS 1.0.0
------------------
La version 0.1.0 promettait le passage en 1.0.0 « quand les chaînes en ligne,
les graphiques et les tableaux croisés seront traités ». Ils le sont — mais
cette condition était incomplète, et le dire vaut mieux que la tenir à la
lettre.

Il reste un défaut de RENDU : **l'expansion n'est pas gérée**. Une traduction
plus longue que sa source déborde de sa colonne ou s'affiche en `#####`. Le
texte est juste ; sa présentation ne l'est pas toujours. Les moteurs PDF et
PPTX ajustent, celui-ci pas encore.

Une version majeure dit « le contrat est stable ». Qui lit « 1.0.0 » dans
`/health` comprend « ce moteur rend un classeur fidèle » — ce qui n'est pas
encore vrai. D'où 0.2.0 : la couverture a franchi un palier, la fidélité non.
"""

__version__ = "0.2.0"
