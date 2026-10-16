"""DeepSeek seul : réflexion explicite, refus sans acharnement, alertes de coût.

Ce que l'on vérifie :
  • la réflexion n'est JAMAIS laissée au défaut de DeepSeek (actif) : sans
    suffixe elle est coupée, « :high » l'active avec un budget suffisant ;
  • un 401/402/403/429 arrête le lot d'un coup, sans relance ni scission, et
    les pages suivantes n'appellent plus le réseau pendant la pause ;
  • le premier 402 d'une série déclenche l'alerte « solde épuisé », une fois ;
  • un incident passager (500) garde ses relances ;
  • les jetons facturés sont cumulés par modèle ;
  • la surveillance du solde : seuil bas signalé une fois puis réarmé par une
    recharge ; dépense 24 h = somme des baisses, recharges ignorées.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_deepseek_seul.py
Aucune dépendance réseau : le client est simulé.
"""
from __future__ import annotations
import json
import os
import sys
import types

import racine  # noqa: F401  -- met backend/ sur le chemin

import httpx                                                           # noqa: E402
import openai                                                          # noqa: E402

from engines import translation_ai                                     # noqa: E402
from engines.translation_ai import TranslatorAI                        # noqa: E402


def _erreur(statut):
    """Une VRAIE erreur du SDK openai : c'est elle que l'app reçoit."""
    rep = httpx.Response(statut, request=httpx.Request("POST", "https://x"))
    return openai.APIStatusError(f"statut {statut}", response=rep, body=None)


class _Client:
    """Répond `reponses[i]` au i-ème appel : une exception est levée, une
    chaîne est renvoyée comme contenu ; au-delà, la dernière se répète."""
    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.appels: list[dict] = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.appels.append(kw)
        r = self.reponses[min(len(self.appels), len(self.reponses)) - 1]
        if isinstance(r, Exception):
            raise r
        usage = types.SimpleNamespace(
            prompt_cache_hit_tokens=10, prompt_cache_miss_tokens=90,
            prompt_tokens=100, completion_tokens=50,
            completion_tokens_details=types.SimpleNamespace(reasoning_tokens=20))
        return types.SimpleNamespace(usage=usage, choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=r), finish_reason="stop")])


def _traduction(*ids, texte="Hello"):
    return json.dumps({"translations": [
        {"id": i, "translated_text": texte} for i in ids]})


def _traducteur(client, modele="deepseek-flash"):
    # `object.__new__` : aucune clé exigée, on pose le strict nécessaire.
    translation_ai._hors_service.update(jusqua=0.0, statut=None)
    tr = object.__new__(TranslatorAI)
    tr.client = client
    tr.model = modele
    tr.system_instruction = ""
    return tr


