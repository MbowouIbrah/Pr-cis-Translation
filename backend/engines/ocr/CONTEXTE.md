# Moteur OCR — contexte

Version **0.1.0** · `engines/ocr/` · documents **scannés** · absent du registre

> À lire d'abord : [`../CONTEXTE.md`](../CONTEXTE.md) — la règle d'indépendance,
> l'instance par opération.

## Où il en est : l'IDENTIFICATION, et rien d'autre

Il lit un scan, en déduit mots → lignes → paragraphes, et rend le document
d'origine avec les blocs **encadrés**. Il ne traduit pas, n'efface rien, ne
réécrit rien.

> « le cœur du travail n'est pas la traduction mais la détection : une bonne
> détection donne un document de meilleure qualité »

Deux versions précédentes traduisaient déjà, et le rendu n'était pas livrable
parce que la détection ne l'était pas (voir
[`docs/bilan-ocr-2026-07-28.md`](../../../docs/bilan-ocr-2026-07-28.md), et les
tags `archive/ocr-v1-*` / `archive/ocr-v2-*`).

## Où on en est : **75 %** (note de l'utilisateur, 08/08)

L'essentiel est détecté. La première partie de l'identification est faite,
elle n'est pas finie — restent 5 défauts mesurés (voir plus bas), puis
d'autres problèmes de détection à traiter ensuite.

## La chaîne

```
scan ─▶ lecture.py ─▶ spans ─▶ _group_text_lines ─▶ relecture.py ─▶ _group_paragraphs ─▶ tri.py ─▶ fusion.py ─▶ apercu.py
                        │       + justifie.py         (moteur PDF)                    │
                        │        (moteur PDF)                                         └── audit.py juge
                        └── contrat de 14 champs : spans.py
```

`fusion.py` passe **après** le tri, délibérément : recoller deux morceaux dont
l'un aurait été écarté comme débris ferait rentrer le débris par la fenêtre.

Le moteur PDF est une **bibliothèque de mise en page** : il s'instancie sans
état ni fichier (`vars(e) == {}`). On ne le modifie **jamais** pour arranger un
cas OCR sans repasser `test_engine_v2_generic.py` (59/59).

## Comment on juge : les invariants, pas les pixels

Trois règles posées par l'utilisateur, **sans aucun seuil** (`invariants.py`) :

* un mot n'appartient pas à deux lignes ;
* un mot n'appartient pas à deux paragraphes ;
* un paragraphe n'en contient pas un autre.

C'est ce qui manquait aux deux versions précédentes : leurs indicateurs
mesuraient des **pixels** et restaient bons sur une page jugée mauvaise.

### L'AUDIT CHIRURGICAL — la mesure qui fait foi (`audit.py`)

Les invariants sont **nécessaires et insuffisants** : ils tolèrent 0,5 pt de
recouvrement et n'annoncent une inclusion qu'au-delà de 90 %. L'utilisateur a
demandé mieux :

> « chaque bloc doit délimiter au millimètre près uniquement le paragraphe
>   concerné, sans empiéter ni toucher un autre bloc, ni être inscrit dans un
>   paragraphe »

`audit.py` n'accepte **rien** : tout recouvrement strictement positif est un
défaut. Six familles :

| type | ce qu'il détecte |
|---|---|
| `chevauchement` | deux blocs partagent une surface |
| `bloc_dans_bloc` | l'un est entièrement inscrit dans l'autre |
| `blocs_colles` | aucun blanc entre eux (< 0,25 × ligne, jamais moins que le trait) |
| `cadre_lache` | le cadre revendique du vide au-delà de son encre |
| `ligne_partagee` | **une LIGNE de texte revendiquée par deux blocs** |
| `ligne_amputee` | une ligne qui commence après le fer **et dont le début est ailleurs** |

Les deux dernières ont été ajoutées après coup, **signalées à l'œil** et non
par le compte. `ligne_partagee` est celle qui remonte à la CAUSE : les
chevauchements de cadres en découlent, le morceau expulsé retombant dans le
cadre qu'il a quitté.

### L'état mesuré (3 pages du Code de la Route, vrai scan)

| | valeur |
|---|---|
| mot dans deux lignes | **0** |
| mot dans deux paragraphes | **0** |
| défauts de l'audit | **5** (était 27) |

