"""Moteur XLSX — SQUELETTE.

CE QUE CE FICHIER EST, ET CE QU'IL N'EST PAS
--------------------------------------------
Il est le CHEMIN complet : un `.xlsx` est accepté, décompressé, parcouru, ses
chaînes relevées et balisées, la traduction réinjectée, le classeur re-zippé.
Un fichier passe de bout en bout et ressort ouvrable par Excel.

Il n'est PAS la couverture complète du format. Excel range du texte à une
dizaine d'endroits (voir `CONTEXTE.md`) ; ce squelette en traite deux — les
chaînes partagées et les chaînes en ligne. Les autres sont recensées, nommées,
et laissées en place : `_PARTIES` liste ce qui reste à faire, et chaque manque
y porte son motif.

C'est délibéré. Un squelette qui prétend tout couvrir est pire qu'un squelette :
il fait croire le travail fini, et le trou se découvre chez l'utilisateur.

CE QUI EST DÉJÀ JUSTE ET NE DOIT PAS BOUGER
-------------------------------------------
* le contrat de balises `[[n]]…[[/n]]` — commun à tous les moteurs ;
* le refus de traduire un NOMBRE : « 1 234,50 » traduit devient une cellule
  cassée, et une formule qui la lit se casse avec elle ;
* le refus de toucher aux FORMULES : une formule est du code. Traduire « SUM »
  produit `#NAME?`, et l'erreur se propage à toute la feuille ;
* une instance par opération (`self.temp_dir`), comme les autres moteurs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

from lxml import etree

from engines.base import TranslationEngine

#: Espaces de noms SpreadsheetML.
NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}

#: Où Excel range du texte, et ce que le squelette en fait.
#:
#: Cette table est le PLAN DE TRAVAIL. Chaque entrée non traitée dit pourquoi
#: elle compte — sans quoi la prochaine personne la traitera par ordre
#: alphabétique plutôt que par importance.
_PARTIES: dict[str, tuple[bool, str]] = {
    "xl/sharedStrings.xml":
        (True, "Le gros du texte. Excel déduplique les chaînes des cellules "
               "ici et n'y laisse qu'un index dans la feuille."),
    "xl/worksheets/sheet*.xml (t=\"inlineStr\")":
        (True, "Chaînes écrites DANS la cellule au lieu du magasin partagé. "
               "Les fichiers produits par un export automatique n'utilisent "
               "souvent que cette forme — les ignorer, c'est ne rien traduire "
               "d'un classeur entier."),
    "xl/workbook.xml (noms d'onglets)":
        (True, "Nom VISIBLE et référencé par les formules (`=Feuil1!A1`). "
               "Renommage et réécriture des références (formules, noms "
               "définis, plages 3D) faits ENSEMBLE : séparés, ils laisseraient "
               "un classeur d'apparence traduite dont tous les calculs sont "
               "morts. Un nom qu'Excel refuserait, ou qui entrerait en "
               "collision, n'est pas appliqué — l'onglet garde le sien."),
    "xl/charts/chart*.xml":
        (True, "Titres, noms d'axes, étiquettes. La logique du PPTX ne se "
               "recopie PAS telle quelle : dans un classeur, un `<c:v>` sous "
               "un cache est le reflet d'une cellule DÉJÀ traduite via "
               "sharedStrings. Le traduire à nouveau donnerait deux "
               "formulations pour la même donnée, et Excel réécrit ce cache "
               "au premier rafraîchissement. On traduit le texte riche "
               "(`<a:t>`) et les `<c:v>` SANS cache — le texte en dur."),
    "xl/drawings/drawing*.xml":
        (False, "Zones de texte et formes posées sur la feuille."),
    "xl/comments*.xml / xl/threadedComments/*":
        (False, "Commentaires. Deux formats coexistent : l'ancien (comments) "
                "et le moderne (threadedComments)."),
    "xl/tables/table*.xml":
        (False, "En-têtes de tableaux structurés — visibles, et cités par les "
                "formules en références structurées."),
    "xl/pivotCache/* et xl/pivotTables/*":
        (False, "Tableaux croisés dynamiques : les libellés y sont DUPLIQUÉS "
                "entre le cache et la table. En traduire un seul des deux "
                "désaligne le croisé au premier rafraîchissement."),
    "xl/styles.xml (formats de nombre personnalisés)":
        (False, "Un format peut contenir du texte littéral — `#\\ ##0\\ \"F "
                "CFA\"`. C'est du visible, et c'est piégeux : la syntaxe du "
                "format doit rester intacte autour du mot."),
    "docProps/core.xml":
        (False, "Titre et sujet du document. Visibles dans les propriétés, "
                "rarement décisifs — à faire en dernier."),
}


def est_numerique(valeur: str) -> bool:
    """La chaîne est-elle un nombre pur ?

    On ne traduit JAMAIS un nombre. « 1 234,50 » passé au modèle revient
    parfois en « 1,234.50 », parfois en toutes lettres : la cellule cesse d'être
    numérique et toute formule qui la lit se casse.
    """
    try:
        float(valeur.strip().replace(",", ".").replace(" ", "")
              .replace(" ", "").replace("%", ""))
        return True
    except ValueError:
        return False


#: Caractères qu'Excel INTERDIT dans un nom d'onglet, plus l'apostrophe qui
#: sert de délimiteur. Un nom traduit qui en contiendrait rendrait le classeur
#: illisible — on refuse alors la traduction et on garde le nom d'origine.
_CAR_INTERDITS_ONGLET = set(":\\/?*[]'")
#: Longueur maximale d'un nom d'onglet, imposée par Excel.
_LONGUEUR_MAX_ONGLET = 31


def nom_onglet_valide(nom: str) -> bool:
    """Excel accepterait-il ce nom d'onglet ?

    Cinq règles, toutes du format et non de ce projet : non vide, 31
    caractères au plus, aucun de `: \\ / ? * [ ]`, pas d'apostrophe (elle
    délimite les noms dans les références), et pas d'apostrophe en tête ou en
    queue.

    Un nom refusé n'est pas une erreur : c'est un nom qu'on NE TRADUIT PAS. Le
    classeur garde alors son onglet d'origine, ce qui est toujours préférable
    à un fichier qu'Excel refuse d'ouvrir.
    """
    n = (nom or "").strip()
    if not n or len(n) > _LONGUEUR_MAX_ONGLET:
        return False
    if any(c in _CAR_INTERDITS_ONGLET for c in n):
        return False
    return not (n.startswith("'") or n.endswith("'"))


def _citation(nom: str) -> str:
    """Le nom tel qu'il s'écrit DANS une référence.

    Excel entoure d'apostrophes tout nom qui n'est pas un simple identifiant,
    et double les apostrophes internes. On ne produit jamais de nom à
    apostrophe (cf. `nom_onglet_valide`), mais on doit savoir en LIRE.
    """
    if re.fullmatch(r"[A-Za-z_À-ɏ][A-Za-z0-9_.À-ɏ]*", nom):
        return nom
    return "'" + nom.replace("'", "''") + "'"


def reecrire_references(formule: str, renommage: dict[str, str]) -> str:
    """Remplace les noms d'onglets cités par une formule ou un nom défini.

    POURQUOI CE N'EST PAS UN `str.replace`
    --------------------------------------
    Un remplacement naïf casse le classeur de quatre façons, toutes réelles :

      · « Ventes » remplacerait aussi le mot dans la chaîne littérale
        `="Ventes du mois"` — on ne touche qu'à ce qui PRÉCÈDE un « ! » ;
      · un onglet nommé « Ventes » et un autre « Ventes2 » se confondraient ;
      · `[1]Ventes!A1` désigne un onglet d'un classeur EXTERNE, que nous ne
        traduisons pas — le préfixe `[n]` protège donc la référence ;
      · `'Chiffre d''affaires'!A1` porte des apostrophes doublées ; les ignorer
        couperait le nom en deux.

    La plage 3D `Ventes:Synthese!A1` cite DEUX onglets d'un coup : les deux
    sont réécrits.

    Les chaînes littérales sont mises à l'abri AVANT toute réécriture, puis
    remises telles quelles : c'est le seul moyen sûr de ne pas traduire du
    texte qui ressemble à une référence.
    """
    if not formule or not renommage:
        return formule

    # 1. Mettre les chaînes littérales de côté (`"…"`, apostrophes doublées
    #    à l'intérieur d'Excel : `""`).
    litterales: list[str] = []

    def _garer(m):
        litterales.append(m.group(0))
        return f"\x00{len(litterales) - 1}\x00"

    sans_texte = re.sub(r'"(?:[^"]|"")*"', _garer, formule)

    # 2. Réécrire les références. Le motif capture soit un nom entre
    #    apostrophes, soit un identifiant nu, suivi de « ! » ou de « : ».
    def _remplacer(m):
        prefixe = m.group("ext") or ""
        if prefixe:
            return m.group(0)          # classeur externe : intouchable
        brut = m.group("nom")
        if brut.startswith("'") and brut.endswith("'"):
            nom = brut[1:-1].replace("''", "'")
        else:
            nom = brut
        cible = renommage.get(nom)
        if cible is None:
            return m.group(0)
        return _citation(cible) + m.group("fin")

    motif = re.compile(
        r"(?P<ext>\[\d+\])?"
        r"(?P<nom>'(?:[^']|'')+'|[A-Za-z_À-ɏ][A-Za-z0-9_.À-ɏ]*)"
        r"(?P<fin>\s*[!:])")
    reecrit = motif.sub(_remplacer, sans_texte)

    # 3. Remettre les chaînes littérales.
    return re.sub(r"\x00(\d+)\x00",
                  lambda m: litterales[int(m.group(1))], reecrit)


def _baliser(morceaux: list) -> str:
    """Balise les `<t>` d'une entrée — ou rend une chaîne vide si rien à dire.

    LA DÉCISION SE PREND SUR L'ENTRÉE ENTIÈRE, PAS SUR CHAQUE MORCEAU
    ----------------------------------------------------------------
    Une cellule mise en forme est découpée en plusieurs `<t>` : « Total » en
    gras, puis « 2026 » en maigre. Écarter les morceaux numériques UN À UN
    laissait passer « Total » seul — et comme aucun nœud ne conserve son texte
    source à l'injection, le « 2026 » du classeur était EFFACÉ. Une cellule
    perdait la moitié de son contenu, sans la moindre erreur.

    Trouvé par le classeur SYNTHÉTIQUE de `test_xlsx_squelette.py`, pas par un
    fichier réel : la cellule à deux graisses est rare, et le défaut n'apparaît
    que là. C'est exactement pourquoi la preuve se fait sur du synthétique.

    La règle correcte : si le texte COMPLET de la cellule est un nombre, on n'y
    touche pas. Sinon tous ses morceaux sont balisés — y compris ceux qui
    ressemblent à des nombres, puisqu'ils appartiennent à une phrase.
    """
    textes = [(t.text or "") for t in morceaux]
    complet = "".join(textes)
    if not complet.strip() or est_numerique(complet):
        return ""
    return "".join(f"[[{i}]]{txt}[[/{i}]]" for i, txt in enumerate(textes))


class XLSXTranslatorEngine(TranslationEngine):
    """Moteur XLSX — UNE INSTANCE PAR OPÉRATION, JAMAIS PARTAGÉE.

    Même règle que les moteurs PPTX et DOCX, et pour la même raison : tout
    l'état tient dans `self.temp_dir`, et deux opérations qui le partagent
    écrivent dans les mêmes fichiers. Voir `engines/CONTEXTE.md`.
    """

    extension = "xlsx"

    def __init__(self):
        self.temp_dir = None

    # ── Dossier de travail ────────────────────────────────────────────────
    def _dossier(self) -> Path:
        if self.temp_dir is None:
            self.temp_dir = tempfile.mkdtemp(prefix="precis_xlsx_")
        return Path(self.temp_dir)

    def _nettoyer(self) -> None:
        if self.temp_dir and os.path.isdir(self.temp_dir):
            # `ignore_errors` : sous Windows, un fichier encore ouvert par un
            # antivirus fait échouer la suppression et transformerait une
            # traduction RÉUSSIE en échec. Le dossier temporaire sera balayé.
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = None

    def _ouvrir(self, chemin_xlsx: str) -> Path:
        dossier = self._dossier()
        with zipfile.ZipFile(chemin_xlsx, "r") as z:
            z.extractall(dossier)
        return dossier

    def _refermer(self, chemin_sortie: str) -> None:
        """Re-zippe le dossier de travail.

        `ZIP_DEFLATED` et l'ordre de parcours n'ont pas d'importance pour Excel,
        qui lit le ZIP par son index. Ce qui compte, c'est que `[Content_Types]
        .xml` soit présent — il l'est, puisqu'on n'enlève jamais rien.
        """
        dossier = self._dossier()
        with zipfile.ZipFile(chemin_sortie, "w", zipfile.ZIP_DEFLATED) as z:
            for racine, _, fichiers in os.walk(dossier):
                for nom in fichiers:
                    complet = Path(racine) / nom
                    z.write(complet, complet.relative_to(dossier).as_posix())

    # ── Feuilles, et classeur PARTIEL (aperçu progressif) ─────────────────
    def feuilles(self, chemin_xlsx: str | None = None) -> list[dict]:
        """Les feuilles du classeur, dans l'ordre où Excel les affiche.

        Rend `[{"index", "nom", "sheet_id", "r_id", "cible"}]`. L'ORDRE est
        celui de `<sheets>` dans `workbook.xml`, et c'est le seul qui vaille :
        le numéro du fichier `sheet3.xml` ne dit RIEN de sa position — un
        classeur dont on a déplacé les onglets garde les noms de fichiers
        d'origine. Se fier à `sheetN.xml` afficherait les feuilles dans le
        désordre, ce que l'utilisateur verrait immédiatement.

        La CIBLE (le fichier réel) se lit dans `xl/_rels/workbook.xml.rels`.
        Beaucoup de classeurs minimalistes n'ont pas ce fichier : on retombe
        alors sur `sheet{sheetId}.xml`, faute de mieux, plutôt que de refuser
        le classeur.
        """
        dossier = self._ouvrir(chemin_xlsx) if chemin_xlsx else self._dossier()

        cibles: dict[str, str] = {}
        rels = dossier / "xl" / "_rels" / "workbook.xml.rels"
        if rels.exists():
            arbre = etree.parse(str(rels))
            for rel in arbre.getroot():
                rid = rel.get("Id")
                cible = (rel.get("Target") or "").lstrip("/")
                if rid and cible:
                    # Les cibles sont relatives à `xl/`. Une cible absolue
                    # (« /xl/worksheets/sheet1.xml ») existe aussi : le
                    # `lstrip` ci-dessus l'a déjà ramenée à la même forme.
                    cibles[rid] = (cible if cible.startswith("xl/")
                                   else f"xl/{cible}")

        wb = dossier / "xl" / "workbook.xml"
        if not wb.exists():
            return []
        arbre = etree.parse(str(wb))
        out = []
        for i, sh in enumerate(arbre.getroot().xpath(".//s:sheet",
                                                     namespaces=NS)):
            rid = sh.get(f"{{{NS['r']}}}id") or ""
            sid = sh.get("sheetId") or str(i + 1)
            out.append({
                "index": i,
                "nom": sh.get("name") or f"Feuille{i + 1}",
                "sheet_id": sid,
                "r_id": rid,
                "cible": cibles.get(rid, f"xl/worksheets/sheet{sid}.xml"),
            })
        return out

    def chaines_par_feuille(self, chemin_xlsx: str | None = None
                            ) -> dict[int, set[str]]:
        """Quelles chaînes chaque feuille AFFICHE — `{index_feuille: {id, …}}`.

        POURQUOI CETTE CARTE EXISTE
        ---------------------------
        Une chaîne du magasin partagé ne SAIT PAS à quelle feuille elle
        appartient, et c'est volontaire : Excel y déduplique le texte de tout
        le classeur, si bien qu'une même entrée peut servir dix feuilles. Le
        relevé ne peut donc pas porter cette information, et l'aperçu
        progressif — qui veut savoir quand une feuille est prête — en a
        pourtant besoin.

        On la reconstruit dans l'autre sens : on lit chaque feuille et on note
        les index qu'elle CITE (`<c t="s"><v>3</v></c>`). Une même chaîne
        apparaît alors dans plusieurs feuilles, ce qui est la réalité et non un
        défaut : cette chaîne n'est traduite qu'une fois, mais elle rend
        plusieurs feuilles complètes à la fois.

        Les chaînes EN LIGNE, elles, appartiennent à une feuille et à une
        seule : leur identifiant porte déjà son nom de fichier.
        """
        dossier = self._ouvrir(chemin_xlsx) if chemin_xlsx else self._dossier()
        carte: dict[int, set[str]] = {}
        for f in self.feuilles():
            chemin = dossier / f["cible"]
            ids: set[str] = set()
            if chemin.exists():
                arbre = etree.parse(str(chemin))
                for c in arbre.getroot().xpath(".//s:c[@t='s']", namespaces=NS):
                    v = c.find(f"{{{NS['s']}}}v")
                    if v is not None and (v.text or "").strip().isdigit():
                        ids.add(f"ss_{int(v.text.strip())}")
                stem = chemin.stem
                for c in arbre.getroot().xpath(".//s:c[@t='inlineStr']",
                                               namespaces=NS):
                    ref = c.get("r") or ""
                    ids.add(f"inline_{stem}_{ref}")
            carte[f["index"]] = ids
        return carte

    def injecter_partiel(self, traductions: dict) -> None:
        """Réinjecte DANS LE DOSSIER DE TRAVAIL, sans refermer le classeur.

        `inject_translation` fait tout d'un bloc — ouvrir, injecter, refermer,
        nettoyer — ce qui convient au fichier final mais pas à l'aperçu, qui
        doit réinjecter plusieurs fois de suite puis construire un partiel à
        partir de l'état courant.

        Ne rend rien et ne referme rien : l'appelant reste maître du dossier.
        """
        dossier = self._dossier()
        self._injecter_noms_onglets(dossier, traductions)
        self._injecter_chaines_partagees(dossier, traductions)
        self._injecter_chaines_en_ligne(dossier, traductions)
        self._injecter_graphiques(dossier, traductions)

    def build_partial_xlsx(self, output_path: str,
                           only_sheets: set[int] | None = None) -> None:
        """Écrit un classeur ne contenant QUE les feuilles demandées.

        `only_sheets` : les INDEX (0-basés) des feuilles à garder, au sens de
        l'ordre d'affichage rendu par `feuilles()`. `None` = tout garder.

        C'est le pendant XLSX de `build_partial_pptx`, et il en reprend les
        deux règles durement acquises :

        NON DESTRUCTIF. Le dossier temporaire n'est JAMAIS modifié : les
        fichiers de contrôle amputés sont calculés EN MÉMOIRE et écrits
        directement dans le zip. La méthode est appelée une fois par feuille
        pendant l'aperçu, puis une dernière fois pour le fichier final ; une
        version qui retirerait les feuilles du dossier laisserait le classeur
        final amputé de tout sauf la première.

        ÉCRITURE ATOMIQUE (fichier temporaire + remplacement) : le convertisseur
        d'aperçu lit ce fichier pendant qu'on l'écrit, et ne doit jamais tomber
        sur un zip à moitié rempli.

        CE QU'ON N'ENLÈVE PAS, ET POURQUOI
        ----------------------------------
        Seuls les `worksheets/sheetN.xml` non retenus sont exclus, plus leurs
        rels. Tout le reste est copié tel quel — styles, magasin partagé,
        thème, images. On pourrait élaguer davantage ; ce serait une erreur :

          · `sharedStrings.xml` est indexé par POSITION. En retirer les entrées
            d'une feuille écartée décalerait tous les index suivants, et les
            cellules des feuilles GARDÉES afficheraient le mauvais texte.
          · `styles.xml` est indexé pareil. Un style retiré, et toute la mise
            en forme glisse d'un cran.

        Le gain d'un tel élagage serait quelques kilo-octets ; le risque est un
        aperçu qui montre autre chose que le document. Mesuré côté PPTX :
        élaguer les médias d'une diapositive isolée ne gagne RIEN, LibreOffice
        ne lisant pas ce que rien ne référence. La même logique vaut ici.
        """
        dossier = self._dossier()
        toutes = self.feuilles()
        garder = ({s["index"] for s in toutes} if only_sheets is None
                  else {i for i in only_sheets})
        # Au moins UNE feuille : un classeur vide n'est pas ouvrable, et
        # LibreOffice rendrait une page blanche au lieu de signaler l'erreur.
        if not garder & {s["index"] for s in toutes}:
            garder = {toutes[0]["index"]} if toutes else set()

        gardees = [s for s in toutes if s["index"] in garder]
        cibles_gardees = {s["cible"] for s in gardees}
        cibles_toutes = {s["cible"] for s in toutes}
        exclues = cibles_toutes - cibles_gardees

        # Les rels des feuilles écartées (xl/worksheets/_rels/sheetN.xml.rels).
        exclues_rels = set()
        for cible in exclues:
            nom = cible.rsplit("/", 1)[-1]
            exclues_rels.add(f"xl/worksheets/_rels/{nom}.rels")

        wb_ampute = self._workbook_ampute(dossier, gardees)
        rels_ampute = self._workbook_rels_ampute(dossier, gardees)

        tmp = f"{output_path}.tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for racine, _, fichiers in os.walk(dossier):
                for nom in fichiers:
                    complet = Path(racine) / nom
                    rel = complet.relative_to(dossier).as_posix()
                    if rel in exclues or rel in exclues_rels:
                        continue
                    if rel == "xl/workbook.xml" and wb_ampute is not None:
                        z.writestr(rel, wb_ampute)
                        continue
                    if (rel == "xl/_rels/workbook.xml.rels"
                            and rels_ampute is not None):
                        z.writestr(rel, rels_ampute)
                        continue
                    z.write(complet, rel)
        os.replace(tmp, output_path)

    @staticmethod
    def _workbook_ampute(dossier: Path, gardees: list[dict]) -> bytes | None:
        """`workbook.xml` où ne restent que les `<sheet>` gardés."""
        wb = dossier / "xl" / "workbook.xml"
        if not wb.exists():
            return None
        arbre = etree.parse(str(wb))
        rids = {s["r_id"] for s in gardees}
        for sh in arbre.getroot().xpath(".//s:sheet", namespaces=NS):
            if (sh.get(f"{{{NS['r']}}}id") or "") not in rids:
                sh.getparent().remove(sh)
        # `definedNames` peut citer une feuille disparue (« =Feuil2!A1 ») :
        # Excel signale alors une référence brisée à l'ouverture. On retire les
        # noms qui citent une feuille écartée — l'aperçu n'en a aucun besoin.
        noms_gardes = {s["nom"] for s in gardees}
        for dn in arbre.getroot().xpath(".//s:definedName", namespaces=NS):
            texte = (dn.text or "")
            cite = texte.split("!")[0].strip("='$ ")
            if cite and cite not in noms_gardes and "!" in texte:
                dn.getparent().remove(dn)
        return etree.tostring(arbre, xml_declaration=True,
                              encoding="UTF-8", standalone=True)

    @staticmethod
    def _workbook_rels_ampute(dossier: Path,
                              gardees: list[dict]) -> bytes | None:
        """`workbook.xml.rels` privé des relations vers les feuilles écartées.

        Les AUTRES relations (styles, magasin partagé, thème) sont conservées :
        `workbook.xml` les cite toujours, et une relation manquante rend le
        classeur illisible.
        """
        chemin = dossier / "xl" / "_rels" / "workbook.xml.rels"
        if not chemin.exists():
            return None
        arbre = etree.parse(str(chemin))
        rids = {s["r_id"] for s in gardees}
        for rel in list(arbre.getroot()):
            type_ = rel.get("Type") or ""
            if not type_.endswith("/worksheet"):
                continue
            if (rel.get("Id") or "") not in rids:
                arbre.getroot().remove(rel)
        return etree.tostring(arbre, xml_declaration=True,
                              encoding="UTF-8", standalone=True)

    # ── Extraction ────────────────────────────────────────────────────────
    def extract_text(self, input_path: str, output_json: str = "extraction_xlsx.json",
                     filters: dict | None = None, progress_callback=None):
        """Relève le texte traduisible du classeur.

        Le relevé porte l'IDENTITÉ de chaque chaîne (partie + index), pas sa
        valeur : c'est ce qui permet à l'injection de la retrouver sans
        deviner. Deux cellules au même texte restent deux entrées distinctes.
        """
        dossier = self._ouvrir(input_path)
        elements: list[dict] = []
        types = defaultdict(set)

        if progress_callback:
            progress_callback("Lecture du classeur…")

        self._relever_noms_onglets(dossier, elements, types)
        self._relever_chaines_partagees(dossier, elements, types)
        self._relever_chaines_en_ligne(dossier, elements, types)
        self._relever_graphiques(dossier, elements, types)

        extraction = {
            "workbook": {
                "elements": elements,
                # Ce que le moteur SAIT ne pas avoir lu. Transporté dans le
                # relevé pour être visible d'un simple coup d'œil au JSON,
                # plutôt que d'être une surprise à l'ouverture du résultat.
                "non_traite": [nom for nom, (fait, _) in _PARTIES.items() if not fait],
            },
            "types": {k: sorted(v) for k, v in types.items()},
        }

        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(extraction, f, ensure_ascii=False, indent=2)
        return extraction, output_json

    def _relever_noms_onglets(self, dossier: Path, elements, types) -> None:
        """`xl/workbook.xml` — le nom de chaque onglet.

        Un nom d'onglet est VISIBLE, en bas de la fenêtre : le laisser en langue
        source dans un classeur par ailleurs traduit se remarque immédiatement.

        Il est aussi RÉFÉRENCÉ — par les formules (`=Ventes!A1`) et par les noms
        définis. Le traduire sans réécrire ces références produit `#REF!` dans
        toute la feuille. Les deux gestes sont donc faits ENSEMBLE à
        l'injection ; le relevé, lui, se contente de nommer chaque onglet.

        Pas de balises `[[n]]` ici : un nom d'onglet n'a pas de mise en forme
        par morceaux, et le baliser ferait passer des crochets dans un champ où
        Excel ne les accepte pas.

        `dossier` n'est pas lu — `feuilles()` travaille déjà sur le dossier
        courant — mais reste dans la signature : les trois releveurs s'appellent
        de la même façon, et briser cette symétrie pour un paramètre inutilisé
        rendrait le point d'appel moins lisible qu'il ne l'est.
        """
        for f in self.feuilles():
            nom = f["nom"]
            if not nom.strip() or est_numerique(nom):
                continue
            elements.append({
                "id": f"sheetname_{f['index']}",
                "text": nom,
                "context": {"part": "sheetName", "index": f["index"],
                            "sheet_id": f["sheet_id"]},
            })
            types["workbook"].add("Nom d'onglet")

    def _relever_chaines_partagees(self, dossier: Path, elements, types) -> None:
        """`xl/sharedStrings.xml` — le magasin de chaînes du classeur.

        Une entrée `<si>` peut contenir PLUSIEURS `<t>` quand la cellule mêle
        des mises en forme (« Total » en gras + « 2026 » en maigre). Chaque
        `<t>` devient une balise : c'est exactement le contrat de runs des
        autres moteurs, et il rend sa graisse à chaque morceau.
        """
        chemin = dossier / "xl" / "sharedStrings.xml"
        if not chemin.exists():
            return
        arbre = etree.parse(str(chemin))
        for i, si in enumerate(arbre.getroot().xpath("./s:si", namespaces=NS)):
            balise = _baliser(si.xpath(".//s:t", namespaces=NS))
            if not balise:
                continue
            elements.append({
                "id": f"ss_{i}",
                "text": balise,
                "context": {"part": "sharedStrings", "index": i},
            })
            types["workbook"].add("Chaîne partagée")

    @staticmethod
    def _textes_de_graphique(racine):
        """Les nœuds de texte TRADUISIBLES d'un `chart*.xml`, dans l'ordre.

        DEUX FAMILLES, ET UNE SEULE RÈGLE POUR LES SÉPARER
        ---------------------------------------------------
        `<a:t>` — le texte RICHE du graphique : titre, noms d'axes, étiquettes
        saisies à la main. Il n'existe que là, il n'est le reflet de rien. On
        le traduit.

        `<c:v>` — une valeur. Presque toujours sous un `<c:strCache>` ou un
        `<c:numCache>`, c'est-à-dire le CACHE d'une cellule de la feuille :
        Excel y recopie ce qu'affiche `Ventes!$B$1` pour dessiner sans relire
        le classeur.

        CE CACHE NE DOIT PAS ÊTRE TRADUIT ICI, et c'est LA différence avec le
        moteur PPTX — où le graphique porte ses propres données et où traduire
        `<c:v>` est juste. Dans un classeur, la cellule d'origine est déjà
        traduite via `sharedStrings` : traduire aussi son cache, c'est
        soumettre deux fois le même texte au modèle, qui peut rendre deux
        formulations différentes. Le graphique afficherait alors autre chose
        que sa feuille — et au premier rafraîchissement, Excel réécrit le cache
        depuis la cellule et le travail est perdu.

        MESURÉ sur un graphique réel (titre + 2 axes + série + catégories) :
        les 7 `<c:v>` sont TOUS sous un cache, et les 3 vrais textes du
        graphique sont TOUS des `<a:t>`.

        Reste le cas d'un `<c:v>` SANS cache au-dessus : un texte saisi en dur,
        sans référence à une cellule. Celui-là n'est le reflet de rien, et on
        le traduit — c'est le sens du test d'ancêtres.
        """
        noeuds = []
        for t in racine.xpath(".//a:t", namespaces=NS):
            if (t.text or "").strip():
                noeuds.append(t)
        for v in racine.xpath(".//c:v", namespaces=NS):
            texte = (v.text or "").strip()
            if not texte or est_numerique(texte):
                continue
            # Sous un cache = reflet d'une cellule déjà traduite.
            if any(a.tag.split("}")[-1] in ("strCache", "numCache")
                   for a in v.iterancestors()):
                continue
            noeuds.append(v)
        return noeuds

    def _relever_graphiques(self, dossier: Path, elements, types) -> None:
        """`xl/charts/chart*.xml` — titres, noms d'axes, étiquettes.

        Un graphique est ce qu'on regarde en premier dans un classeur : son
        titre et ses axes non traduits sautent aux yeux, même quand tout le
        reste est juste.
        """
        dossier_charts = dossier / "xl" / "charts"
        if not dossier_charts.is_dir():
            return
        for chart in sorted(dossier_charts.glob("chart*.xml")):
            arbre = etree.parse(str(chart))
            for i, noeud in enumerate(self._textes_de_graphique(
                    arbre.getroot())):
                elements.append({
                    "id": f"chart_{chart.stem}_{i}",
                    "text": f"[[0]]{noeud.text}[[/0]]",
                    "context": {"part": "chart", "chart": chart.stem,
                                "index": i},
                })
                types["workbook"].add("Graphique")

    def _injecter_graphiques(self, dossier: Path, traductions: dict) -> None:
        """Réinjecte dans les graphiques, par POSITION.

        L'appariement se fait sur l'ordre des nœuds, exactement comme au
        relevé : `_textes_de_graphique` est la SEULE source des deux côtés, si
        bien qu'aucune divergence n'est possible. Se fier au texte pour
        retrouver un nœud échouerait dès que deux axes portent le même libellé.
        """
        from engines import runtags

        dossier_charts = dossier / "xl" / "charts"
        if not dossier_charts.is_dir():
            return
        for chart in sorted(dossier_charts.glob("chart*.xml")):
            arbre = etree.parse(str(chart))
            touche = False
            for i, noeud in enumerate(self._textes_de_graphique(
                    arbre.getroot())):
                traduit = traductions.get(f"chart_{chart.stem}_{i}")
                if traduit is None:
                    continue
                # Un seul nœud, donc une seule balise : `sans_balises` suffit,
                # là où les cellules passent par `_repartir` (plusieurs `<t>`).
                propre = runtags.sans_balises(traduit).strip()
                if propre and propre != noeud.text:
                    noeud.text = propre
                    touche = True
            if touche:
                arbre.write(str(chart), xml_declaration=True,
                            encoding="UTF-8", standalone=True)

    def _relever_chaines_en_ligne(self, dossier: Path, elements, types) -> None:
        """Cellules `t="inlineStr"` — la chaîne est écrite DANS la feuille.

        Beaucoup d'exports automatiques n'utilisent que cette forme et laissent
        `sharedStrings.xml` absent. Ne lire que le magasin partagé revient alors
        à rendre le classeur inchangé, sans la moindre erreur — le pire des
        échecs, celui qui se croit réussi.
        """
        feuilles = sorted((dossier / "xl" / "worksheets").glob("sheet*.xml"))
        for feuille in feuilles:
            arbre = etree.parse(str(feuille))
            cellules = arbre.getroot().xpath(
                ".//s:c[@t='inlineStr']", namespaces=NS)
            for cellule in cellules:
                ref = cellule.get("r") or ""
                balise = _baliser(cellule.xpath(".//s:is//s:t", namespaces=NS))
                if not balise:
                    continue
                elements.append({
                    "id": f"inline_{feuille.stem}_{ref}",
                    "text": balise,
                    "context": {"part": "inlineStr", "sheet": feuille.stem,
                                "cell": ref},
                })
                types["workbook"].add("Chaîne en ligne")

    # ── Injection ─────────────────────────────────────────────────────────
    def inject_translation(self, original_path: str, translated_json: str,
                           output_path: str, format_options: dict | None = None,
                           progress_callback=None) -> tuple[bool, str]:
        """Réinjecte la traduction et réassemble le classeur.

        Les nœuds sont modifiés SUR PLACE : aucun `<t>` n'est supprimé ni
        recréé, donc styles, largeurs de colonnes, mises en forme
        conditionnelles et formules restent exactement ce qu'ils étaient.
        """
        if not os.path.exists(translated_json):
            return False, "Le fichier de traduction est introuvable."

        with open(translated_json, encoding="utf-8") as f:
            donnees = json.load(f)

        traductions = {
            el["id"]: el.get("translated_text", el.get("text", ""))
            for el in donnees.get("workbook", {}).get("elements", [])
        }
        if not traductions:
            return False, "Aucun texte traduit à réinjecter."

        try:
            dossier = self._ouvrir(original_path)
            if progress_callback:
                progress_callback("Réinjection dans le classeur…")

            # L'ordre compte : les noms d'onglets D'ABORD. La réécriture des
            # références lit les noms COURANTS des feuilles pour construire sa
            # table de renommage ; la faire après une autre injection ne
            # changerait rien ici, mais garder le même ordre que
            # `injecter_partiel` évite d'avoir à se poser la question.
            self._injecter_noms_onglets(dossier, traductions)
            self._injecter_chaines_partagees(dossier, traductions)
            self._injecter_chaines_en_ligne(dossier, traductions)
            self._injecter_graphiques(dossier, traductions)

            self._refermer(output_path)
            return True, "Classeur traduit."
        except Exception as exc:
            # Le message part vers l'utilisateur : il dit ce qui s'est passé,
            # pas où dans le code.
            return False, f"Le classeur n'a pas pu être reconstruit : {exc}"
        finally:
            self._nettoyer()

    def _injecter_noms_onglets(self, dossier: Path, traductions: dict) -> None:
        """Renomme les onglets ET réécrit TOUTES les références qui les citent.

        LES DEUX GESTES SONT UN SEUL, ET C'EST TOUT L'ENJEU
        ---------------------------------------------------
        Un nom d'onglet est cité à trois endroits, et en oublier un casse le
        classeur :

          · `<sheet name="…">` dans `workbook.xml` — le nom lui-même ;
          · `<definedName>` — les noms définis (`Zone_ventes` -> `Ventes!$A$1`) ;
          · `<f>` dans chaque feuille — les formules (`=Ventes!A2-Charges!A2`).

        Renommer l'onglet sans réécrire les formules produit `#REF!` dans toute
        la feuille : l'utilisateur reçoit un classeur ouvrable, d'apparence
        traduite, et dont tous les calculs sont morts. C'est exactement le
        genre de dégât silencieux qu'on refuse.

        UN NOM REFUSÉ N'EST PAS UNE ERREUR
        ----------------------------------
        Excel impose ses règles (31 caractères, pas de `: \\ / ? * [ ]`, pas
        d'apostrophe). Une traduction qui les viole n'est pas appliquée : le
        classeur garde l'onglet d'origine. Un onglet non traduit se voit ;
        un classeur qu'Excel refuse d'ouvrir ne se rattrape pas.

        On refuse aussi les COLLISIONS — deux onglets ne peuvent pas porter le
        même nom, et le modèle peut très bien traduire « Ventes » et « Ventes
        2026 » par le même mot. La comparaison est insensible à la casse, comme
        celle d'Excel.
        """
        wb = dossier / "xl" / "workbook.xml"
        if not wb.exists():
            return

        feuilles = self.feuilles()
        renommage: dict[str, str] = {}
        # Les noms DÉJÀ pris : ceux qu'on ne traduit pas restent en place et
        # continuent d'occuper leur nom.
        pris = {f["nom"].casefold() for f in feuilles}

        for f in feuilles:
            propose = (traductions.get(f"sheetname_{f['index']}") or "").strip()
            if not propose or propose == f["nom"]:
                continue
            if not nom_onglet_valide(propose):
                continue
            if propose.casefold() in pris - {f["nom"].casefold()}:
                continue                      # collision : on garde l'original
            pris.discard(f["nom"].casefold())
            pris.add(propose.casefold())
            renommage[f["nom"]] = propose

        if not renommage:
            return

        # 1. Le nom lui-même, et les noms définis.
        arbre = etree.parse(str(wb))
        for sh in arbre.getroot().xpath(".//s:sheet", namespaces=NS):
            cible = renommage.get(sh.get("name") or "")
            if cible:
                sh.set("name", cible)
        for dn in arbre.getroot().xpath(".//s:definedName", namespaces=NS):
            if dn.text:
                dn.text = reecrire_references(dn.text, renommage)
        arbre.write(str(wb), xml_declaration=True, encoding="UTF-8",
                    standalone=True)

        # 2. Les formules de CHAQUE feuille. Une formule d'une feuille peut
        #    citer n'importe quelle autre : on les parcourt toutes.
        for f in feuilles:
            chemin = dossier / f["cible"]
            if not chemin.exists():
                continue
            arbre_f = etree.parse(str(chemin))
            touche = False
            for noeud in arbre_f.getroot().xpath(".//s:f", namespaces=NS):
                if not noeud.text:
                    continue
                reecrit = reecrire_references(noeud.text, renommage)
                if reecrit != noeud.text:
                    noeud.text = reecrit
                    touche = True
            if touche:
                arbre_f.write(str(chemin), xml_declaration=True,
                              encoding="UTF-8", standalone=True)

    def _injecter_chaines_partagees(self, dossier: Path, traductions: dict) -> None:
        chemin = dossier / "xl" / "sharedStrings.xml"
        if not chemin.exists():
            return
        arbre = etree.parse(str(chemin))
        for i, si in enumerate(arbre.getroot().xpath("./s:si", namespaces=NS)):
            traduit = traductions.get(f"ss_{i}")
            if traduit is None:
                continue
            self._repartir(si.xpath(".//s:t", namespaces=NS), traduit)
        arbre.write(str(chemin), xml_declaration=True, encoding="UTF-8",
                    standalone=True)

    def _injecter_chaines_en_ligne(self, dossier: Path, traductions: dict) -> None:
        for feuille in sorted((dossier / "xl" / "worksheets").glob("sheet*.xml")):
            arbre = etree.parse(str(feuille))
            touche = False
            for cellule in arbre.getroot().xpath(".//s:c[@t='inlineStr']",
                                                 namespaces=NS):
                ref = cellule.get("r") or ""
                traduit = traductions.get(f"inline_{feuille.stem}_{ref}")
                if traduit is None:
                    continue
                self._repartir(cellule.xpath(".//s:is//s:t", namespaces=NS),
                               traduit)
                touche = True
            if touche:
                arbre.write(str(feuille), xml_declaration=True,
                            encoding="UTF-8", standalone=True)

    @staticmethod
    def _repartir(noeuds: list, traduit: str) -> None:
        """Répartit un texte balisé sur ses nœuds `<t>` d'origine.

        Le contrat de balises est celui d'`engines.runtags`, et sa règle vaut
        ici comme ailleurs : AUCUN nœud ne conserve son texte SOURCE. Quand le
        modèle rend moins de balises qu'il n'en a reçu, `runtags.repartir`
        étale ce qui est revenu sur tous les nœuds et vide les surnuméraires.

        Un nœud vide est invisible dans une cellule ; un nœud resté en français
        ne l'est pas — c'est du texte bilingue affiché, et c'est le défaut que
        `runtags` a été écrit pour tuer.
        """
        from engines import runtags

        if not noeuds:
            return
        # `repartir` reçoit les textes SOURCE (pour connaître les longueurs
        # d'origine) et la réponse balisée ; il rend un texte par nœud.
        sources = [n.text or "" for n in noeuds]
        morceaux = runtags.repartir(sources, traduit)
        for rang, noeud in enumerate(noeuds):
            noeud.text = morceaux[rang] if rang < len(morceaux) else ""

    # ── Diagnostic ────────────────────────────────────────────────────────
    @staticmethod
    def couverture() -> dict[str, bool]:
        """Ce que le moteur lit, et ce qu'il ne lit pas encore.

        Lue par les tests pour que l'écart entre la promesse et le code reste
        mesurable au lieu d'être une impression.
        """
        return {nom: fait for nom, (fait, _) in _PARTIES.items()}
