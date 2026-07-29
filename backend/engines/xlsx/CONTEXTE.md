# Moteur XLSX — contexte

Version **0.1.0** · `engines/xlsx/` · inscrit au registre sous `xlsx`

> ## ⚠ C'est un SQUELETTE
>
> Le **chemin** est complet : un classeur est accepté, décompressé, parcouru,
> ses chaînes relevées et balisées, la traduction réinjectée, le fichier
> réassemblé. Un `.xlsx` entre et ressort ouvrable par Excel.
>
> La **couverture du format** ne l'est pas. Excel range du texte à une dizaine
> d'endroits ; **six** sont traités. Les autres sont recensés plus bas — pas
> oubliés, pas masqués.
>
> D'où le **0.1.0** et non 1.0.0 : une version majeure dit « le contrat est
> stable ». Le déclarer en 1.0.0 ferait croire l'inverse à qui lit `/health`.

> À lire d'abord : [`../CONTEXTE.md`](../CONTEXTE.md) — la règle
> d'indépendance, l'instance par opération, les balises de runs.

## Le principe

Un `.xlsx` est un ZIP d'XML, comme un `.docx` ou un `.pptx`. On le décompresse,
on modifie les nœuds de texte **sur place** avec lxml, on re-zippe. Aucun nœud
n'est supprimé ni recréé : styles, largeurs de colonnes, mises en forme
conditionnelles et formules survivent par construction.

```python
eng = engines.new_engine("xlsx")          # instance NEUVE, obligatoire
data, path = eng.extract_text(xlsx, json_out)
ok, msg    = eng.inject_translation(original, json_traduit, sortie)
```

## Deux règles qu'un classeur impose et qu'un document n'impose pas

### On ne traduit jamais un nombre

« 1 234,50 » passé au modèle revient parfois en « 1,234.50 », parfois en toutes
lettres. La cellule cesse alors d'être numérique, **et toute formule qui la lit
se casse**. Une seule cellule suffit à propager `#VALUE!` dans une feuille
entière.

### On ne touche jamais à une formule

Une formule est du **code**. Traduire `SUM` en `SOMME` produit `#NAME?` — et
Excel ne localise les noms de fonctions qu'à l'affichage, jamais dans le
fichier. Les formules ne sont donc même pas relevées.

## Le piège déjà payé

**La garde numérique se décide sur la cellule ENTIÈRE, jamais morceau par
morceau.**

Une cellule mise en forme est découpée en plusieurs `<t>` : « Total » en gras,
« 2026 » en maigre. La première version écartait les morceaux numériques un à
un — donc « 2026 » — et ne balisait que « Total ». À l'injection, la règle des
balises veut qu'aucun nœud ne conserve son texte source : le nœud non couvert
était **vidé**. La cellule perdait la moitié de son contenu, **sans la moindre
erreur**.

Trouvé par le classeur **synthétique** de `test_xlsx_squelette.py`, pas par un
fichier réel : une cellule à deux graisses est rare, et le défaut n'apparaît
que là. C'est précisément pourquoi la preuve se fait sur du synthétique.

## Ce qui est fait

| Partie | État |
|---|---|
| `xl/sharedStrings.xml` | ✅ le gros du texte — Excel y déduplique les chaînes |
| Cellules `t="inlineStr"` | ✅ chaîne écrite dans la feuille, sans magasin partagé |
| `xl/workbook.xml` — noms d'onglets | ✅ renommage **et** réécriture des références, d'un seul geste |
| `xl/charts/chart*.xml` | ✅ titres, noms d'axes, étiquettes en dur — **jamais** les caches |
| `xl/drawings/drawing*.xml` | ✅ zones de texte et formes — **jamais** le `name=` |
| `comments*.xml` + `threadedComments/*` | ✅ les **deux** formats — **jamais** les `<author>` |

La seconde n'est pas un détail : beaucoup d'exports automatiques n'utilisent
**que** cette forme et ne produisent aucun `sharedStrings.xml`. Ne lire que le
magasin partagé rendrait ces classeurs **inchangés sans lever d'erreur** — le
pire des échecs, celui qui se croit réussi.


### Annotations : on traduit ce qui s'affiche, jamais ce qui identifie

Deux formats de commentaires **coexistent** : `comments*.xml` (historique,
celui qui porte le texte affiché) et `threadedComments/*` (moderne, les fils).
Excel maintient les deux — n'en traiter qu'un laisse la moitié des notes en
langue source.

Deux choses ne sont jamais traduites, et ce ne sont pas des oublis :

* `name="TextBox 1"` — identifiant **interne** d'une forme, jamais affiché,
  cité par les macros ;
* `<author>` — un **nom de personne**, et `authorId` y renvoie **par index** :
  toucher à cette liste réattribuerait les notes.

### Les graphiques : là où la logique du PPTX ne se recopie pas

Le moteur PPTX traduit les `<c:v>` d'un graphique, et il a raison : là-bas, le
graphique porte ses **propres** données.

Dans un classeur, c'est faux. Un `<c:v>` sous un `<c:strCache>` est le **cache**
d'une cellule de la feuille — déjà traduite via `sharedStrings`. Le traduire à
nouveau, c'est soumettre deux fois le même texte au modèle (qui peut rendre deux
formulations : le graphique afficherait alors autre chose que sa feuille), et
pour rien — Excel réécrit ce cache au premier rafraîchissement.