Les deux premières à zéro depuis le début : le regroupement ne duplique jamais
un mot — c'est ce qui a rendu la fusion licite. Détail des 5 : 3
`chevauchement`, 2 `blocs_colles`. Répartition **0 / 5 / 0** : les pages 1 et
3 sont propres, la page 2 concentre tout.

### ⚠ UN SEUIL SURVIT RAREMENT AU CORRECTIF SUIVANT (09/08)

**9 des 17 défauts n'en étaient pas.** `_ECART_MEME_LIGNE = 4.0` avait été
calibré sur une mesure honnête : gouttières de colonnes à 13-55 × la hauteur
de ligne, vrais défauts à 1,3-1,8 ×, deux populations sans recouvrement.

Les correctifs suivants — alignement des lignes OCR, bridage des corps,
recollage justifié — ont **resserré la géométrie**. Les mêmes gouttières sont
retombées à 0,5-2,6 ×, donc **sous le seuil**. Les deux populations se
recouvraient désormais entièrement, et l'audit accusait neuf colonnes
parfaitement séparées :

```
'Usagers | Le conducteur'                      cadres disjoints de 12,96 pt
'Le dépassement est interdit si | ...autorisé' cadres disjoints de  8,16 pt
'Les panneaux de stationnement | Les bornes'   cadres disjoints de 10,80 pt
```

Les 9 avaient **zéro mot commun** et des **cadres disjoints**. Je chassais un
chiffre qui comptait du bon travail comme des erreurs.

Le garde-fou qui décide désormais **ne se règle pas** : deux blocs dont les
cadres sont disjoints ne peuvent pas se déchirer une ligne — le morceau
expulsé retombe forcément dans le cadre qu'il a quitté.

**Le discriminant qui semblait meilleur, mesuré et rejeté.** Compter les
lignes APPARIÉES côte à côte : deux colonnes s'apparient sur toute leur
hauteur, une ligne déchirée est un accident isolé. Éprouvé sur du synthétique
à vérité connue — rapporté au bloc le plus court, il vaut 1,00 dans les
4 cas (ne discrimine rien) ; rapporté au plus long, il sépare bien la ligne
arrachée (0,17) des colonnes égales (1,00) mais **condamne les colonnes de
hauteurs inégales (0,33)** — c'est-à-dire `'Usagers' | 'Le conducteur'`.

**Le PDF ne sait rien de ses images** (sondé le 09/08) : deux images
plein-cadre par page (moitié haute, moitié basse du scan), `get_drawings()`
vide, zéro mot natif. La piste « demander au PDF où sont les dessins » est
close — mesurée en 30 secondes plutôt qu'en une journée.

## Les correctifs qui ont payé

**RELIRE LA LIGNE POUR DÉFAIRE LES MOTS FONDUS** (`relecture.py`, 10/08).
Signalé à l'œil : la colonne des paginations ressortait en `'532'`,
`'59à80'`, `'81à96'`, `'(07à52'` — plusieurs nombres soudés en un seul
« mot ». Or ces lignes sont parfaitement lisibles : c'est la **segmentation
de la page entière** qui échoue, pas la reconnaissance.

Donner la bande SEULE à Tesseract (`--psm 7`, sur une ligne déjà délimitée
par le regroupement) les sépare à 93-96 %. Mesuré : **8/12 → 9/12**
paginations correctes, **audit inchangé à 5**.

⚠ **On ne remplace que le DÉCOUPAGE** : chaque mot relu est reposé dans la
hauteur et sur la baseline du span qu'il remplace — **en X le relu, en Y
l'ancien**. C'est la leçon payée deux fois le même jour.

Deux portes, et il en faut deux : **même contenu mieux découpé**
(`'81à96'` → `'81' 'à' '96'`, lu à 80 % contre 93-96 % — l'écart de 13-16 ne
suffirait pas, **la confiance n'est pas le bon signal**, un span fondu peut
être lu avec assurance) ; **contenu différent** (`'532'` → `'53 à 68'`, où le
découpage ne prouve plus rien et où l'on exige un écart franc).

Coût : un appel Tesseract par ligne (~300 sur 3 pages). À généraliser, le
réserver aux lignes suspectes.


**LES FLÈCHES NE SONT PAS DES MOTS** (`_sans_traits_de_dessin`, 10/08).
Signalé à l'œil sur le sommaire : les flèches et filets de conduite (`'—'`,
`'——'`, `'—>'`, `'—+—'`, `'=='`) étaient encadrés comme des mots — **onze**
sur la seule page 2.

