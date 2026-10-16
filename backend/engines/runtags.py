"""Contrat des balises de RUNS — et sa réparation.

LE PROBLÈME
-----------
Un paragraphe PPTX est découpé en `runs` (`<a:r>`), un par changement de mise en
forme. Pour que la traduction retrouve la graisse, la taille et la couleur de
chaque morceau, l'extraction les balise :

    [[0]]2[[/0]][[1]]°[[/1]][[2]] JOUR DE FORMATION[[/2]]

Le modèle est censé rendre les mêmes balises. Il ne le fait pas toujours : il
lui arrive de rendre toute la traduction dans la première et d'oublier les
autres —

    [[0]]DAY 2 OF TRAINING[[/0]]

L'injection parcourait alors les runs et ne remplaçait que ceux qu'elle
trouvait dans la réponse. Les autres GARDAIENT LEUR TEXTE SOURCE, et le
document affichait la traduction COLLÉE à l'original :

    « DAY 2 OF TRAINING° JOUR DE FORMATION »        (slide 8, mesuré)
    « Subtitletitre » / « subtitulotitre »          (sous-titre, mesuré)

Ce n'est pas un défaut de traduction : le modèle avait bien traduit. C'est un
défaut de CONTRAT — personne ne vérifiait que la réponse avait la forme promise,
et le silence de l'injection transformait l'écart en texte bilingue affiché.

LA RÈGLE
--------
Aucun run ne conserve jamais son texte SOURCE. Si la réponse ne couvre pas tous
les runs, on RÉPARTIT la traduction reçue sur les runs d'origine — quitte à en
vider certains. Un run vide est invisible ; un run resté en français ne l'est
pas.

COMMENT RÉPARTIR
----------------
Deux cas, distingués par une question simple : les frontières de runs
tombent-elles entre les MOTS ?

  • OUI (« Note : » | « ceci est important ») — le découpage porte du sens et
    chaque morceau a sa mise en forme. On répartit les mots proportionnellement
    aux longueurs d'origine : chaque run garde son style sur sa part de phrase.

  • NON (« 2 » | « ° » | « JOUR… », ou « Sous- » | « titre ») — le découpage
    coupe AU MILIEU d'un mot ou d'un nombre. Il ne veut rien dire dans la langue
    cible : y répartir des mots produirait « Sub » + « title ». Toute la
    traduction va au run qui portait le PLUS de texte (donc la mise en forme
    dominante), les autres sont vidés.

C'est volontairement conservateur : on peut perdre une nuance de graisse sur un
paragraphe mal rendu, jamais afficher deux langues à la fois.
"""
from __future__ import annotations

import re
import unicodedata

_BALISE = re.compile(r"\[\[(\d+)\]\](.*?)\[\[/\1\]\]", re.DOTALL)


def parse(texte: str) -> dict[int, str]:
    """Contenu de chaque balise, par index."""
    return {int(i): t for i, t in _BALISE.findall(texte or "")}


def sans_balises(texte: str) -> str:
    """Le texte NU. On retire les balises appariées, puis toute balise
    orpheline : un `[[1]]` sans fermeture ne doit jamais atteindre la diapo."""
    nu = _BALISE.sub(lambda m: m.group(2), texte or "")
    return re.sub(r"\[\[/?\d+\]\]", "", nu)


def conforme(nb_runs: int, traduit: str) -> bool:
    """La réponse couvre-t-elle exactement les runs attendus ?"""
    return set(parse(traduit)) >= set(range(nb_runs))


def _alignee_sur_les_mots(parts: list[str]) -> bool:
    """Chaque frontière entre deux runs tombe-t-elle entre deux mots ?

    Une frontière est propre si le run qui précède finit par une espace ou si
    celui qui suit commence par une espace. « Sous- » | « titre » ne l'est pas :
    la coupure est au milieu d'un mot, et répartir des mots dessus n'aurait
    aucun sens dans la langue cible.
    """
    for avant, apres in zip(parts, parts[1:]):
        if not (avant.endswith((" ", " ", "\t")) or
                apres.startswith((" ", " ", "\t"))):
            return False
    return True


def _nu(texte: str) -> str:
    """Forme comparable : sans casse, sans accents, espaces normalisées.

    Comparer brut ne suffit pas : le modèle recopie souvent le morceau non
    traduit en changeant la casse (« ° JOUR DE FORMATION » rendu « ° jour de
    formation »), ce qui suffisait à le faire passer pour une traduction.
    """
    d = unicodedata.normalize("NFKD", texte or "")
    d = "".join(c for c in d if not unicodedata.combining(c))
    return " ".join(d.lower().split())


