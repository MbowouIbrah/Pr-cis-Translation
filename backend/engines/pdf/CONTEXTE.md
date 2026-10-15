# Moteur PDF — contexte

Version **1.0.0** · `engines/pdf/` · **absent du registre**, appelé nommément

Le PDF n'a pas de texte : il a des **glyphes posés à des coordonnées**. Il n'y a
ni paragraphe, ni colonne, ni « zone de texte » à modifier. Tout ce que les
autres moteurs lisent dans le fichier, celui-ci doit le **déduire**.

C'est ce qui explique sa taille, sa forme, et pourquoi il ne ressemble à aucun
autre moteur du projet.

> À lire d'abord : [`../CONTEXTE.md`](../CONTEXTE.md) — la règle d'indépendance,
> les balises de runs, les invariants sans seuil.
>
> Référence détaillée du format JSON, de la CLI et de la détection de
> disposition : [`README.md`](README.md).

## Pourquoi il n'est pas au registre

Les moteurs Office honorent `extract_text` / `inject_translation` : deux temps,
tout le document. Le moteur PDF, lui, est **progressif par construction** —
chaque page est extraite, traduite **et rendue** avant qu'on passe à la
suivante. L'exécuteur l'appelle donc directement
(`from engines.pdf import stream`).

Cette asymétrie est réelle et assumée. La maquiller derrière une fausse
conformité au registre coûterait plus qu'elle ne rapporte.

## La chaîne

```
extract_page_data   glyphes → spans → lignes visuelles → paragraphes
      ↓
tagging             [[0]]…[[/0]] par SEGMENT DE STYLE, pas par run
      ↓
translate           lots vers DeepSeek + mémoire de document
      ↓
reflow              coule le traduit dans le conteneur d'origine
      ↓
render_page_into    repeint la page sur une feuille vierge
```

Le JSON est la **seule source** du rendu : rien n'est copié depuis le PDF
d'origine. C'est un test de fidélité permanent de l'extraction — si l'extraction
rate quelque chose, ça se voit immédiatement à l'écran.

## Ce qui rend ce moteur difficile

### Le vide n'est pas une preuve

Un blanc entre deux blocs de texte ne dit pas s'il sépare deux colonnes, deux
paragraphes, ou s'il est l'intérieur d'un texte justifié. Toutes les règles de
regroupement sont donc **relatives** — à la largeur de glyphe de la ligne, au
saut de ligne de base, à la taille de police — jamais absolues.

Un seuil en points fonctionne sur le document qui l'a inspiré et sur aucun
autre.

### Le conteneur a une hauteur fixe

Une traduction est plus longue que sa source, presque toujours. Mais le
paragraphe traduit doit tenir **exactement** là où était l'original, sinon la
page entière se décale.

D'où une cascade d'ajustements bornés, du moins au plus intrusif :

```
tracking (resserrement inter-lettres) → taille de police → interligne
```

plus une **césure syllabique** (pyphen) par ligne. En dessous d'un plancher de
**0,92×**, le modèle est resollicité pour une version compacte plutôt que de
comprimer visiblement : sous ce plancher, DeepSeek se met à abréger, et un texte
abrégé sans qu'on l'ait demandé est un texte faux.

### Les polices embarquées sont sous-ensemblées

Un PDF n'embarque que les glyphes qu'il utilise, et les polices **CID / Type0
(Identity-H)** le font sans table Unicode. Le moteur reconstruit cette table à
partir de `get_texttrace()` via fontTools.

Deux conséquences : une police assortie peut être **corrompue** (c'est arrivé
pour PT Serif — vérifier la densité d'encre par variante, pas seulement que le
fichier se charge), et le repli base-14 change la métrique, donc le reflow.

## Le piège le plus coûteux : la clé de cache

Le rendu est mis en cache sous une clé faite du nom, du hash du contenu, de la
langue, de la qualité et de la sélection de pages. **Aucune de ces cinq valeurs
ne bouge quand on corrige le moteur.**

Un document déjà traduit continuait donc à servir éternellement le PDF d'avant
le correctif. Le cache n'était pas de trop : il était **malhonnête** — il
prétendait que la sortie ne dépend que de l'entrée.

Et `v2_pages.json` ne stocke pas que la traduction : il stocke les **décisions
de géométrie** du moteur (`align`, `container_bbox`, une cinquantaine de fois
par page). Le rejouer, c'est rejouer la mise en page d'une version antérieure.

D'où `ENGINE_VERSION = f"v{__version__}"` — **dérivé** de la version, pour que
les deux ne puissent plus diverger, et présent dans le **nom** des rendus en
cache.

> **Tout correctif qui peut changer un pixel impose d'incrémenter le patch.**
> Ne pas le faire ne casse rien visiblement : ça resert un rendu périmé en
> silence. On croit avoir corrigé, l'utilisateur voit le contraire, et on
> cherche le défaut dans le moteur alors qu'il est dans la clé.
> C'est arrivé sur toute une campagne de correctifs (v21 → v22 oublié).

## Deux mémoires, deux rôles

* **mémoire de document** — un texte court déjà traduit dans ce document est
  resservi tel quel aux pages suivantes. Sans elle, le même intitulé de tableau
  était traduit différemment page 3 et page 40 ;
* **cache de reprise** (`cache_path`) — chaque page traduite y est écrite
  aussitôt. Un job interrompu reprend où il en était, sans repayer. Il sème
  aussi la mémoire de document, pour que la reprise garde les choix du premier
  passage.

Les écritures du PDF partiel sont **atomiques** (tmp + `os.replace`) : un client
qui télécharge pendant l'écriture ne voit jamais un fichier tronqué.

## Limites connues (assumées)

* **dégradés / shadings** (`sh`) non capturés ;
* **images pivotées** placées dans leur rectangle d'englobement, sans matrice ;
* **texte pivoté** rendu à sa baseline, sans rotation ;
* **clips et groupes** vectoriels ignorés ;
* **PDF scannés** : aucun OCR. On ne traduit jamais ce qu'on n'a pas lu — voir
  [`../../../docs/etude-ocr.md`](../../../docs/etude-ocr.md) ;
* une **colonne justifiée de ≤ 5 lignes** peut rester mal recollée.

## Vérifier

```bash
backend/venv/Scripts/python.exe backend/tests/test_engine_v2_generic.py
```

77 contrôles sur un PDF **synthétique**, généré par la suite elle-même. C'est
délibéré : un correctif prouvé sur le document qui l'a révélé est une
heuristique déguisée. Tout défaut trouvé sur un vrai document doit être
reproduit sur le synthétique avant d'être corrigé.