⚠ `tri.py` ne pouvait pas les attraper, et ce n'est pas un oubli : **il juge
des BLOCS**. Une flèche seule est bien écartée ; celle qui a été absorbée dans
un bloc portant du vrai texte le rend lisible à 80 %, donc le bloc est retenu
avec sa flèche. Le filtre manquait au niveau du **mot**.

Le discriminant est la **largeur par caractère**, rapportée à la largeur de
glyphe de la page (jamais en points absolus — un document au corps double
aurait une ponctuation double). Mesuré sur 54 spans sans caractère lisible,
deux populations **sans recouvrement** :

| | ratio largeur / gw |
|---|---|
| ponctuation réelle `':'` `'-'` `'+'` | 0,24 à **2,42** |
| traits et flèches `'—'` `'——'` `'=='` | **3,01** à 12,27 |

Ni la distance au mot voisin (0 à 4 pt dans les deux cas) ni la confiance
(`'|'` lu à 87 %) ne séparent quoi que ce soit.


**LA MEILLEURE LECTURE GAGNE** (`_prolonge`, seconde porte, 09/08). Signalé à
l'œil sur le sommaire : la colonne des paginations ressortait en `'md sis'` et
`'min'` au lieu de `'187 à 200'` et `'201 à 212'` — alors que la passe
distance-au-blanc les lit à **75-95 %**. Ce ne sont pas des fragments
(`'201'` ne contient pas `'min'`), donc la règle du contenu ne pouvait rien.

⚠ **L'AUDIT NE VOIT PAS CE DÉFAUT** : il juge la géométrie, et les cadres sont
corrects. Balayé de 0 à 60, il rend 5 défauts partout. La mesure qui fait foi
est donc le **TEXTE**, contre une vérité relevée à l'œil (les 24 nombres des
paginations) : **13/24 → 17/24**.

⚠ **ON NE PREND QUE LE TEXTE, JAMAIS LA BOÎTE EN Y.** Première version :
la boîte du candidat était substituée aussi — et l'aperçu a montré une
régression que TOUS les compteurs manquaient (audit 5, texte 17/24). Les deux
passes ne découpent pas au même endroit : `'min'` couvre 389→427 (toute la
pagination), `'201'` seulement 389→398. On perdait **29 pt de largeur**,
`'201 à 212'` ressortait en deux blocs et `'187 à 200'` se faisait absorber
par le paragraphe voisin, qui perdait son cadre.

Le partage exact de ce que chaque passe sait : **en X le candidat** (il a su
séparer `'201'`, `'à'`, `'212'` là où l'autre voyait un mot large de 38 pt),
**en Y le rival** (sa hauteur et sa clé de ligne portent la baseline de toute
la rangée — c'est en la déplaçant que la substitution disloquait des blocs,
15 → 34). Un span qui a cédé ne bloque plus les suivants (`_cede`), sans quoi
un mot faux en avalait trois justes.

Deux garde-fous, chacun imposé par une régression parmi les 9 substitutions
mesurées : ne pas remplacer par plus **court** (`'59à80'` → `'69à'`) ni par
moins **lisible** (`'Il'` → `'||'`, deux traits lus avec assurance). Résultat :
7 substitutions, 4 gains nets, **aucune régression**.


**L'alignement sur les lignes de Tesseract** (`_aligner_sur_lignes_ocr`). On
jette sa segmentation en PARAGRAPHES (`par_num`, fausse en multi-colonnes),
mais `line_num` est un autre signal, et il est **bon** : mesuré sur « Les
permis moto », Tesseract rendait les 3 lignes exactement, mots dans l'ordre,
pendant que l'aval les mélangeait. On donne donc une baseline unique (la
médiane) à tous les mots d'une même ligne OCR. Les boîtes ne sont pas touchées.