_CHIFFRES = re.compile(r"\d+")


def runs_recopies(parts: list[str], rendus: list[str]) -> list[int]:
    """Indices des runs rendus IDENTIQUES à leur source, alors qu'un autre run
    du même paragraphe, lui, a changé.

    Égalité stricte (à la casse et aux accents près), rien d'autre. Pas de
    compte de mots, pas de rapport de longueurs : ces seuils-là ne peuvent être
    choisis qu'en regardant un document précis, et un seuil calé sur un document
    est faux sur le suivant.

    La condition « un autre run a changé » n'est pas un réglage, c'est une
    définition : si RIEN n'a changé dans le paragraphe, il n'y a pas de mélange
    de langues à signaler — c'est le cas normal d'un nom propre, d'une date ou
    d'un sigle, qui se traduisent par eux-mêmes.

    ⚠ CE QUE CETTE FONCTION N'AUTORISE PAS : supprimer quoi que ce soit. Un run
    identique à sa source est peut-être un oubli de traduction, peut-être un nom
    propre — et RIEN dans le texte ne permet de trancher. Une version antérieure
    a cru pouvoir, et a effacé des noms de personnes (« INDIVIDUAL TRAINING
    RESULT: Inoc Rodrigues Franca e Almeida » réduit à « INDIVIDUAL TRAINING
    RESULT: »). Le seul usage légitime est de REDEMANDER au modèle : lui seul
    sait si « Almeida » se traduit. Un faux positif coûte alors un appel, jamais
    du contenu.
    """
    if len(parts) != len(rendus) or len(parts) < 2:
        return []
    identiques = [i for i in range(len(parts)) if _nu(parts[i]) == _nu(rendus[i])]
    if len(identiques) == len(parts):
        return []                      # rien n'a changé : pas de mélange
    # Un run SANS LETTRE (« • », « 2026 | », « – ») n'a rien à traduire : sa
    # recopie est la seule réponse juste, pas un mélange de langues. Le signaler
    # renvoyait au modèle chaque ligne à puce d'un CV, pour rien.
    return [i for i in identiques if any(c.isalpha() for c in parts[i])]


def nombres(texte: str) -> list[str]:
    """Suites de chiffres du texte, dans l'ordre."""
    return _CHIFFRES.findall(texte or "")


def _ouvertures(texte):
    """Parenthèses OUVRANTES (« ( », « （ »), hors marque d'écriture
    inclusive collée au mot (« étudiant(e)s ») qui n'ajoute aucun sens."""
    t = sans_balises(texte).replace("（", "(")
    return t.count("(") - len(re.findall(r"[a-zà-ÿ]\([a-zà-ÿ]{1,3}\)", t))


def defauts(source: str, traduit: str) -> list[str]:
    """Ce qui cloche dans une traduction, sans jamais regarder QUEL document.

    Quatre invariants, aucun réglage :

      • `balises`  — autant de morceaux rendus que reçus. Un morceau sans
        réponse est un morceau non traduit ; il ne se devine pas.
      • `recopie`  — un morceau identique à sa source à côté d'un morceau
        traduit. Mélange de langues probable.
      • `nombres`  — les suites de chiffres de la source se retrouvent dans la
        traduction. « 6ème jour » rendu « 5.e jour » change une donnée du
        document : c'est une faute d'une autre nature qu'une maladresse de
        style, et elle est invisible à la relecture d'un lecteur qui ne lit que
        la langue cible.
      • `ajout`   — plus de parenthèses dans la traduction que dans la
        source : une glose (« Probatoire (équivalent des AS-levels) ») que
        personne n'a écrite. Compte brut, aucun seuil.

    Renvoie la liste des défauts constatés — à charge de l'appelant de
    REDEMANDER. Rien ici ne corrige, rien ici ne supprime.
    """
    attendus = parse(source)
    trouves = parse(traduit)
    maux = []

    if attendus and not set(trouves) >= set(attendus):
        maux.append("balises")
    elif attendus:
        parts = [attendus[i] for i in sorted(attendus)]
        rendus = [trouves[i] for i in sorted(attendus)]
        if runs_recopies(parts, rendus):
            maux.append("recopie")

    if sorted(nombres(sans_balises(source))) != \
            sorted(nombres(sans_balises(traduit))):
        maux.append("nombres")

    if _ouvertures(traduit) > _ouvertures(source):
        maux.append("ajout")

    return maux