def _lot():
    return [{"id": "a", "text": "Bonjour"}, {"id": "b", "text": "Monde"}]


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    attentes: list[float] = []
    vrai_sleep = translation_ai.time.sleep
    translation_ai.time.sleep = attentes.append      # relances sans attendre
    alertes: list[int] = []
    translation_ai.sur_solde_epuise.append(lambda: alertes.append(1))
    try:
        # ── Réflexion explicite ──────────────────────────────────────────
        c = _Client(_traduction("a", "b"))
        _traducteur(c)._translate_batch(_lot(), "English", None)
        kw = c.appels[0]
        ok("RÉFLEXION  sans suffixe : coupée explicitement",
           kw.get("extra_body") == {"thinking": {"type": "disabled"}}
           and "reasoning_effort" not in kw, repr(kw.get("extra_body")))
        ok("RÉFLEXION  sans suffixe : nom de modèle transmis tel quel",
           kw["model"] == "deepseek-flash", kw["model"])

        c = _Client(_traduction("a", "b"))
        _traducteur(c, "deepseek-v4-pro:high")._translate_batch(
            _lot(), "English", None)
        kw = c.appels[0]
        ok("RÉFLEXION  « :high » : activée, effort transmis, suffixe retiré",
           kw.get("extra_body") == {"thinking": {"type": "enabled"}}
           and kw.get("reasoning_effort") == "high"
           and kw["model"] == "deepseek-v4-pro", repr(kw))
        ok("RÉFLEXION  budget de sortie relevé (le raisonnement s'y décompte)",
           kw["max_tokens"] >= 65536, str(kw["max_tokens"]))
        try:
            translation_ai.decouper_modele("deepseek-flash:turbo")
            leve = False
        except ValueError:
            leve = True
        ok("RÉFLEXION  niveau inconnu : erreur explicite", leve)

        # ── Consommation ─────────────────────────────────────────────────
        conso = translation_ai.consommation.get("deepseek-v4-pro:high", {})
        ok("CONSO  jetons cumulés par modèle (cache, hors cache, sortie, réflexion)",
           conso.get("appels") == 1 and conso.get("entree_cache") == 10
           and conso.get("entree_hors_cache") == 90 and conso.get("sortie") == 50
           and conso.get("reflexion") == 20, repr(conso))

        # ── Refus : arrêt net ────────────────────────────────────────────
        for statut in (401, 402, 403, 429):
            alertes.clear()
            c = _Client(_erreur(statut))
            reussi = _traducteur(c)._translate_batch(_lot(), "English", None)
            # 429 : une attente puis UN nouvel essai ; refusé encore, on cesse.
            attendu = 2 if statut == 429 else 1
            ok(f"REFUS {statut}  {attendu} appel(s), lot en échec sans scission",
               not reussi and len(c.appels) == attendu, f"{len(c.appels)} appels")
            if statut == 429:
                ok("REFUS 429  répétée après la pause : hors service",
                   translation_ai.hors_service())
            if statut == 402:
                ok("REFUS 402  alerte « solde épuisé » déclenchée une fois",
                   alertes == [1], repr(alertes))
                # Page suivante (nouvelle instance), pendant la pause.
                c2 = _Client(_traduction("a", "b"))
                tr2 = object.__new__(TranslatorAI)
                tr2.client, tr2.model, tr2.system_instruction = c2, "deepseek-flash", ""
                reussi2 = tr2._translate_batch(_lot(), "English", None)
                ok("REFUS 402  page suivante : aucun appel réseau pendant la pause",
                   not reussi2 and c2.appels == [], f"{len(c2.appels)} appels")
                ok("REFUS 402  pas de seconde alerte pendant la pause",
                   alertes == [1], repr(alertes))

        # ── Incident passager : relances conservées ──────────────────────
        c = _Client(_erreur(500), _traduction("a", "b"))
        lot = _lot()
        reussi = _traducteur(c)._translate_batch(lot, "English", None)
        ok("PASSAGER 500  relancé puis traduit",
           reussi and len(c.appels) == 2 and lot[0].get("translated_text") == "Hello",
           f"{len(c.appels)} appels")

        # ── Clé absente ──────────────────────────────────────────────────
        garde = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = ""
        try:
            TranslatorAI()
            leve = False
        except ValueError:
            leve = True
        finally:
            if garde is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = garde
        ok("ENV  sans DEEPSEEK_API_KEY : erreur explicite", leve)
    finally:
        translation_ai.time.sleep = vrai_sleep
        translation_ai.sur_solde_epuise.clear()
        translation_ai._hors_service["jusqua"] = 0.0

    # ── Surveillance du solde ────────────────────────────────────────────
    from app.services.solde_ia import Surveillance
    h = 3600.0
    s = Surveillance(seuil_solde=2.0, seuil_jour=1000.0)
    ok("SOLDE  au-dessus du seuil : aucune alerte", s.enregistrer(10.0, 0) == [])
    a = s.enregistrer(1.5, h)
    ok("SOLDE  passage sous le seuil : une alerte",
       len(a) == 1 and "bas" in a[0][0], repr(a))
    ok("SOLDE  toujours sous le seuil : pas de répétition",
       s.enregistrer(1.4, 2 * h) == [])
    s.enregistrer(20.0, 3 * h)                          # recharge
    a = s.enregistrer(1.0, 4 * h)
    ok("SOLDE  recharge puis rechute : alerte réarmée",
       any("bas" in x[0] for x in a), repr(a))

    s = Surveillance(seuil_solde=0.0, seuil_jour=3.0)
    s.enregistrer(10.0, 0)
    s.enregistrer(8.0, h)                               # -2
    s.enregistrer(15.0, 2 * h)                          # recharge +7
    ok("DÉPENSE  recharge ignorée : 2 $ dépensés", s.depense_24h(2 * h) == 2.0,
       str(s.depense_24h(2 * h)))
    a = s.enregistrer(13.5, 3 * h)                      # -1,5 → 3,5 $
    ok("DÉPENSE  au-delà du seuil 24 h : une alerte",
       len(a) == 1 and "Dépense" in a[0][0], repr(a))
    ok("DÉPENSE  pas de seconde alerte dans les 24 h",
       s.enregistrer(12.0, 4 * h) == [])
    ok("DÉPENSE  les baisses de plus de 24 h sortent de la fenêtre",
       s.depense_24h(30 * h) == 0.0, str(s.depense_24h(30 * h)))

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