Mesuré sur un graphique produit par Excel : les **7 `<c:v>` sont tous sous un
cache**, et les **3 vrais textes sont tous des `<a:t>`**.

La règle : on traduit le texte riche (`<a:t>`) et les `<c:v>` **sans** cache
au-dessus — du texte saisi en dur, qui n'est le reflet de rien.

### Les noms d'onglets : deux gestes qui n'en font qu'un

Un nom d'onglet est cité à **trois** endroits — `<sheet name>`, les
`<definedName>`, et les `<f>` de chaque feuille. Renommer sans réécrire produit
`#REF!` partout : un classeur ouvrable, d'apparence traduite, dont tous les
calculs sont morts.

La réécriture n'est pas un `str.replace`, et quatre cas l'imposent :

| Cas | Piège |
|---|---|
| `="Ventes du mois"` | chaîne **littérale** qui contient le nom |
| `Ventes` / `Ventes2` | un nom en **préfixe** d'un autre |
| `[1]Ventes!A1` | classeur **externe**, qu'on ne traduit pas |
| `'Chiffre d''affaires'!A1` | apostrophes **doublées** |

**Un nom refusé n'est pas une erreur.** Excel impose ses règles (31 caractères,
pas de `: \ / ? * [ ]`, pas d'apostrophe, pas de doublon) : une traduction qui
les viole n'est pas appliquée et l'onglet garde son nom. Un onglet non traduit
se voit ; un fichier qu'Excel refuse d'ouvrir ne se rattrape pas.

## Ce qui n'est pas encore fait

Recensé dans `_PARTIES` (`engine.py`), avec le motif de chacun. C'est le **plan
de travail**, ordonné par importance et non par ordre alphabétique.

| Partie | Pourquoi ça compte |
|---|---|
| `xl/tables/table*.xml` | En-têtes de tableaux structurés — visibles, et cités en références structurées. |
| `pivotCache` / `pivotTables` | Les libellés sont **dupliqués** entre le cache et la table. N'en traduire qu'un des deux désaligne le croisé au premier rafraîchissement. |
| `xl/styles.xml` — formats personnalisés | Un format peut contenir du texte littéral (`#\ ##0\ "F CFA"`). C'est du visible, et la syntaxe doit rester intacte autour du mot. |
| `docProps/core.xml` | Titre et sujet. Rarement décisifs — en dernier. |

`XLSXTranslatorEngine.couverture()` rend cette table, et le relevé d'extraction
transporte la liste des manques sous `workbook.non_traite`. **L'écart entre la
promesse et le code reste ainsi mesurable**, au lieu d'être une impression.

## L'aperçu progressif — feuille par feuille

Le classeur suit désormais le même flux que le PDF et le PPTX :

1. **socle** — le classeur d'origine, converti une fois, affichable tout de
   suite en langue source ;
2. **greffe** — chaque feuille traduite est convertie *seule* et sa page
   remplace celle du socle.

`build_partial_xlsx(sortie, only_sheets={…})` écrit un classeur ne contenant
que ces feuilles ; `feuilles()` les énumère **dans l'ordre d'affichage**.

### Ce qui distingue un classeur d'un diaporama

Une diapositive est autonome : on l'extrait, on la traduit, on l'injecte. **Une
feuille ne l'est pas.** Excel déduplique le texte de tout le classeur dans
`sharedStrings.xml`, et une même chaîne peut servir dix feuilles — aucune ne
peut la revendiquer.

On ne découpe donc **pas la traduction** par feuille : le classeur part d'un
bloc, seul découpage honnête. C'est **l'affichage** qui est progressif.
`chaines_par_feuille()` reconstruit le rattachement dans l'autre sens, en
lisant les index que chaque feuille cite.

### Trois pièges, chacun verrouillé par un test

| Piège | Conséquence si ignoré |
|---|---|
| Le numéro du fichier ne dit rien de la position de l'onglet | Feuilles affichées dans le désordre |
| `sharedStrings.xml` est indexé par **position** | L'élaguer décale les index : les feuilles gardées affichent le mauvais texte |
| `ProgressivePreview` compte en pages **1-basées**, `feuilles()` en index **0-basés** | Tout l'aperçu décalé d'un cran |

Le partiel est **non destructif** (fichiers de contrôle calculés en mémoire) et
écrit **atomiquement** — le convertisseur lit pendant qu'on écrit.

## Limites au-delà du texte

* **une feuille longue s'imprime sur plusieurs pages.** La greffe exige alors
  un appariement certain feuille ↔ page, qu'on n'a pas : dans ce cas l'aperçu
  progressif est **désactivé pour ce classeur** et l'utilisateur garde le socle
  en langue source jusqu'au document final. Mieux vaut ne rien greffer que
  poser une traduction en face de la mauvaise page ;
* l'aperçu passe par LibreOffice, comme tout format non-PDF ;
* **l'expansion n'est pas gérée** — une traduction plus longue que sa source
  déborde de sa colonne ou s'affiche en `#####`. Les moteurs PDF et PPTX
  ajustent ; celui-ci ne le fait pas encore, et c'est le premier vrai chantier
  de fidélité après la couverture des parties.

## Vérifier

```bash
backend/venv/Scripts/python.exe backend/tests/test_xlsx_squelette.py
```

29 contrôles sur un classeur **synthétique** écrit par la suite elle-même.
Prouvé par mutation : garde numérique remise par-morceau → 28/29 ; lecture des
chaînes en ligne retirée → 27/29.