_MOT = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def termes_incoherents(paires) -> dict[str, int]:
    """Termes que le document traduit ICI et laisse en langue source LÀ.

    LE DÉFAUT. Sur une diapositive, « le fonctionnement du Gerbeur » est rendu
    « the operation of the Gerbeur » — le mot reste français. Sur une autre, le
    MÊME mot devient « Stacker » (« Moving the Stacker outside the container »).
    Le contrat de balises est parfaitement respecté dans les deux cas : rien, du
    point de vue de la FORME, ne cloche. C'est la COHÉRENCE du document qui est
    rompue, et c'est invisible à qui ne lit que la langue cible.

    LA PREUVE VIENT DU DOCUMENT, PAS D'UNE LISTE. On ne peut pas savoir dans
    l'absolu si « Gerbeur » se traduit — c'est peut-être une marque. Mais si le
    document lui-même l'a traduit UNE FOIS, la question est tranchée : le mot est
    traduisible, et les endroits où il survit sont des oublis.

    Symétriquement, un mot qui survit PARTOUT (« CFAO », « Emitério », un sigle)
    n'est jamais signalé : le document n'a produit aucune preuve contre lui.

    Aucun seuil, aucune liste de termes, rien à calibrer : c'est le même
    document qui accuse et qui disculpe.

    ⚠ CE QUE ÇA NE SAIT PAS FAIRE. Les COGNATS passent pour des oublis :
    « motivation », « progression », « identification » s'écrivent pareil en
    français et en anglais, donc ils « survivent » alors que la traduction est
    juste. Mesuré sur un document réel : 23 fragments signalés sur 196, dont une
    majorité de cognats. Les écarter demanderait un dictionnaire — le document
    seul ne suffit pas, et j'ai vérifié qu'aucun signal interne ne les distingue
    de façon fiable.

    C'est pourquoi cette fonction ne conclut à RIEN : elle prépare une question
    posée au modèle, qui est le seul à savoir que « progression » se dit ainsi
    dans les deux langues et que « Gerbeur » se dit « Stacker ». La redemande
    doit donc être formulée pour qu'un cognat reste INCHANGÉ sans dommage.

    `paires` : itérable de (texte_source, texte_traduit), balisés ou non.
    Renvoie {mot_source_normalisé: nombre d'endroits où il a été traduit}.
    """
    survit: dict[str, int] = {}
    traduit: dict[str, int] = {}
    for source, cible in paires:
        src = _nu(sans_balises(source))
        tgt = set(_MOT.findall(_nu(sans_balises(cible or ""))))
        if not src or not tgt:
            continue
        # Comparaison MOT À MOT, jamais par sous-chaîne : « les » se trouve dans
        # « rules », « son » dans « person », « par » dans « part ». Cherchée en
        # sous-chaîne, la moitié des mots outils du français paraissaient
        # « survivre » dans une traduction anglaise parfaitement correcte.
        for mot in set(_MOT.findall(src)):
            if mot in tgt:
                survit[mot] = survit.get(mot, 0) + 1
            else:
                traduit[mot] = traduit.get(mot, 0) + 1
    return {m: traduit[m] for m in survit if m in traduit}


def termes_a_reprendre(source: str, cible: str,
                       incoherents: dict[str, int]) -> list[str]:
    """Les termes incohérents que CE fragment laisse en langue source."""
    src = _MOT.findall(_nu(sans_balises(source)))
    tgt = set(_MOT.findall(_nu(sans_balises(cible or ""))))
    return sorted({m for m in src if m in incoherents and m in tgt})


def _concentrer(parts: list[str], plein: str) -> list[str]:
    """Tout le texte au run qui portait le plus de source, les autres vidés.

    Dernier recours, quand le découpage en runs n'a aucun sens dans la langue
    cible (« Sous- » | « titre »). On perd la nuance de mise en forme entre les
    morceaux — mais on ne peut pas faire mieux sans inventer, et un run resté en
    langue source serait pire.
    """
    dominant = max(range(len(parts)), key=lambda i: len(parts[i].strip()))
    return [plein if i == dominant else "" for i in range(len(parts))]


