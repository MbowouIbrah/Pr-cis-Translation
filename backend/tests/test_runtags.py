"""Contrat des balises de runs — aucun run ne garde jamais sa langue SOURCE.

Les deux premiers cas sont MESURÉS sur des diapositives réelles ; les autres
sont là pour que la réparation ne casse pas ce qui marchait.

Le test décisif n'est pas « la traduction est-elle bien répartie » (c'est un
compromis, il est discutable) mais « reste-t-il un mot de la source dans la
sortie ». C'est ce que l'utilisateur voyait, et c'est ce qui ne doit plus
arriver.

Exécution :  backend/venv/Scripts/python.exe backend/test_runtags.py
Aucune dépendance : ni base, ni LibreOffice, ni réseau.
"""
from __future__ import annotations
import json
import os
import sys

import racine  # noqa: F401  -- met backend/ sur le chemin

from engines import runtags                                            # noqa: E402


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    # ── 1. Le cas de la slide 8 ──────────────────────────────────────────
    # « 2 » | « ° » | « JOUR DE FORMATION », le modèle rend tout dans [[0]].
    # Affiché avant correctif : « DAY 2 OF TRAINING° JOUR DE FORMATION ».
    parts = ["2", "°", " JOUR DE FORMATION"]
    out = runtags.repartir(parts, "[[0]]DAY 2 OF TRAINING[[/0]]")
    joint = "".join(out)
    ok("SLIDE 8  un texte par run", len(out) == len(parts), f"out={out}")
    ok("SLIDE 8  la traduction est présente", "DAY 2 OF TRAINING" in joint,
       f"joint={joint!r}")
    ok("SLIDE 8  plus aucun mot français", "JOUR" not in joint and "FORMATION" not in joint,
       f"joint={joint!r}")
    ok("SLIDE 8  le degré orphelin a disparu", "°" not in joint, f"joint={joint!r}")

    # ── 2. Le cas du sous-titre ──────────────────────────────────────────
    # « Sous- » | « titre » : coupure AU MILIEU d'un mot.
    # Affiché avant correctif : « Subtitletitre » / « subtitulotitre ».
    parts = ["Sous-", "titre"]
    for langue, trad in (("EN", "Subtitle"), ("PT", "Subtítulo")):
        out = runtags.repartir(parts, f"[[0]]{trad}[[/0]]")
        joint = "".join(out)
        ok(f"SOUS-TITRE {langue}  la traduction est intacte", joint.strip() == trad,
           f"joint={joint!r}")
        ok(f"SOUS-TITRE {langue}  « titre » ne survit pas", "titre" not in joint,
           f"joint={joint!r}")

    # ── 2-bis. Le VRAI cas de la slide 8 : un run RECOPIÉ ────────────────
    # Les deux balises sont là — le contrat est formellement respecté — mais le
    # run 1 a été recopié en français (à la casse près). Aucune vérification de
    # balises ne l'attrape : c'est `defauts` qui doit le voir, pour REDEMANDER.
    src = "[[0]]2[[/0]][[1]]° JOUR DE FORMATION[[/1]]"
    rendu = "[[0]]Day 2 of training[[/0]][[1]]° jour de formation[[/1]]"
    ok("RECOPIE  le défaut est signalé", "recopie" in runtags.defauts(src, rendu),
       str(runtags.defauts(src, rendu)))
    # …mais la réinjection ne SUPPRIME rien : elle n'a pas de quoi trancher.
    joint = "".join(runtags.repartir(["2", "° JOUR DE FORMATION"], rendu))
    ok("RECOPIE  la réinjection ne supprime rien",
       "Day 2 of training" in joint and "jour de formation" in joint,
       f"joint={joint!r}")

    # ── 2-ter. Ce qui ne doit JAMAIS être signalé ────────────────────────
    # Un paragraphe entièrement inchangé est le cas NORMAL d'un nom propre, d'un
    # sigle ou d'une date : rien n'a changé, il n'y a pas de mélange de langues.
    parts = ["CFAO MOBILITY Slide Master ", "MOBILITY", " 2026"]
    ok("NOM PROPRE  rien n'a changé : aucun défaut",
       runtags.runs_recopies(parts, list(parts)) == [],
       str(runtags.runs_recopies(parts, list(parts))))

    # Un run SANS LETTRE recopié est la seule réponse juste : une puce, une
    # date suivie d'un séparateur ne se traduisent pas.
    for src, rendu in (("[[0]]• [[/0]][[1]]Lecture[[/1]]",
                        "[[0]]• [[/0]][[1]]Reading[[/1]]"),
                       ("[[0]]2021 – 2023 | [[/0]][[1]]Commerciale[[/1]]",
                        "[[0]]2021 – 2023 | [[/0]][[1]]Sales representative[[/1]]")):
        ok(f"SANS LETTRE  {src[5:12]!r} recopié n'est pas un défaut",
           runtags.defauts(src, rendu) == [], str(runtags.defauts(src, rendu)))
    # …mais un run AVEC lettres recopié à côté reste signalé.
    ok("SANS LETTRE  le mot recopié voisin reste signalé",
       runtags.runs_recopies(["• ", "Lecture", " et danse"],
                             ["• ", "lecture", " and dance"]) == [1],
       "")

    # LE NOM DE PERSONNE — une version antérieure, calée sur un document, l'a
    # EFFACÉ. Il est désormais SIGNALÉ (donc redemandé), jamais supprimé.
    parts = ["RÉSULTAT INDIVIDUEL DE FORMATION: ", "Inoc Rodrigues Franca e Almeida"]
    joint = "".join(runtags.repartir(
        parts, "[[0]]INDIVIDUAL TRAINING RESULT: [[/0]]"
               "[[1]]Inoc Rodrigues Franca e Almeida[[/1]]"))
    ok("NOM DE PERSONNE  survit intact à la réinjection",
       "Inoc Rodrigues Franca e Almeida" in joint, f"joint={joint!r}")

    # ── 2-quinquies. Les NOMBRES sont des données ────────────────────────
    # « 6ème jour » rendu « 5.e jour » : invisible pour qui ne lit que la cible.
    ok("NOMBRES  un chiffre altéré est signalé",
       "nombres" in runtags.defauts("[[0]]6ème jour de formation[[/0]]",
                                    "[[0]]5.e day of training[[/0]]"),
       str(runtags.defauts("[[0]]6ème jour de formation[[/0]]",
                           "[[0]]5.e day of training[[/0]]")))
    ok("NOMBRES  un ordinal correctement rendu ne l'est pas",
       runtags.defauts("[[0]]6ème jour[[/0]]", "[[0]]6th day[[/0]]") == [],
       str(runtags.defauts("[[0]]6ème jour[[/0]]", "[[0]]6th day[[/0]]")))
    ok("NOMBRES  un texte sans chiffre ne déclenche rien",
       runtags.defauts("[[0]]Bonjour[[/0]]", "[[0]]Hello[[/0]]") == [],
       str(runtags.defauts("[[0]]Bonjour[[/0]]", "[[0]]Hello[[/0]]")))

    # ── 2-sexies. Balises manquantes : signalées AUSSI ───────────────────
    ok("BALISES  une réponse incomplète est signalée",
       "balises" in runtags.defauts("[[0]]Sous-[[/0]][[1]]titre[[/1]]",
                                    "[[0]]Subtitle[[/0]]"),
       str(runtags.defauts("[[0]]Sous-[[/0]][[1]]titre[[/1]]",
                           "[[0]]Subtitle[[/0]]")))
    ok("BALISES  une réponse complète ne l'est pas",
       runtags.defauts("[[0]]Note : [[/0]][[1]]lire[[/1]]",
                       "[[0]]Note: [[/0]][[1]]read[[/1]]") == [],
       str(runtags.defauts("[[0]]Note : [[/0]][[1]]lire[[/1]]",
                           "[[0]]Note: [[/0]][[1]]read[[/1]]")))

    # ── 2-quater. La slide 19 : réponse INCOMPLÈTE, 5 balises pour 7 runs ─
    # Le modèle a fusionné deux runs et ABANDONNÉ le dernier (une phrase
    # entière). Une première version redistribuait la phrase sur les 7 runs et
    # entassait tout dans le DERNIER, vidant les six autres : le nom propre en
    # gras des runs 1 et 4 perdait sa mise en forme. On garde désormais chaque
    # réponse à SA place et on ne vide que les runs sans réponse.
    parts = ["Pour la partie théorique ", "Emitério",
             " maîtrise avec un taux de 80%, il doit s'",
             "exercer à la conduite. ", "Emitério", " a eu ",
             "un comportement de qualité lors de la formation."]
    rendu = ("[[0]]For the theoretical part, [[/0]][[1]]Emitério[[/1]]"
             "[[2]] has mastered with a rate of 80%, he must practise driving. [[/2]]"
             "[[3]]Emitério[[/3]][[4]] a eu [[/4]]")
    out = runtags.repartir(parts, rendu)
    ok("SLIDE 19  chaque réponse reste à SA place",
       out[0] == "For the theoretical part, " and out[1] == "Emitério",
       f"out={out[:3]}")
    ok("SLIDE 19  les runs sans réponse sont vidés, pas remplis de source",
       out[5] == "" and out[6] == "", f"out[5:]={out[5:]}")
    ok("SLIDE 19  la mise en forme n'est pas concentrée sur un seul run",
       sum(1 for t in out if t.strip()) >= 4, f"out={out}")
    ok("SLIDE 19  aucun mot français de la source ne subsiste",
       "comportement" not in "".join(out) and "théorique" not in "".join(out),
       f"joint={''.join(out)!r}")

    # ── 3. Contrat respecté : on ne touche à RIEN ────────────────────────
    # La réparation ne doit jamais s'inviter quand le modèle a bien répondu :
    # elle détruirait une répartition volontaire.
    parts = ["Attention : ", "lire la notice"]
    conforme = "[[0]]Warning: [[/0]][[1]]read the manual[[/1]]"
    out = runtags.repartir(parts, conforme)
    ok("CONFORME  la réponse du modèle est reprise telle quelle",
       out == ["Warning: ", "read the manual"], f"out={out}")

    # ── 4. Frontières sur les mots : on répartit, on ne concentre pas ────
    # Ici le découpage porte du sens (gras + normal) : chaque run doit garder
    # sa part, sinon on perd la mise en forme sur la moitié de la phrase.
    parts = ["Note : ", "ceci est un point important"]
    out = runtags.repartir(parts, "[[0]]Note: this is an important point[[/0]]")
    ok("MOTS  les deux runs reçoivent du texte",
       all(t.strip() for t in out), f"out={out}")
    ok("MOTS  aucun mot n'est perdu",
       "".join(out).split() == "Note: this is an important point".split(),
       f"out={out}")
    ok("MOTS  aucun mot n'est collé",
       "pointNote" not in "".join(out) and ":this" not in "".join(out),
       f"joint={''.join(out)!r}")

    # ── 5. Moins de mots que de runs ─────────────────────────────────────
    # On ne peut pas donner un mot à chacun sans en couper : tout va au run
    # dominant plutôt que de fabriquer des moitiés de mots.
    parts = ["Bonjour ", "tout ", "le monde"]
    out = runtags.repartir(parts, "[[0]]Hello[[/0]]")
    ok("PEU DE MOTS  un seul run porte le texte",
       sum(1 for t in out if t.strip()) == 1, f"out={out}")
    ok("PEU DE MOTS  rien de la source ne subsiste",
       "tout" not in "".join(out) and "monde" not in "".join(out), f"out={out}")

    # ── 6. Balises orphelines ────────────────────────────────────────────
    # Une ouverture sans fermeture traversait l'ancienne regex et s'affichait
    # telle quelle dans le document.
    ok("ORPHELINE  aucune balise ne franchit sans_balises",
       "[[" not in runtags.sans_balises("[[0]]Bonjour[[/0]] [[1]]reste"),
       repr(runtags.sans_balises("[[0]]Bonjour[[/0]] [[1]]reste")))

    # ── 7. Réponse NON balisée ───────────────────────────────────────────
    # Le modèle rend parfois du texte nu. Ce n'est pas une raison pour
    # laisser la source en place.
    parts = ["2", "°", " JOUR DE FORMATION"]
    out = runtags.repartir(parts, "DAY 2 OF TRAINING")
    ok("TEXTE NU  la source ne survit pas",
       "JOUR" not in "".join(out) and "DAY 2 OF TRAINING" in "".join(out),
       f"out={out}")

    # ── 8. Un seul run : cas courant, rien ne doit se compliquer ────────
    out = runtags.repartir(["Bonjour le monde"], "[[0]]Hello world[[/0]]")
    ok("RUN UNIQUE  rendu tel quel", out == ["Hello world"], f"out={out}")

    # ── 8-bis. Glossaire de DOCUMENT : la preuve vient du document ───────
    # Le modèle traduit slide par slide et n'a aucune mémoire d'une slide à
    # l'autre : il rend « Gerbeur » par « Stacker » quinze fois et le laisse en
    # français la seizième. Aucune règle de forme ne peut l'attraper — le
    # fragment est parfaitement balisé. C'est la COHÉRENCE qui est rompue.
    paires = [
        ("[[0]]Découverte du Gerbeur livré[[/0]]",
         "[[0]]Discovering the Stacker delivered[[/0]]"),
        ("[[0]]Manutention du Gerbeur hors du conteneur[[/0]]",
         "[[0]]Moving the Stacker outside the container[[/0]]"),
        ("[[0]]le fonctionnement du Gerbeur, la maintenance[[/0]]",
         "[[0]]the operation of the Gerbeur, maintenance[[/0]]"),
    ]
    inc = runtags.termes_incoherents(paires)
    ok("GLOSSAIRE  le terme oublié est identifié", "gerbeur" in inc,
       str(sorted(inc)))
    ok("GLOSSAIRE  le fragment fautif est celui qui garde le terme",
       runtags.termes_a_reprendre(paires[2][0], paires[2][1], inc) == ["gerbeur"]
       and runtags.termes_a_reprendre(paires[0][0], paires[0][1], inc) == [],
       str(runtags.termes_a_reprendre(paires[2][0], paires[2][1], inc)))

    # Un nom propre survit PARTOUT : le document ne produit aucune preuve
    # contre lui, il ne doit jamais être signalé.
    propres = [("[[0]]Résultat de Emitério[[/0]]", "[[0]]Result for Emitério[[/0]]"),
               ("[[0]]Emitério a progressé[[/0]]", "[[0]]Emitério improved[[/0]]")]
    ok("GLOSSAIRE  un nom propre survivant partout n'est jamais signalé",
       "emiterio" not in runtags.termes_incoherents(propres),
       str(sorted(runtags.termes_incoherents(propres))))

    # LE PIÈGE DES SOUS-CHAÎNES — une première version cherchait le mot comme
    # sous-chaîne : « les » se trouvait dans « rules », « son » dans « person »,
    # et la moitié des mots outils du français paraissait non traduite.
    souschaines = [("[[0]]les règles du jeu[[/0]]", "[[0]]the rules of the game[[/0]]"),
                   ("[[0]]les stagiaires[[/0]]", "[[0]]the trainees[[/0]]")]
    ok("GLOSSAIRE  « les » dans « rules » n'est pas une survivance",
       "les" not in runtags.termes_incoherents(souschaines),
       str(sorted(runtags.termes_incoherents(souschaines))))

    # ── 9. La passe de QUALITÉ redemande, elle ne rafistole pas ──────────
    # Ce que les tests précédents ne peuvent pas montrer : qu'un défaut
    # CONSTATÉ déclenche bien un second appel au modèle, avec la consigne qui
    # dit ce qui n'allait pas. C'est là que la correction se fait — la
    # réinjection, elle, n'a plus le droit de deviner.
    import types
    from engines.translation_ai import TranslatorAI

    appels: list[list[dict]] = []

    class _Reponse:
        def __init__(self, contenu):
            msg = types.SimpleNamespace(content=contenu)
            self.choices = [types.SimpleNamespace(message=msg,
                                                  finish_reason="stop")]

    class _FauxClient:
        """1er appel : oublie une balise. 2e : réponse correcte."""
        def __init__(self):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create))

        def _create(self, **kw):
            envoye = json.loads(kw["messages"][1]["content"].split("\n\n", 1)[1])
            appels.append(envoye)
            if len(appels) == 1:
                corps = [{"id": "e1", "translated_text": "[[0]]Subtitle[[/0]]"}]
            else:
                corps = [{"id": "e1",
                          "translated_text": "[[0]]Sub[[/0]][[1]]title[[/1]]"}]
            return _Reponse(json.dumps({"translations": corps}))

    # `object.__new__` évite d'exiger une clé d'API : ce test ne parle à
    # personne. On pose à la main le strict nécessaire.
    tr = object.__new__(TranslatorAI)
    tr.client, tr.model, tr.max_retries = _FauxClient(), "faux", 1
    tr.system_instruction = ""
    lot = [{"id": "e1", "text": "[[0]]Sous-[[/0]][[1]]titre[[/1]]"}]
    tr._translate_batch(lot, "en", None, retries=1, passes=1)

    ok("PASSE  un défaut déclenche un second appel", len(appels) == 2,
       f"appels={len(appels)}")
    ok("PASSE  la consigne dit CE QUI n'allait pas",
       len(appels) > 1 and "consigne" in appels[1][0]
       and "balise" in appels[1][0]["consigne"].lower(),
       str(appels[1][0] if len(appels) > 1 else None))
    ok("PASSE  la seconde réponse remplace la première",
       lot[0]["translated_text"] == "[[0]]Sub[[/0]][[1]]title[[/1]]",
       repr(lot[0].get("translated_text")))

    appels.clear()
    lot = [{"id": "e1", "text": "[[0]]Bonjour le monde[[/0]]"}]

    class _ClientBon(_FauxClient):
        def _create(self, **kw):
            envoye = json.loads(kw["messages"][1]["content"].split("\n\n", 1)[1])
            appels.append(envoye)
            return _Reponse(json.dumps({"translations": [
                {"id": "e1", "translated_text": "[[0]]Hello world[[/0]]"}]}))

    tr.client = _ClientBon()
    tr._translate_batch(lot, "en", None, retries=1, passes=1)
    ok("PASSE  une réponse correcte ne coûte AUCUN appel de plus",
       len(appels) == 1, f"appels={len(appels)}")

    # ── 10. Un fragment ENTIER rendu inchangé est redemandé, une fois ────
    # La colonne anglaise d'un en-tête bilingue revenait telle quelle en
    # espagnol : aucun run « mélangé », donc aucun défaut, donc jamais
    # redemandée. Nom propre ou oubli, seul le modèle peut le dire.
    src = "[[0]]REPUBLIC OF CAMEROON[[/0]]"
    ok("INCHANGÉ  un fragment entier recopié est signalé",
       "inchange" in runtags.defauts(src, "[[0]]Republic of Cameroon[[/0]]"),
       str(runtags.defauts(src, "[[0]]Republic of Cameroon[[/0]]")))
    ok("INCHANGÉ  un fragment sans lettre recopié ne l'est pas",
       runtags.defauts("[[0]]2021 – 2023[[/0]]", "[[0]]2021 – 2023[[/0]]") == [],
       str(runtags.defauts("[[0]]2021 – 2023[[/0]]", "[[0]]2021 – 2023[[/0]]")))

    appels.clear()

    class _ClientRecopie(_FauxClient):
        """Rend toujours la source : un nom propre que le modèle maintient."""
        def _create(self, **kw):
            envoye = json.loads(kw["messages"][1]["content"].split("\n\n", 1)[1])
            appels.append(envoye)
            return _Reponse(json.dumps({"translations": [
                {"id": "e1", "translated_text": "[[0]]Almeida[[/0]]"}]}))

    tr.client = _ClientRecopie()
    lot = [{"id": "e1", "text": "[[0]]Almeida[[/0]]"}]
    tr._translate_batch(lot, "es", None, retries=1, passes=1)
    ok("INCHANGÉ  redemandé avec la consigne qui le dit",
       len(appels) == 2 and "inchangé" in appels[1][0].get("consigne", ""),
       str(appels[1][0] if len(appels) > 1 else None))
    ok("INCHANGÉ  la seconde réponse fait foi : rien n'est supprimé",
       len(appels) == 2 and lot[0]["translated_text"] == "[[0]]Almeida[[/0]]",
       f"appels={len(appels)} {lot[0].get('translated_text')!r}")

    print()
    n_ok = sum(1 for _, c, _ in checks if c)
    for nom, cond, detail in checks:
        etat = "  OK  " if cond else " FAIL "
        ligne = f"{etat}  {nom}"
        if not cond and detail:
            ligne += f"   [{detail}]"
        print(ligne)
    print(f"\n{n_ok}/{len(checks)}")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
