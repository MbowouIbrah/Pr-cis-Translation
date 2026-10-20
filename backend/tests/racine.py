"""Met `backend/` sur le chemin d'import.

Chaque suite est un SCRIPT autonome (`python backend/tests/test_x.py`), pas un
module de paquet : Python ajoute alors `backend/tests/` au chemin, jamais
`backend/`. Sans cette ligne, `import app` ou `import engines` echoue.

Importer ce module suffit -- l'effet a lieu au chargement :

    import racine  # noqa: F401
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Les suites montent l'application des dizaines de fois : une banniere par
# montage noierait leur sortie, et c'est cette sortie qu'on lit pour savoir ce
# qui a echoue.
os.environ.setdefault("PRECIS_NO_BANNER", "1")

# Aucune suite ne télécharge de police ni ne dépend des polices installées sur
# la machine : le rendu doit être le même partout. Cache des polices vide.
os.environ.setdefault("PRECIS_FONT_DOWNLOAD", "0")
os.environ.setdefault("PRECIS_FONT_SYSTEM", "0")
# Ni le catalogue pré-chargé sur ce poste : il varie d'une machine à l'autre.
os.environ.setdefault("PRECIS_FONT_CATALOGUE", "0")
if "PRECIS_FONTS_CACHE" not in os.environ:
    import atexit
    import shutil
    import tempfile
    _cache_polices = tempfile.mkdtemp(prefix="polices_test_")
    os.environ["PRECIS_FONTS_CACHE"] = _cache_polices
    atexit.register(shutil.rmtree, _cache_polices, ignore_errors=True)
