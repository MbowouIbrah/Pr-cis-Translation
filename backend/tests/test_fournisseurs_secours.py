"""Chaîne de fournisseurs — un solde épuisé bascule, il ne s'acharne pas.

Avant : un 402 de DeepSeek (solde négatif) était pris pour un incident
passager — 3 relances avec attente, puis scission du lot en deux, récursivement.
Une page de 50 fragments coûtait des dizaines d'appels voués à l'échec, et
l'app restait bloquée tant que personne ne rechargeait le compte.

Ce que l'on vérifie : un 401/402/403/429 met le fournisseur hors service et
passe AUSSITÔT au suivant ; quand plus aucun ne répond, le lot échoue d'un coup ;
un incident passager (500), lui, garde ses relances.

Exécution :  backend/venv/Scripts/python.exe backend/tests/test_fournisseurs_secours.py
Aucune dépendance réseau : les clients sont simulés.
"""
from __future__ import annotations
import json
import os
import sys
import time
import types

import racine  # noqa: F401  -- met backend/ sur le chemin

import httpx                                                           # noqa: E402
import openai                                                          # noqa: E402

from engines import translation_ai                                     # noqa: E402
from engines.translation_ai import TranslatorAI, _Fournisseur          # noqa: E402


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
        msg = types.SimpleNamespace(content=r)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=msg, finish_reason="stop")])


def _traduction(*ids, texte="Hello"):
    return json.dumps({"translations": [
        {"id": i, "translated_text": texte} for i in ids]})