**Le bridage du corps** (`_brider_les_corps`). `size` gouverne la tolérance de
rangée (0,45 × size) : une boîte gonflée élargit sa propre tolérance et avale
la ligne voisine. Plafonné à 1,2× la médiane. ⚠ **Ce correctif avait été
essayé et rejeté AVANT l'alignement** (51/21/19/24, bruit) — il ne mordait pas
parce que les baselines étaient déjà dispersées en amont. **Un correctif
inefficace ne l'est pas toujours définitivement : il peut attendre celui qui
le rend utile.**


**Les lignes JUSTIFIÉES recollées** (`justifie.py`). Justifier une colonne
étroite étire ses blancs : mesuré à 2,0× et 3,5× la largeur de glyphe, au-delà
du seuil de coupe en colonnes (2,5×). Un paragraphe de 4 lignes ressortait en
**6 blocs**, et deux de ses mots (« de », « si ») étaient écartés comme débris.

`_rejoin_justified` du moteur PDF traite ce cas mais exige 3 lignes
**intactes** comme témoins ; il n'y en avait qu'**une**. C'est la « colonne
justifiée courte », limite structurelle connue.

Le signal qu'on a ici : on juge l'**étendue des RANGÉES**, pas des fragments.
Une rangée coupée commence quand même au fer gauche de sa colonne et finit à
son fer droit — l'étirement déplace les blancs *intérieurs*, jamais les bords.
Mesuré : 4 rangées sur 5 au même fer gauche, 3 sur 5 au même fer droit.
Résultat : 6 blocs → **3**.



**Le plafond d'INTERLIGNE** (`_interligne`). Un mot ne peut pas être plus haut
que l'interligne : il empiéterait sur la ligne voisine *par construction*. Sur
du texte **barré**, la barre traverse la ligne, la mesure d'encre l'attrape et
remonte à la ligne du dessus — mesuré page 1, trois lignes de 8,4 / 9,0 /
9,9 pt pour un interligne de 6,3, dont les boîtes se recouvraient. L'interligne
est **relevé** sur la page (écart médian entre bas d'encre consécutifs : 5,10 /
4,80 / 5,20), jamais choisi. Le plafond de 2,2 × la médiane des hauteurs, lui,
ne mordait pas : les mots fautifs font 2,1 à 2,6 × la médiane.

**La FUSION des paragraphes coupés** (`fusion.py`). Deux blocs qui se touchent
sont parfois un seul paragraphe. Mais sur 15 paires en conflit, **3 seulement**
le sont : 9 sont deux colonnes côte à côte, 3 un sommaire et son numéro de
page. Fusionner tout ce qui se touche abîmerait 12 cas pour en réparer 3.

