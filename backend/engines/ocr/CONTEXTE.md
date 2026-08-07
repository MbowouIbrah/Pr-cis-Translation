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

## La chaîne

```
scan ─▶ lecture.py ─▶ spans ─▶ _group_text_lines ─▶ _group_paragraphs ─▶ tri.py ─▶ apercu.py
                        │        (moteur PDF)          (moteur PDF)
                        └── contrat de 14 champs : spans.py
```

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

### L'état mesuré (3 pages du Code de la Route, vrai scan)

| | valeur |
|---|---|
| mot dans deux lignes | **0** |
| mot dans deux paragraphes | **0** |
| inclusions + croisements | **8** (était 80) |

Les deux premières à zéro depuis le début : le regroupement ne duplique jamais
un mot. Les 8 restantes sont **géométriques** (1 / 5 / 2).

## Les correctifs qui ont payé

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

Les deux dernières méritent un mot, parce qu'elles semblaient évidentes :

* **la dérive d'ancre.** `_baseline_rows` laisse l'ancre suivre le dernier span
  ajouté. Sur un scan dense, une rangée absorbait 33 mots en dérivant de
  16,05 pt (deux lignes et demie). Borner la dérive coupe aussi les **vraies**
  rangées, qui se rattrapent en blocs plus petits et plus nombreux, donc plus
  souvent en collision. Le moteur PDF a été remis à l'identique ;
* **couper après coup** les 8 lignes manifestement fusionnées (hautes ET de
  plus de 4 mots) : même conclusion, aucun seuil ne fait mieux que l'absence
  de découpe.

## Ce qui reste (8 violations)

Concentrées sur la page 2 (1 / 5 / 2). Le défaut visible à l'œil n'est plus le
sur-découpage mais le **chevauchement d'un interligne** entre deux blocs
voisins : ils se stratifient au lieu de s'empiler proprement.

⚠ **Les invariants ne mesurent PAS tout.** Le sur-découpage des lignes
justifiées — 6 blocs pour un paragraphe de 4 lignes — n'en produisait
**aucune**, alors que c'était le défaut le plus visible. Il a été signalé à
l'œil, pas par le compteur. Les invariants restent nécessaires et
**insuffisants** : toujours regarder l'aperçu.

Piste non essayée : lire par bandes horizontales pour donner à Tesseract un
contexte de ligne franc dans les zones à interligne serré.

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

**Les tests tournent SANS Tesseract** (`test_ocr_contrat.py`, 60/60) : le
contrat de spans permet d'injecter des mots dont on connaît la vérité. Un test
qu'on ne peut pas lancer chez soi ne protège rien.