def repartir(parts: list[str], traduit: str) -> list[str]:
    """Le texte à mettre dans CHAQUE run, un par run source.

    `parts` : les textes SOURCE des runs, dans l'ordre.
    `traduit` : ce que le modèle a rendu, balisé ou non.

    Le résultat a toujours exactement `len(parts)` éléments — c'est ce qui
    garantit qu'aucun run ne reste en langue source.
    """
    if not parts:
        return []

    trouve = parse(traduit)

    if set(trouve) >= set(range(len(parts))):
        # Le contrat est rempli : on rend la réponse TELLE QUELLE.
        #
        # Même si un run y semble recopié de la source. On ne peut pas le
        # trancher ici : « Almeida » identique à sa source est un nom propre,
        # « JOUR DE FORMATION » identique est un oubli, et rien dans le texte ne
        # les distingue. Ce jugement appartient au modèle, qu'on redemande à la
        # traduction (`defauts` → nouvelle passe). La réinjection, elle, ne
        # supprime jamais un texte que quelqu'un a écrit.
        return [trouve[i] for i in range(len(parts))]

    # ── Réponse INCOMPLÈTE ───────────────────────────────────────────────
    plein = sans_balises(traduit).strip()
    if len(parts) == 1:
        return [plein]

    if trouve:
        # Le modèle a répondu pour CERTAINS runs. On garde ses réponses là où il
        # en a donné une, et on VIDE les autres : jamais de texte source.
        #
        # C'est volontairement le minimum. Une version précédente redistribuait
        # la phrase entière sur les runs source, et sur un paragraphe où le
        # modèle avait rendu 5 balises pour 7 runs, elle a tout entassé dans le
        # DERNIER run en vidant les six autres (slide 19, mesuré). Le texte lu
        # était le même, mais le nom propre en gras des runs 1 et 4 avait perdu
        # sa mise en forme. Deviner une répartition coûtait plus cher que de ne
        # pas deviner : la mise en forme est ce qu'on vend.
        if not _alignee_sur_les_mots(parts):
            return [trouve.get(i, "") for i in range(len(parts))]
        # Découpage aligné sur les mots : là, répartir a un sens et préserve la
        # mise en forme de chaque morceau (« Note : » en gras + le reste).

    if not _alignee_sur_les_mots(parts):
        return _concentrer(parts, plein)

    mots = plein.split()
    if len(mots) < len(parts):
        # Moins de mots que de runs : on ne peut pas en donner un à chacun sans
        # couper des mots. Même repli que ci-dessus.
        return _concentrer(parts, plein)

    # Répartition proportionnelle aux longueurs SOURCE, au mot près. Chaque run
    # reçoit au moins un mot (garanti par le test ci-dessus), et le dernier
    # ramasse le reste — aucun mot ne se perd.
    total = sum(len(p) for p in parts) or 1
    sortie: list[str] = []
    curseur = 0
    for i, p in enumerate(parts):
        if i == len(parts) - 1:
            part_mots = mots[curseur:]
        else:
            reste_runs = len(parts) - i - 1
            n = round(len(mots) * len(p) / total)
            n = max(1, min(n, len(mots) - curseur - reste_runs))
            part_mots = mots[curseur:curseur + n]
            curseur += n
        sortie.append(" ".join(part_mots))

    # Espaces de jonction : sans elles, « Note : » + « ceci » deviendrait
    # « Note :ceci ». On rend l'espace au run qui la portait dans la source.
    for i in range(len(sortie) - 1):
        if sortie[i] and sortie[i + 1]:
            if parts[i].endswith((" ", " ", "\t")):
                sortie[i] += " "
            elif parts[i + 1].startswith((" ", " ", "\t")):
                sortie[i + 1] = " " + sortie[i + 1]
    return sortie


# Redemande de cohérence (PPTX et PDF) : écrite pour qu'un COGNAT reste
# inchangé sans dommage et que seule la portion fautive soit reprise.
CONSIGNE_COHERENCE = (
    "COHÉRENCE DU DOCUMENT. Dans ta traduction ci-dessous, ces termes sont "
    "restés identiques à la source : {termes}. Ailleurs dans le MÊME document, "
    "tu les as traduits. Reprends ta traduction en ne changeant QUE ce qui "
    "concerne ces termes — garde le reste MOT POUR MOT.\n"
    "Si l'un d'eux s'écrit de la même façon dans la langue cible, ou s'il "
    "s'agit d'un nom propre, d'une marque ou d'un sigle, LAISSE-LE TEL QUEL : "
    "c'est légitime, et le changer serait une faute.\n"
    "Ta traduction précédente : {precedente}"
)