Le discriminant, lu dans les mesures : écart de fer gauche de **0,00 à 0,24 pt**
pour les paragraphes coupés, **8 à 306 pt** pour les colonnes — deux populations
sans recouvrement. Trois garde-fous, chacun imposé par un cas réel : même fer
(ou recouvrement en x total, pour le texte à contour), largeurs comparables,
et **jamais de chaîne** (l'union de 32 et 34 contient 33).

**L'aperçu ne GONFLE plus les cadres.** Il les dessinait 1 pt plus grands que
les blocs : 6 paires se touchent vraiment, **14** se touchaient à l'écran.
L'utilisateur voyait donc des défauts que l'audit ne signalait pas. Un aperçu
qui ment sur ce qu'il montre invalide le jugement à l'œil, qui est le mode de
jugement retenu ici. `audit.py` connaît désormais l'épaisseur du trait
(`_TRAIT`), et trois tests gardent l'accord entre les deux modules.

**La boîte de LIGNE.** Tesseract rend souvent, pour plusieurs mots
consécutifs, la boîte de leur *ligne* et non du *mot* — 28 % des mots d'une
page. Détection sans seuil : trois mots dont les bords haut **et** bas
coïncident exactement ne se produisent pas par hasard.

**Le BAS DE L'ENCRE est le seul repère comparable.** Deux populations
cohabitent sur une même ligne (boîtes de ligne à 7,2 pt, mots mesurés à
3,9 pt). Leurs baselines diffèrent alors de 2,57 pt pour une tolérance de
1,75 → elles se séparent. Le bas de l'encre, lui, est identique pour tous les
mots d'une ligne (mesuré : 72,7 puis 79,4).

**La double lecture est éteinte par défaut.** La v1 l'imposait sur une mesure
juste — la binarisation seule détruit les gros titres — mais qui comparait les
deux lectures SÉPARÉMENT, jamais le coût de leur UNION. Mesuré : elle n'ajoute
que **12 mots sur 1415**, dont plusieurs sont des morceaux de mots déjà lus
entiers (« Usag » + « er »), et fait passer les violations de 18 à 22. Deux
lectures du même texte à des découpes différentes se superposent, et c'est ce
qui fabrique les boîtes de ligne. Le paramètre reste disponible.

## Les impasses MESURÉES — ne pas les refaire

Chacune a coûté un balayage. Elles sont aussi écrites dans le code au point
concerné.

| piste | balayage | résultat |
|---|---|---|
| constante de descente (jambages) | 0,70 → 1,00 | 48 à 54, **sans tendance** |
| plafonner la hauteur d'encre | 1,2× → 3,0× médiane | 22/29/23/23/22/22/22 |
| plafonner la `size` des spans *(avant l'alignement)* | 1,0× → 2,0× médiane | 51/21/19/24, bruit — **devenu efficace APRÈS l'alignement** |
| **borner la dérive d'ancre** (moteur PDF) | 1,0× → 4,0× tol | 73/47/46/36/33/33 — **toute borne dégrade** |
| **couper les lignes fusionnées** | saut 0,5× → 1,3× médiane | 29/24/24/22 — **jamais mieux que ne rien faire** |
| augmenter le DPI de lecture | 300 → 600 | 22/25/24/24 — **300 est déjà le meilleur** |
| serrer `_ECART_MAX_GW` (recollage justifié) | 2,5 → 6,0 | 38/36/29/25/23/23 — **serrer DÉGRADE** |
| plafonner **tous** les mots à l'interligne | — | 17 → 20, `bloc_dans_bloc` reparaît (0 → 2) |
| abaisser `_CARS_MIN_COULEUR` 2 → 1 (pour le « à » de « 201 à 212 ») | — | audit **5 → 16**, et **aucun** nombre gagné |
| accepter **2** mots pour une boîte de ligne | — | 17 → 19 ; `_paires_trop_hautes` conservée, **non appelée** |

Les trois dernières méritent un mot :

* **serrer l'écart de recollage** semblait évident pour empêcher de souder
  deux colonnes. Le balayage dit l'inverse : le seuil coupe alors de vraies
  lignes justifiées. Le bon garde-fou était ailleurs — ne pas enjamber le fer
  gauche d'une autre colonne attestée ;
* **plafonner tous les mots** à l'interligne : l'argument géométrique est juste
  et pourtant faux en pratique, une capitale suivie d'un jambage (« Jg », « À »)
  occupe légitimement plus que l'interligne, et la raboter décale sa baseline ;
* **abaisser à 2 mots** le seuil de détection des boîtes de ligne : deux mots
  aux bords identiques se produisent par hasard sur une page dense.

Les deux dernières méritent un mot, parce qu'elles semblaient évidentes :

* **la dérive d'ancre.** `_baseline_rows` laisse l'ancre suivre le dernier span
  ajouté. Sur un scan dense, une rangée absorbait 33 mots en dérivant de
  16,05 pt (deux lignes et demie). Borner la dérive coupe aussi les **vraies**
  rangées, qui se rattrapent en blocs plus petits et plus nombreux, donc plus
  souvent en collision. Le moteur PDF a été remis à l'identique ;
* **couper après coup** les 8 lignes manifestement fusionnées (hautes ET de
  plus de 4 mots) : même conclusion, aucun seuil ne fait mieux que l'absence
  de découpe.

## Le TEXTE ENROULÉ autour d'une image n'est pas un défaut (09/08)

**3 des 8 défauts restants n'en étaient pas** — le même piège que les colonnes,
une semaine plus tard. `_ligne_amputee` prend `min(fers)` comme fer du
paragraphe. Mais un texte qui contourne une image descend en marches puis
**revient au fer plein** une fois l'image passée :

```
bloc #12   L0 fer=94,6   L1 97,2   L2 97,7   L3 98,6   L4 38,6
                    (le camion)                      (image finie)
```

`min(fers)` vaut alors 38,6 et accuse L1-L3 de commencer 60 pt trop loin —
alors que leur texte est **parfaitement lu** (`'Il permet de conduire les
ensembles de véhicules'`).

Le discriminant ne se règle pas : une ligne réellement amputée a perdu son
début **au profit d'un autre bloc**, sur sa bande, à sa gauche. Mesuré :

| cas | voisin à gauche |
|---|---|
| vraie amputation (`'3'` expulsé de `'mètres'`) | **le bloc `'3'`** |
| lignes enroulées (#7, #12, #50) | **aucun** |

C'est la leçon de `ligne_partagee`, reprise mot pour mot : **le VIDE n'est pas
une preuve**. Seule la présence du début ailleurs en est une.

## Ce qui reste (5 défauts) — la suite du travail

Répartition **0 / 5 / 0** : pages 1 et 3 propres. Tout est page 2.

| famille | n | ce que c'est |
|---|---|---|
| `chevauchement` | 3 | sommaire + texte à contour |
| `blocs_colles` | 2 | cadres qui se frôlent (0,27 et 0,48 pt) |

Ils se concentrent sur **deux causes**, et non cinq :

* le **sommaire** (`'| Accidents Les statistiques,'` chevauche
  `"l'assurance, le constat"` sur 45,60 × 4,80 pt, et frôle
  `'e pratique et Index'` à 0,27 pt) — le titre reste soudé à son corps de
  texte. **Quatre pistes mesurées, quatre échecs**, détaillées dans
  `_debris_de_dessin` : le seul signal est la taille (9,12 vs 4,32) et aucun
  seuil ne survit au faux positif `L'arrêt et le stationnement / 81 à 96` ;
* le **texte à contour** (`'Le permisC'` chevauche `'Il permet de conduire'`
  sur 1,35 pt, et `'La signalisation verticale'` frôle ses voisins) — un titre
  et son corps que le regroupement laisse se toucher.

Le texte de ces blocs porte la signature du défaut : des mots collés sans
espace (`'AprèsdeuxansdepermisB'`, `'MA transportdemarchandises'`) trahissent
une ligne encore mal découpée en amont.

**Pistes non essayées** : lire par bandes horizontales, pour donner à Tesseract
un contexte de ligne franc dans les zones à interligne serré ; découper après
coup les lignes dont les boîtes se recouvrent encore.

## La leçon qui revient : LE COMPTEUR NE VOIT QUE CE QU'ON LUI A APPRIS

Trois fois de suite, un défaut réel est passé sous les indicateurs et a été
signalé **à l'œil** par l'utilisateur :

1. le sur-découpage des lignes justifiées — 6 blocs pour un paragraphe de 4
   lignes, **zéro** violation des invariants ;
2. les lignes partagées entre deux blocs — l'audit ne jugeait que les cadres,
   deux à deux, jamais leur contenu ligne par ligne ;
3. les cadres gonflés de 1 pt par l'aperçu — l'audit avait raison, c'est le
   **dessin** qui mentait.

D'où deux règles de travail :

* **toujours regarder l'aperçu**, un compte vert ne prouve rien ;
* **l'aperçu et l'audit doivent parler de la même géométrie**, sinon le
  jugement à l'œil ne vaut rien (gardé par test).

Et une règle sur les tests eux-mêmes : trois d'entre eux se sont révélés
**aveugles** — verts alors que le correctif qu'ils gardaient était neutralisé.
Un test se vérifie par mutation, sinon il ne protège rien.

## Où il tourne

**Dans Docker, et nulle part ailleurs** — il dépend du binaire Tesseract, que
le poste de développement n'a pas.

```bash
docker build -f backend/Dockerfile.ocr-banc -t precis-ocr-banc .
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -w /w precis-ocr-banc \
  python backend/scripts/apercu_identification.py --source tesseract \
  --langue fra --pages 3 --sans-lignes "backend/tests files/Code_de_la_Route.pdf" \
  ".ocr_out/reel.pdf"
```

Le banc minimal (~2 min de construction contre ~15 pour l'image complète)
installe le **même** binaire et les **mêmes** paquets Python ; il en retire
seulement ce que l'OCR n'appelle pas.

**Les tests tournent SANS Tesseract** (`test_ocr_contrat.py`, 132/132) : le
contrat de spans permet d'injecter des mots dont on connaît la vérité. Un test
qu'on ne peut pas lancer chez soi ne protège rien.