def _traducteur(*fournisseurs):
    # `object.__new__` : aucune clé exigée, on pose le strict nécessaire.
    translation_ai._hors_service.clear()
    tr = object.__new__(TranslatorAI)
    tr.fournisseurs = list(fournisseurs)
    tr.model = "deepseek-v4-flash"
    tr.system_instruction = ""
    return tr


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    attentes: list[float] = []
    vrai_sleep = translation_ai.time.sleep
    translation_ai.time.sleep = attentes.append      # relances sans attendre
    try:
        # ── 1. Solde épuisé : bascule immédiate ──────────────────────────
        ds = _Client(_erreur(402))
        orr = _Client(_traduction("a", "b"))
        tr = _traducteur(
            _Fournisseur("deepseek", ds),
            _Fournisseur("openrouter", orr, modele="m1:free",
                         modeles_secours=["m2:free"], max_sortie=32768))
        lot = [{"id": "a", "text": "Bonjour"}, {"id": "b", "text": "Salut"}]
        reussi = tr._translate_batch(lot, "English", None, retries=3,
                                     model="deepseek-v4-flash",
                                     max_tokens=65536, passes=0)
        ok("402  le lot est traduit par le secours", reussi
           and [b.get("translated_text") for b in lot] == ["Hello", "Hello"],
           repr(lot))
        ok("402  DeepSeek n'est appelé qu'UNE fois (ni relance ni scission)",
           len(ds.appels) == 1, f"appels={len(ds.appels)}")
        ok("402  aucune attente de relance", not attentes, repr(attentes))
        kw = orr.appels[0] if orr.appels else {}
        ok("SECOURS  reçoit SON modèle, jamais le nom DeepSeek",
           kw.get("model") == "m1:free", repr(kw.get("model")))
        ok("SECOURS  modèles de repli transmis à OpenRouter",
           kw.get("extra_body") == {"models": ["m1:free", "m2:free"]},
           repr(kw.get("extra_body")))
        ok("SECOURS  budget de sortie plafonné au maximum du fournisseur",
           kw.get("max_tokens") == 32768, repr(kw.get("max_tokens")))

        # Le lot suivant ne repasse pas par le fournisseur hors service.
        lot2 = [{"id": "c", "text": "Merci"}]
        orr.reponses = [_traduction("c")]
        tr._translate_batch(lot2, "English", None, retries=3, passes=0)
        ok("HORS SERVICE  le lot suivant évite DeepSeek",
           len(ds.appels) == 1 and lot2[0].get("translated_text") == "Hello",
           f"ds={len(ds.appels)}")

        # Une NOUVELLE instance (le moteur PDF en crée une par page) se
        # souvient que DeepSeek est hors service.
        neuf = object.__new__(TranslatorAI)
        neuf.__dict__.update(tr.__dict__)
        neuf.fournisseurs = [_Fournisseur("deepseek", ds),
                             _Fournisseur("openrouter", orr, modele="m1:free")]
        orr.reponses = [_traduction("e")]
        lot_e = [{"id": "e", "text": "Merci"}]
        neuf._translate_batch(lot_e, "English", None, retries=3, passes=0)
        ok("HORS SERVICE  partagé entre instances (une par page)",
           len(ds.appels) == 1 and lot_e[0].get("translated_text") == "Hello",
           f"ds={len(ds.appels)}")

        # Le délai écoulé, le principal est de nouveau essayé.
        tr.fournisseurs[0].hors_service_jusqua = time.monotonic() - 1
        ds.reponses = [_traduction("d", texte="Thanks")]
        lot3 = [{"id": "d", "text": "Merci"}]
        tr._translate_batch(lot3, "English", None, retries=3, passes=0)
        ok("HORS SERVICE  le principal revient une fois le délai passé",
           lot3[0].get("translated_text") == "Thanks", repr(lot3))

        # ── 2. Plus personne : échec d'un coup ───────────────────────────
        attentes.clear()
        ds = _Client(_erreur(402))
        orr = _Client(_erreur(429))
        tr = _traducteur(_Fournisseur("deepseek", ds),
                         _Fournisseur("openrouter", orr, modele="m1:free"))
        messages: list[str] = []
        lot = [{"id": str(i), "text": f"Phrase {i}"} for i in range(8)]
        reussi = tr._translate_batch(lot, "English", messages.append,
                                     retries=3, passes=0)
        ok("ÉPUISÉ  le lot échoue", reussi is False)
        ok("ÉPUISÉ  un seul appel par fournisseur pour 8 fragments",
           (len(ds.appels), len(orr.appels)) == (1, 1),
           f"ds={len(ds.appels)} or={len(orr.appels)}")
        ok("ÉPUISÉ  aucune attente de relance", not attentes, repr(attentes))
        ok("ÉPUISÉ  le message nomme la cause",
           any("Aucun fournisseur" in m and "402" in m for m in messages),
           repr(messages))

        # ── 3. Incident passager : on garde les relances ─────────────────
        attentes.clear()
        ds = _Client(_erreur(500), _traduction("x", texte="Done"))
        orr = _Client(_traduction("x", texte="Secours"))
        tr = _traducteur(_Fournisseur("deepseek", ds),
                         _Fournisseur("openrouter", orr, modele="m1:free"))
        lot = [{"id": "x", "text": "Fait"}]
        tr._translate_batch(lot, "English", None, retries=3, passes=0)
        ok("500  relancé sur le principal, sans basculer",
           lot[0].get("translated_text") == "Done" and not orr.appels,
           f"{lot!r} or={len(orr.appels)}")

        # ── 4. Clôture ```json d'un modèle qui ignore response_format ────
        ds = _Client("```json\n" + _traduction("y", texte="Fenced") + "\n```")
        tr = _traducteur(_Fournisseur("deepseek", ds))
        lot = [{"id": "y", "text": "Clos"}]
        tr._translate_batch(lot, "English", None, retries=1, passes=0)
        ok("JSON  une réponse en clôture ```json est lue",
           lot[0].get("translated_text") == "Fenced", repr(lot))
    finally:
        translation_ai.time.sleep = vrai_sleep

    # ── 5. Construction depuis l'environnement ───────────────────────────
    gardes = {k: os.environ.get(k) for k in
              ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODELS",
               "TRADUCTION_FOURNISSEURS")}
    try:
        os.environ.update(DEEPSEEK_API_KEY="sk-test", OPENROUTER_API_KEY="or-test",
                          OPENROUTER_MODELS="a:free, b:free")
        os.environ.pop("TRADUCTION_FOURNISSEURS", None)
        noms = [f.nom for f in TranslatorAI().fournisseurs]
        ok("ENV  ordre par défaut : DeepSeek puis OpenRouter",
           noms == ["deepseek", "openrouter"], repr(noms))
        f = TranslatorAI().fournisseurs[1]
        ok("ENV  liste de modèles OpenRouter lue",
           (f.modele, f.modeles_secours) == ("a:free", ["b:free"]),
           repr((f.modele, f.modeles_secours)))
        os.environ["TRADUCTION_FOURNISSEURS"] = "openrouter"
        noms = [f.nom for f in TranslatorAI().fournisseurs]
        ok("ENV  TRADUCTION_FOURNISSEURS restreint la chaîne",
           noms == ["openrouter"], repr(noms))
        os.environ.pop("TRADUCTION_FOURNISSEURS")
        os.environ["DEEPSEEK_API_KEY"] = ""
        noms = [f.nom for f in TranslatorAI().fournisseurs]
        ok("ENV  sans clé DeepSeek, OpenRouter seul suffit",
           noms == ["openrouter"], repr(noms))
        os.environ["OPENROUTER_API_KEY"] = ""
        try:
            TranslatorAI()
            leve = False
        except ValueError:
            leve = True
        ok("ENV  aucune clé : erreur explicite au démarrage", leve)
    finally:
        for k, v in gardes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

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
