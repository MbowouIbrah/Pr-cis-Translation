"""Conversion en PDF pour l'aperçu — la couche qui décide de METTRE EN CACHE.

La conversion elle-même vit dans `engines.office` : c'est une capacité, pas une
politique. Ce module y ajoute ce qui relève de l'application — où conserver un
résultat, et quand ne pas le conserver.

L'aperçu côte-à-côte s'appuie sur pdf.js : pour obtenir un rendu EXACT des
formats non-PDF (DOCX, PPTX, TXT), on les convertit en PDF via LibreOffice
headless. La conversion ne sert QUE l'aperçu — le téléchargement garde le
format d'origine.
"""
from __future__ import annotations

import logging
import os

from app.config import PREVIEW_CACHE_DIR, note
from app.core.files import get_file_hash
from engines.office import SOFFICE_PATH, preview_lock, prewarm

__all__ = ["SOFFICE_PATH", "preview_lock", "prewarm", "convert_to_pdf_bytes"]

if SOFFICE_PATH:
    note(logging.INFO, f"LibreOffice  ~  {SOFFICE_PATH}")
else:
    note(logging.WARNING, "LibreOffice introuvable : l'aperçu des formats "
                          "non-PDF sera indisponible.")


def convert_to_pdf_bytes(file_bytes: bytes, ext: str,
                         use_cache: bool = True) -> bytes:
    """Convertit un document en PDF (bytes) pour l'aperçu. Les PDF sont
    renvoyés tels quels. Lève une exception si la conversion échoue.

    `use_cache=False` pour les documents JETABLES et uniques — le PPTX partiel
    d'un job, qui change à chaque diapositive traduite : le mettre en cache
    emplirait `_previews` d'un fichier par état intermédiaire, dont aucun ne
    resservira jamais.
    """
    if ext == "pdf":
        return file_bytes

    cache_path = None
    if use_cache:
        file_hash = get_file_hash(file_bytes)
        os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(PREVIEW_CACHE_DIR, f"{file_hash}.pdf")
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                data = f.read()
            # Servi = récent : la borne du cache efface d'abord les plus anciens.
            try:
                os.utime(cache_path)
            except OSError:
                pass
            return data

    from engines.office import convert_to_pdf
    data = convert_to_pdf(file_bytes, ext)

    if cache_path:
        with open(cache_path, "wb") as f:
            f.write(data)
        from app.services.stockage import limiter_apercus
        limiter_apercus()
    return data
