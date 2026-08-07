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
| inclusions + croisements | **18** (était 80) |

Les deux premières à zéro depuis le début : le regroupement ne duplique jamais
un mot. Les 18 restantes sont **géométriques**, et réparties également
(6 / 6 / 6) — plus de page catastrophe.

## Les deux correctifs qui ont payé

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
| plafonner la `size` des spans | 1,0× → 2,0× médiane | 51/21/19/24, bruit |
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

## Ce qui reste, et où il faut chercher

15 des 22 violations impliquent une **ligne fusionnée**. Les deux leviers
géométriques évidents sont épuisés (tableau ci-dessus) : le prochain essai doit
porter sur la **LECTURE**, pas sur le regroupement.

La zone la plus atteinte est le texte qui **s'enroule autour d'une
illustration** — chaque ligne commence à un x différent, interligne ~6,5 pt.
Le texte y est parfaitement lisible à l'œil : ce n'est **pas** un problème de
contraste. Pistes non essayées : lire cette zone à un DPI supérieur, ou lire
par bandes horizontales pour donner à Tesseract un contexte de ligne franc.

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
