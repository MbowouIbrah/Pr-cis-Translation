import os
import zipfile
import json
import shutil
import tempfile
import re
import io
from lxml import etree
from pathlib import Path
from collections import defaultdict

from engines import runtags        # contrat des balises de runs (et sa réparation)

NAMESPACES = {
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
}


def _find_soffice_engine() -> str | None:
    """Localise l'exécutable LibreOffice (pour régénérer les aperçus OLE).
    Autonome — le moteur ne dépend pas d'`app` (évite un cycle d'import)."""
    import shutil
    candidates = (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice", "libreoffice",
    )
    for c in candidates:
        if os.path.isfile(c):
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def _is_numeric(val: str) -> bool:
    """Vérifie si une chaîne est une valeur numérique pure (pour ne pas extraire
    les chiffres bruts des axes ou données de graphiques)."""
    try:
        float(val.strip().replace(",", ".").replace(" ", "").replace("%", ""))
        return True
    except ValueError:
        return False


def _auto_refresh_powerpoint(pptx_path: str) -> bool:
    """Sur Windows, ouvre silencieusement le PPTX en arrière-plan via COM/PowerShell
    pour forcer la mise à jour automatique de tous les aperçus d'objets OLE (tableaux/graphiques Excel)."""
    if os.name != 'nt':
        return False
    import subprocess
    abs_path = os.path.abspath(pptx_path)
    if not os.path.exists(abs_path):
        return False

    ps_script = f"""
$ErrorActionPreference = 'Stop'
try {{
    $ppt = New-Object -ComObject PowerPoint.Application
    $pres = $ppt.Presentations.Open('{abs_path}', 0, 0, 0)
    foreach ($slide in $pres.Slides) {{
        foreach ($shape in $slide.Shapes) {{
            if ($shape.Type -eq 7 -or $shape.Type -eq 10 -or $shape.Type -eq 14) {{
                try {{ $shape.OLEFormat.Update() }} catch {{}}
            }}
        }}
    }}
    $pres.Save()
    $pres.Close()
    $ppt.Quit()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($ppt) | Out-Null
    Write-Host "AUTO_REFRESH_SUCCESS"
}} catch {{
    Write-Host "AUTO_REFRESH_ERROR: $_"
    exit 1
}}
"""
    ps_file = os.path.join(os.path.dirname(abs_path), f"_temp_refresh_{os.getpid()}.ps1")
    try:
        with open(ps_file, "w", encoding="utf-8") as f:
            f.write(ps_script)

        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_file]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return "AUTO_REFRESH_SUCCESS" in res.stdout
    except Exception:
        return False
    finally:
        if os.path.exists(ps_file):
            try: os.remove(ps_file)
            except OSError: pass


class PPTXTranslatorEngine:
    """Moteur PPTX — UNE INSTANCE PAR OPÉRATION, JAMAIS PARTAGÉE.

    Tout l'état du moteur tient dans `self.temp_dir` : le PPTX y est décompressé,
    les XML y sont injectés sur place, et `_cleanup_temp()` l'efface. Cet état
    n'est PAS protégé — il ne peut pas l'être, puisque deux traductions
    différentes du même original y écriraient de toute façon dans les mêmes
    fichiers.

    Une instance unique partagée par le processus (ce qu'était `app.pptx_engine`)
    faisait donc collisionner tout ce qui tourne en même temps :

      • deux aperçus concurrents — cas COURANT, l'aperçu lançait lui-même une
        construction de cache en tâche de fond en plus de son propre rendu —
        partageaient le dossier : l'aperçu portugais injectait le portugais dans
        les XML que l'aperçu anglais était en train de re-zipper. On demandait
        l'anglais, on recevait le portugais. Intermittent, donc invisible en
        test manuel et bien réel en usage.
      • pire : le `_cleanup_temp()` d'un aperçu supprimait le dossier temporaire
        d'un JOB de traduction en cours.

    D'où la règle : `new_pptx_engine()` au début de chaque opération, et cette
    instance meurt avec elle.
    """

    # Numéro de « diapositive » des parties PARTAGÉES (slideLayout, slideMaster).
    #
    # Un layout est référencé par plusieurs diapositives, mais c'est UN SEUL
    # fichier. En faisant entrer le numéro de diapositive dans l'identité de ses
    # paragraphes, on en fabriquait autant de copies qu'il y avait de
    # diapositives : traduites une fois chacune (autant d'appels payés), puis
    # injectées tour à tour dans le même fichier — la dernière écrasait les
    # autres. Et en mode progressif, la diapositive 7 extrayait un layout DÉJÀ
    # injecté par la 2 : on traduisait une traduction, avec la dérive que ça
    # suppose.
    #
    # Une partie partagée n'appartient à aucune diapositive : elle porte donc ce
    # numéro fixe, et n'est extraite qu'une fois par document.
    PART_PARTAGEE = 0

    def __init__(self):
        self.temp_dir = None
        # Parties partagées déjà extraites pour CE document (chemins de
        # fichiers). Remis à zéro avec le dossier temporaire.
        self._parts_vues: set[str] = set()

    def generate_autorefresh_pptm(self, pptx_path: str, pptm_output_path: str = None) -> str:
        """Génère une version .pptm (PowerPoint Macro-Enabled) contenant la macro VBA
        Presentation_Open qui rafraîchit automatiquement tous les objets OLE Excel
        dès l'ouverture dans Microsoft PowerPoint."""
        if pptm_output_path is None:
            if pptx_path.lower().endswith(".pptx"):
                pptm_output_path = pptx_path[:-5] + "_autorefresh.pptm"
            else:
                pptm_output_path = pptx_path + "_autorefresh.pptm"

        template_pptm_path = os.path.join(os.path.dirname(__file__), "templates", "template_autorefresh.pptm")

        if not os.path.exists(pptx_path) or not os.path.exists(template_pptm_path):
            return None

        try:
            with zipfile.ZipFile(template_pptm_path, "r") as ztemplate:
                if "ppt/vbaProject.bin" not in ztemplate.namelist():
                    return None
                vba_bin_bytes = ztemplate.read("ppt/vbaProject.bin")

            with zipfile.ZipFile(pptx_path, "r") as xin, zipfile.ZipFile(pptm_output_path, "w", zipfile.ZIP_DEFLATED) as xout:
                for item in xin.infolist():
                    content = xin.read(item.filename)

                    if item.filename == "[Content_Types].xml":
                        tree = etree.fromstring(content)
                        for elem in tree.xpath(".//*[@PartName='/ppt/presentation.xml']"):
                            elem.attrib["ContentType"] = "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"
                        bin_exists = False
                        for elem in tree.xpath(".//*[@Extension='bin']"):
                            bin_exists = True
                        if not bin_exists:
                            default_bin = etree.Element("{http://schemas.openxmlformats.org/package/2006/content-types}Default", Extension="bin", ContentType="application/vnd.ms-office.vbaProject")
                            tree.append(default_bin)
                        content = etree.tostring(tree, encoding="utf-8", xml_declaration=True)

                    if item.filename == "ppt/_rels/presentation.xml.rels":
                        tree = etree.fromstring(content)
                        rel_vba_exists = False
                        for elem in tree.xpath(".//*[@Target='vbaProject.bin']"):
                            rel_vba_exists = True
                        if not rel_vba_exists:
                            rel_elem = etree.Element("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship", Id="rIdVbaProjectAuto", Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject", Target="vbaProject.bin")
                            tree.append(rel_elem)
                        content = etree.tostring(tree, encoding="utf-8", xml_declaration=True)

                    xout.writestr(item, content)

                xout.writestr("ppt/vbaProject.bin", vba_bin_bytes)

            if os.path.exists(pptm_output_path) and os.path.getsize(pptm_output_path) > 0:
                return pptm_output_path
            return None
        except Exception as e:
            print(f"Erreur génération PPTM AutoRefresh: {e}")
            return None

    def _get_temp_dir(self):
        if self.temp_dir is None:
            self.temp_dir = Path(tempfile.mkdtemp(prefix="pptx_trad_"))
        return self.temp_dir

    def _cleanup_temp(self):
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None
        self._parts_vues.clear()

    def _extract_zip(self, pptx_path):
        """Décompresse le PPTX (archive ZIP) dans le dossier temporaire."""
        temp_path = self._get_temp_dir()
        with zipfile.ZipFile(pptx_path, 'r') as zf:
            zf.extractall(temp_path)
        # Nouveau document : les parties partagées sont à revoir.
        self._parts_vues.clear()

    def _part_deja_vue(self, chemin) -> bool:
        """Cette partie PARTAGÉE a-t-elle déjà été extraite ? (et la marque)"""
        cle = str(chemin)
        if cle in self._parts_vues:
            return True
        self._parts_vues.add(cle)
        return False

    def _repack_zip(self, output_path):
        """Re-compresse le dossier temporaire en un fichier PPTX valide."""
        temp_path = self._get_temp_dir()
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(temp_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_path)
                    zf.write(file_path, arcname)

    def extract_text(self, pptx_path, output_json="extraction_texte.json", filters=None, progress_callback=None, pages=None):
        if filters is None:
            filters = {"shapes": True, "smartarts": True, "tables": True, "connectors": True}

        self._cleanup_temp()
        temp_path = self._get_temp_dir()
        temp_path.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback("Décompression du fichier PPTX...")
        try:
            self._extract_zip(pptx_path)
        except Exception as e:
            return None, f"Erreur lors de la décompression : {str(e)}"

        slides_dir = temp_path / "ppt" / "slides"
        if not slides_dir.exists():
            self._cleanup_temp()
            return None, "Le fichier n'est pas un PowerPoint valide."

        slide_files = sorted(slides_dir.glob("slide*.xml"), key=lambda x: int(x.stem.replace("slide", "")))
        
        extraction = {"slides": []}
        element_types_used = defaultdict(set)
        total_slides = len(slide_files)

        for i, slide_path in enumerate(slide_files):
            slide_num = int(slide_path.stem.replace("slide", ""))
            if pages is not None and (i + 1) not in pages:
                continue
            if progress_callback:
                progress_callback(f"Extraction slide {i+1}/{total_slides}...")
                
            tree = etree.parse(str(slide_path))
            root = tree.getroot()

            slide_data = {
                "slide_id": slide_num,
                "text_elements": [],
                "diagram_elements": [],
                "chart_elements": [],
                "layout_elements": [],
                "excel_elements": []
            }

            # 1. Extraction du texte de la slide
            self._process_xml_element(root, slide_num, slide_data["text_elements"], element_types_used, "p", filters)

            rels_path = slides_dir / "_rels" / f"{slide_path.name}.rels"
            if rels_path.exists():
                rels_tree = etree.parse(str(rels_path))

                # 2. SmartArts (Diagrams)
                if filters.get("smartarts"):
                    diag_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData']")
                    for rel in diag_rels:
                        target = rel.get("Target")
                        diag_path = (slides_dir / target).resolve()
                        if diag_path.exists():
                            diag_tree = etree.parse(str(diag_path))
                            diag_root = diag_tree.getroot()
                            diag_id = diag_path.stem
                            diag_data = {"diag_id": diag_id, "text_elements": []}
                            self._process_xml_element(diag_root, slide_num, diag_data["text_elements"], element_types_used, f"diag_{diag_id}", filters)
                            if diag_data["text_elements"]:
                                slide_data["diagram_elements"].append(diag_data)

                # 3. Graphiques XML (Charts)
                if filters.get("tables"):
                    chart_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart']")
                    for rel in chart_rels:
                        target = rel.get("Target")
                        chart_path = (slides_dir / target).resolve()
                        if chart_path.exists():
                            chart_tree = etree.parse(str(chart_path))
                            chart_root = chart_tree.getroot()
                            chart_id = chart_path.stem
                            chart_data = {"chart_id": chart_id, "text_elements": []}
                            self._process_xml_element(chart_root, slide_num, chart_data["text_elements"], element_types_used, f"chart_{chart_id}", filters)
                            if chart_data["text_elements"]:
                                slide_data["chart_elements"].append(chart_data)

                # 4. Modèles de disposition (slideLayouts - pour les sous-titres et masques)
                layout_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout']")
                for rel in layout_rels:
                    target = rel.get("Target")
                    layout_path = (slides_dir / target).resolve()
                    # UNE SEULE FOIS par document : un layout partagé par dix
                    # diapositives était sinon extrait et traduit dix fois.
                    if layout_path.exists() and not self._part_deja_vue(layout_path):
                        layout_tree = etree.parse(str(layout_path))
                        layout_root = layout_tree.getroot()
                        layout_id = layout_path.stem
                        layout_data = {"layout_id": layout_id, "text_elements": []}
                        self._process_xml_element(layout_root, self.PART_PARTAGEE, layout_data["text_elements"], element_types_used, f"layout_{layout_id}", filters)
                        if layout_data["text_elements"]:
                            slide_data["layout_elements"].append(layout_data)

                # 5. Tableaux & Graphiques Excel incorporés (ppt/embeddings/*.xlsx)
                if filters.get("tables"):
                    pkg_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/package']")
                    for rel in pkg_rels:
                        target = rel.get("Target")
                        emb_path = (slides_dir / target).resolve()
                        if emb_path.exists() and emb_path.suffix.lower() in ('.xlsx', '.xlsm'):
                            self._process_excel_file(emb_path, slide_num,
                                                     slide_data["excel_elements"],
                                                     element_types_used)

            # 6. Masques principaux (slideMasters) — hors bloc rels_path
            masters_dir = temp_path / "ppt" / "slideMasters"
            if masters_dir.exists():
                for master_path in sorted(masters_dir.glob("slideMaster*.xml")):
                    # Partagé par TOUTES les diapositives : même règle.
                    if self._part_deja_vue(master_path):
                        continue
                    master_tree = etree.parse(str(master_path))
                    master_root = master_tree.getroot()
                    master_id = master_path.stem
                    master_data = {"master_id": master_id, "text_elements": []}
                    self._process_xml_element(master_root, self.PART_PARTAGEE, master_data["text_elements"], element_types_used, f"master_{master_id}", filters)
                    if master_data["text_elements"]:
                        if "master_elements" not in slide_data:
                            slide_data["master_elements"] = []
                        slide_data["master_elements"].append(master_data)

            if any([slide_data.get("text_elements"), slide_data.get("diagram_elements"),
                    slide_data.get("chart_elements"), slide_data.get("layout_elements"),
                    slide_data.get("master_elements"), slide_data.get("excel_elements")]):
                extraction["slides"].append(slide_data)

        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(extraction, f, ensure_ascii=False, indent=2)

        self._cleanup_temp()
        return extraction, element_types_used

    def _process_excel_file(self, emb_path: Path, slide_num: int, excel_store: list, element_types_used: dict):
        """Extrait les chaînes de texte partagées (xl/sharedStrings.xml) d'un fichier Excel incorporé."""
        try:
            with open(emb_path, "rb") as f:
                excel_bytes = f.read()
            with zipfile.ZipFile(io.BytesIO(excel_bytes), "r") as xzf:
                if "xl/sharedStrings.xml" in xzf.namelist():
                    ss_xml = xzf.read("xl/sharedStrings.xml")
                    tree = etree.fromstring(ss_xml)
                    t_nodes = tree.xpath(".//s:t", namespaces=NAMESPACES)
                    for t_idx, t_node in enumerate(t_nodes):
                        val = t_node.text or ""
                        if val.strip() and not _is_numeric(val):
                            elem_id = f"slide{slide_num}_excel_{emb_path.stem}_ss{t_idx}"
                            excel_store.append({
                                "id": elem_id,
                                "text": f"[[0]]{val}[[/0]]",
                                "context": {"shape_type": "Excel Cell", "paragraph_index": t_idx}
                            })
                            element_types_used[slide_num].add("Excel Cell")
        except Exception:
            pass

    def _process_xml_element(self, root, slide_num, data_store, element_types_used, id_prefix, filters):
        paragraphs = root.xpath(".//a:p", namespaces=NAMESPACES)
        for p_idx, p_elem in enumerate(paragraphs):
            shape_type = "SmartArt (Diagram)" if "diag" in id_prefix else ("Chart" if "chart" in id_prefix else ("Slide Layout" if "layout" in id_prefix else "unknown"))
            if "diag" not in id_prefix and "chart" not in id_prefix and "layout" not in id_prefix:
                parent = p_elem.getparent()
                while parent is not None:
                    tag = parent.tag
                    if tag.endswith("}sp"): shape_type = "shape (sp)"; break
                    elif tag.endswith("}graphicFrame"): shape_type = "graphicFrame"; break
                    elif tag.endswith("}tbl"): shape_type = "tableau (tbl)"; break
                    elif tag.endswith("}cxnSp"): shape_type = "connecteur"; break
                    parent = parent.getparent()

            if shape_type == "shape (sp)" and not filters.get("shapes"): continue
            if shape_type == "tableau (tbl)" and not filters.get("tables"): continue
            if shape_type == "graphicFrame" and not filters.get("tables"): continue
            if shape_type == "connecteur" and not filters.get("connectors"): continue
            if shape_type == "SmartArt (Diagram)" and not filters.get("smartarts"): continue
            if shape_type == "Chart" and not filters.get("tables"): continue

            # Extraction par run <a:r> pour préserver la mise en forme (gras,
            # italique, taille…). Les sauts de ligne <a:br/> sont des siblings
            # des runs et restent intacts dans le XML.
            runs = p_elem.xpath("a:r", namespaces=NAMESPACES)
            tagged_parts = []
            valid_idx = 0

            for r_elem in runs:
                t_elems = r_elem.xpath("a:t", namespaces=NAMESPACES)
                r_text = "".join(t.text for t in t_elems if t.text)
                if r_text.strip():
                    tagged_parts.append(f"[[{valid_idx}]]{r_text}[[/{valid_idx}]]")
                    valid_idx += 1

            # Fallback : paragraphes sans <a:r> mais avec des <a:t> directs
            if not tagged_parts:
                text_nodes = p_elem.xpath(".//a:t", namespaces=NAMESPACES)
                full_text = "".join(t.text for t in text_nodes if t.text)
                if full_text.strip() and not _is_numeric(full_text):
                    tagged_parts.append(f"[[0]]{full_text}[[/0]]")

            if not tagged_parts:
                continue

            # Vérification numérique sur le texte combiné
            combined = re.sub(r"\[\[/?\d+\]\]", "", "".join(tagged_parts))
            if _is_numeric(combined):
                continue

            elem_id = f"slide{slide_num}_{id_prefix}_p{p_idx}"
            data_store.append({
                "id": elem_id,
                "text": "".join(tagged_parts),
                "context": {"shape_type": shape_type, "paragraph_index": p_idx, "run_count": valid_idx}
            })
            element_types_used[slide_num].add(shape_type)

        # Pour les graphiques XML (charts), extraire aussi les étiquettes autonomes (<c:v> / <c:t>)
        if "chart" in id_prefix:
            c_nodes = root.xpath(".//c:v | .//c:t", namespaces=NAMESPACES)
            for v_idx, v_node in enumerate(c_nodes):
                v_text = v_node.text or ""
                if v_text.strip() and not _is_numeric(v_text):
                    elem_id = f"slide{slide_num}_{id_prefix}_cv{v_idx}"
                    data_store.append({
                        "id": elem_id,
                        "text": f"[[0]]{v_text}[[/0]]",
                        "context": {"shape_type": "Chart Label", "paragraph_index": v_idx}
                    })
                    element_types_used[slide_num].add("Chart Label")

    def inject_translation(self, original_pptx, translated_json_path, output_pptx, progress_callback=None, format_options=None):
        if not os.path.exists(translated_json_path):
            return False, "Fichier JSON de traduction introuvable."
        
        if progress_callback:
            progress_callback("Décompression du fichier PPTX...")
        self._extract_zip(original_pptx)
        temp_path = self._get_temp_dir()
        
        with open(translated_json_path, "r", encoding="utf-8") as f:
            translation_data = json.load(f)

        translation_map = {}
        for slide in translation_data.get("slides", []):
            for elem in slide.get("text_elements", []):
                translation_map[elem["id"]] = elem.get("translated_text", elem["text"])
            for diag in slide.get("diagram_elements", []):
                for elem in diag.get("text_elements", []):
                    translation_map[elem["id"]] = elem.get("translated_text", elem["text"])
            for chart in slide.get("chart_elements", []):
                for elem in chart.get("text_elements", []):
                    translation_map[elem["id"]] = elem.get("translated_text", elem["text"])
            for layout_item in slide.get("layout_elements", []):
                for elem in layout_item.get("text_elements", []):
                    translation_map[elem["id"]] = elem.get("translated_text", elem["text"])
            for master_item in slide.get("master_elements", []):
                for elem in master_item.get("text_elements", []):
                    translation_map[elem["id"]] = elem.get("translated_text", elem["text"])
            for elem in slide.get("excel_elements", []):
                translation_map[elem["id"]] = elem.get("translated_text", elem["text"])

        try:
            slides_dir = temp_path / "ppt" / "slides"
            slide_files = sorted(slides_dir.glob("slide*.xml"), key=lambda x: int(x.stem.replace("slide", "")))
            total_slides = len(slide_files)

            for slide_path in slide_files:
                slide_num = int(slide_path.stem.replace("slide", ""))
                if progress_callback:
                    progress_callback(f"Réinjection slide {slide_num}/{total_slides}...")
                
                self._inject_in_xml(slide_path, slide_num, "p", translation_map)

                rels_path = slides_dir / "_rels" / f"{slide_path.name}.rels"
                if rels_path.exists():
                    rels_tree = etree.parse(str(rels_path))
                    
                    # SmartArts
                    diag_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData']")
                    for rel in diag_rels:
                        target = rel.get("Target")
                        diag_path = (slides_dir / target).resolve()
                        if diag_path.exists():
                            diag_id = diag_path.stem
                            self._inject_in_xml(diag_path, slide_num, f"diag_{diag_id}", translation_map)

                    # Charts
                    chart_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart']")
                    for rel in chart_rels:
                        target = rel.get("Target")
                        chart_path = (slides_dir / target).resolve()
                        if chart_path.exists():
                            chart_id = chart_path.stem
                            self._inject_in_xml(chart_path, slide_num, f"chart_{chart_id}", translation_map)

                    # SlideLayouts (sous-titres / masques)
                    layout_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout']")
                    for rel in layout_rels:
                        target = rel.get("Target")
                        layout_path = (slides_dir / target).resolve()
                        if layout_path.exists():
                            layout_id = layout_path.stem
                            # Identité PARTAGÉE ; `num_alt` retrouve les
                            # traductions produites avant ce changement.
                            self._inject_in_xml(layout_path, self.PART_PARTAGEE,
                                                f"layout_{layout_id}",
                                                translation_map,
                                                num_alt=(slide_num,))

                    # Excel incorporés (.xlsx) — injecter dans sharedStrings.xml
                    pkg_rels = rels_tree.xpath("//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/package']")
                    for rel in pkg_rels:
                        target = rel.get("Target")
                        emb_path = (slides_dir / target).resolve()
                        if emb_path.exists() and emb_path.suffix.lower() in ('.xlsx', '.xlsm'):
                            self._inject_excel_file(emb_path, slide_num, translation_map)

            # SlideMasters — UNE injection par masque, pas une par diapositive.
            # La boucle imbriquée réécrivait le même fichier autant de fois
            # qu'il y avait de diapositives, chaque passe écrasant la
            # précédente : seule la dernière comptait, les autres n'étaient que
            # du travail perdu (et des traductions payées pour rien).
            masters_dir = temp_path / "ppt" / "slideMasters"
            if masters_dir.exists():
                nums_anciens = tuple(
                    int(p.stem.replace("slide", "")) for p in slide_files)
                for master_path in sorted(masters_dir.glob("slideMaster*.xml")):
                    master_id = master_path.stem
                    self._inject_in_xml(master_path, self.PART_PARTAGEE,
                                        f"master_{master_id}", translation_map,
                                        num_alt=nums_anciens)

            # Aperçus des objets OLE Excel : régénérés depuis les classeurs
            # traduits (best-effort — voir regenerate_ole_previews). Sans ça, les
            # tableaux/graphiques Excel restent affichés en langue source tant que
            # PowerPoint n'a pas activé l'objet.
            try:
                self.regenerate_ole_previews(progress_callback=progress_callback)
            except Exception:
                pass

            if progress_callback:
                progress_callback("Re-compression du fichier PPTX...")
            self._repack_zip(output_pptx)
            self._cleanup_temp()

            return True, f"Fichier traduit généré : {output_pptx}"

        except Exception as e:
            self._cleanup_temp()
            return False, f"Erreur lors de la réinjection : {str(e)}"

    def _inject_excel_file(self, emb_path: Path, slide_num: int, translation_map: dict) -> bool:
        """Réinjecte la traduction dans le fichier Excel incorporé (xl/sharedStrings.xml) en mémoire."""
        if not emb_path.exists():
            return False
        try:
            with open(emb_path, "rb") as f:
                excel_bytes = f.read()
            
            in_mem = io.BytesIO(excel_bytes)
            out_mem = io.BytesIO()
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            modified = False

            with zipfile.ZipFile(in_mem, "r") as xin, zipfile.ZipFile(out_mem, "w", zipfile.ZIP_DEFLATED) as xout:
                for item in xin.infolist():
                    content = xin.read(item.filename)
                    if item.filename == "xl/sharedStrings.xml":
                        tree = etree.fromstring(content)
                        t_nodes = tree.xpath(".//s:t", namespaces=ns)
                        for t_idx, t_node in enumerate(t_nodes):
                            elem_id = f"slide{slide_num}_excel_{emb_path.stem}_ss{t_idx}"
                            if elem_id in translation_map:
                                tr_text = translation_map[elem_id]
                                clean_text = re.sub(r"\[\[\d+\]\](.*?)\[\[/\d+\]\]", r"\1", tr_text)
                                t_node.text = clean_text
                                modified = True
                        content = etree.tostring(tree, encoding="utf-8", xml_declaration=True)
                    xout.writestr(item, content)

            if modified:
                with open(emb_path, "wb") as f:
                    f.write(out_mem.getvalue())
            return modified
        except Exception:
            return False

    def _inject_in_xml(self, xml_path, slide_num, id_prefix, translation_map,
                       num_alt=()):
        """`num_alt` : autres numéros sous lesquels chercher le paragraphe.

        Sert aux parties PARTAGÉES, dont l'identité ne porte plus le numéro de
        diapositive. Les traductions produites avant ce changement les nomment
        encore `slide{N}_layout_…` : sans ce repli, elles ne seraient plus
        retrouvées et le layout resterait en langue source.
        """
        tree = etree.parse(str(xml_path))
        root = tree.getroot()
        modified = False

        paragraphs = root.xpath(".//a:p", namespaces=NAMESPACES)
        for p_idx, p_elem in enumerate(paragraphs):
            elem_id = f"slide{slide_num}_{id_prefix}_p{p_idx}"
            if elem_id not in translation_map:
                for n in num_alt:
                    candidat = f"slide{n}_{id_prefix}_p{p_idx}"
                    if candidat in translation_map:
                        elem_id = candidat
                        break
            if elem_id in translation_map:
                translated_text = translation_map[elem_id]

                # Réinjection par run <a:r> — même parcours que l'extraction
                runs = p_elem.xpath("a:r", namespaces=NAMESPACES)
                if runs:
                    # On relève d'abord les runs PORTEURS et leur texte source,
                    # dans l'ordre : c'est le contrat que la traduction doit
                    # honorer, et `runtags` le fait honorer coûte que coûte.
                    porteurs = []          # [(r_elem, t_elems, texte_source)]
                    for r_elem in runs:
                        t_elems = r_elem.xpath("a:t", namespaces=NAMESPACES)
                        r_text = "".join(t.text for t in t_elems if t.text)
                        if r_text.strip():
                            porteurs.append((r_elem, t_elems, r_text))

                    # Le code précédent ne remplaçait QUE les runs présents dans
                    # la réponse. Un run oublié par le modèle gardait donc son
                    # texte SOURCE, et la diapositive affichait la traduction
                    # collée à l'original (« DAY 2 OF TRAINING° JOUR DE
                    # FORMATION »). `repartir` rend toujours un texte PAR RUN.
                    textes = runtags.repartir([p[2] for p in porteurs],
                                              translated_text)
                    for (_r, t_elems, _src), nouveau in zip(porteurs, textes):
                        if t_elems:
                            t_elems[0].text = nouveau
                            for extra_t in t_elems[1:]:
                                extra_t.text = ""
                    modified = True
                else:
                    # Fallback : pas de runs, injection directe dans les <a:t>
                    text_nodes_direct = p_elem.xpath(".//a:t", namespaces=NAMESPACES)
                    if text_nodes_direct:
                        clean_text = runtags.sans_balises(translated_text)
                        text_nodes_direct[0].text = clean_text
                        for i in range(1, len(text_nodes_direct)):
                            text_nodes_direct[i].text = ""
                        modified = True

        if "chart" in id_prefix:
            c_nodes = root.xpath(".//c:v | .//c:t", namespaces=NAMESPACES)
            for v_idx, v_node in enumerate(c_nodes):
                elem_id = f"slide{slide_num}_{id_prefix}_cv{v_idx}"
                if elem_id in translation_map:
                    # `sans_balises` retire AUSSI les balises orphelines : une
                    # ouverture sans fermeture traversait l'ancienne regex et
                    # s'affichait telle quelle dans le graphique.
                    v_node.text = runtags.sans_balises(translation_map[elem_id])
                    modified = True

        if modified:
            with open(xml_path, "wb") as f:
                f.write(etree.tostring(tree, encoding="utf-8", xml_declaration=True))
        return modified

    # ── Méthodes pour le pipeline PROGRESSIF (slide par slide) ──────────────

    def _slide_path(self, slide_num: int):
        """Chemin du fichier XML de la slide N dans le dossier temporaire."""
        return self._get_temp_dir() / "ppt" / "slides" / f"slide{slide_num}.xml"

    def _rels_path(self, slide_num: int):
        """Chemin du fichier .rels de la slide N."""
        return self._get_temp_dir() / "ppt" / "slides" / "_rels" / f"slide{slide_num}.xml.rels"

    def slide_count(self) -> int:
        """Nombre de slides dans le PPTX déjà décompressé."""
        slides_dir = self._get_temp_dir() / "ppt" / "slides"
        if not slides_dir.exists():
            return 0
        return len(list(slides_dir.glob("slide*.xml")))

    def extract_slide(self, slide_num: int, filters: dict | None = None):
        """Extrait le texte d'une SEULE slide déjà décompressée."""
        if filters is None:
            filters = {"shapes": True, "smartarts": True, "tables": True, "connectors": True}

        slide_path = self._slide_path(slide_num)
        if not slide_path.exists():
            return None, f"Slide {slide_num} introuvable."

        tree = etree.parse(str(slide_path))
        root = tree.getroot()

        slide_data = {
            "slide_id": slide_num,
            "text_elements": [],
            "diagram_elements": [],
            "chart_elements": [],
            "layout_elements": [],
            "excel_elements": []
        }
        element_types_used = defaultdict(set)

        self._process_xml_element(root, slide_num, slide_data["text_elements"],
                                  element_types_used, "p", filters)

        rels_path = self._rels_path(slide_num)
        if rels_path.exists():
            rels_tree = etree.parse(str(rels_path))
            slides_dir = self._get_temp_dir() / "ppt" / "slides"

            if filters.get("smartarts"):
                diag_rels = rels_tree.xpath(
                    "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData']")
                for rel in diag_rels:
                    target = rel.get("Target")
                    diag_path = (slides_dir / target).resolve()
                    if diag_path.exists():
                        diag_tree = etree.parse(str(diag_path))
                        diag_root = diag_tree.getroot()
                        diag_id = diag_path.stem
                        diag_data = {"diag_id": diag_id, "text_elements": []}
                        self._process_xml_element(diag_root, slide_num, diag_data["text_elements"],
                                                  element_types_used, f"diag_{diag_id}", filters)
                        if diag_data["text_elements"]:
                            slide_data["diagram_elements"].append(diag_data)

            if filters.get("tables"):
                chart_rels = rels_tree.xpath(
                    "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart']")
                for rel in chart_rels:
                    target = rel.get("Target")
                    chart_path = (slides_dir / target).resolve()
                    if chart_path.exists():
                        chart_tree = etree.parse(str(chart_path))
                        chart_root = chart_tree.getroot()
                        chart_id = chart_path.stem
                        chart_data = {"chart_id": chart_id, "text_elements": []}
                        self._process_xml_element(chart_root, slide_num, chart_data["text_elements"],
                                                  element_types_used, f"chart_{chart_id}", filters)
                        if chart_data["text_elements"]:
                            slide_data["chart_elements"].append(chart_data)

            # SlideLayouts — extraits UNE FOIS par document.
            #
            # En progressif, c'était le pire des cas : la diapositive 2
            # extrayait le layout, le traduisait, l'injectait ; la 7 relisait ce
            # MÊME fichier, désormais traduit, et le renvoyait à la traduction.
            # On traduisait une traduction, et la dernière diapositive écrasait
            # le travail de toutes les autres.
            layout_rels = rels_tree.xpath(
                "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout']")
            for rel in layout_rels:
                target = rel.get("Target")
                layout_path = (slides_dir / target).resolve()
                if layout_path.exists() and not self._part_deja_vue(layout_path):
                    layout_tree = etree.parse(str(layout_path))
                    layout_root = layout_tree.getroot()
                    layout_id = layout_path.stem
                    layout_data = {"layout_id": layout_id, "text_elements": []}
                    self._process_xml_element(layout_root, self.PART_PARTAGEE, layout_data["text_elements"],
                                              element_types_used, f"layout_{layout_id}", filters)
                    if layout_data["text_elements"]:
                        slide_data["layout_elements"].append(layout_data)

            # Excel incorporés
            if filters.get("tables"):
                pkg_rels = rels_tree.xpath(
                    "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/package']")
                for rel in pkg_rels:
                    target = rel.get("Target")
                    emb_path = (slides_dir / target).resolve()
                    if emb_path.exists() and emb_path.suffix.lower() in ('.xlsx', '.xlsm'):
                        self._process_excel_file(emb_path, slide_num, slide_data["excel_elements"], element_types_used)

        if not any([slide_data["text_elements"], slide_data["diagram_elements"],
                    slide_data["chart_elements"], slide_data["layout_elements"],
                    slide_data["excel_elements"]]):
            return None, "Aucun texte trouvé dans cette slide."
        return slide_data, element_types_used

    def inject_slide(self, slide_num: int, translation_map: dict):
        """Injecte la traduction dans les XML d'une SEULE slide déjà décompressée."""
        slide_path = self._slide_path(slide_num)
        if not slide_path.exists():
            return False
        modified = self._inject_in_xml(slide_path, slide_num, "p", translation_map)

        rels_path = self._rels_path(slide_num)
        if rels_path.exists():
            rels_tree = etree.parse(str(rels_path))
            slides_dir = self._get_temp_dir() / "ppt" / "slides"

            diag_rels = rels_tree.xpath(
                "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData']")
            for rel in diag_rels:
                target = rel.get("Target")
                diag_path = (slides_dir / target).resolve()
                if diag_path.exists():
                    diag_id = diag_path.stem
                    if self._inject_in_xml(diag_path, slide_num, f"diag_{diag_id}", translation_map):
                        modified = True
            
            chart_rels = rels_tree.xpath(
                "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart']")
            for rel in chart_rels:
                target = rel.get("Target")
                chart_path = (slides_dir / target).resolve()
                if chart_path.exists():
                    chart_id = chart_path.stem
                    if self._inject_in_xml(chart_path, slide_num, f"chart_{chart_id}", translation_map):
                        modified = True

            layout_rels = rels_tree.xpath(
                "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout']")
            for rel in layout_rels:
                target = rel.get("Target")
                layout_path = (slides_dir / target).resolve()
                if layout_path.exists():
                    layout_id = layout_path.stem
                    if self._inject_in_xml(layout_path, self.PART_PARTAGEE,
                                           f"layout_{layout_id}",
                                           translation_map,
                                           num_alt=(slide_num,)):
                        modified = True

            pkg_rels = rels_tree.xpath(
                "//*[@Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/package']")
            for rel in pkg_rels:
                target = rel.get("Target")
                emb_path = (slides_dir / target).resolve()
                if emb_path.exists() and emb_path.suffix.lower() in ('.xlsx', '.xlsm'):
                    if self._inject_excel_file(emb_path, slide_num, translation_map):
                        modified = True

        return bool(modified)

    def build_partial_pptx(self, output_path: str, up_to_slide: int,
                           only_slides: set[int] | None = None):
        """Construit un PPTX partiel depuis le dossier temporaire, SANS JAMAIS
        LE MODIFIER.

        `only_slides` : les seules slides à garder. Par défaut, toutes celles
        jusqu'à `up_to_slide`. Sert à extraire UNE slide isolée pour la convertir
        seule en PDF — l'aperçu progressif ne peut pas se permettre de reconvertir
        tout le deck à chaque diapositive terminée (cf. app.py).

        Non-destructif À DESSEIN : cette méthode est appelée plusieurs fois
        pendant un même job (aperçu progressif, une fois par slide traitée) puis
        une dernière fois pour le fichier final. L'ancienne version réécrivait
        `presentation.xml`, ses rels et `[Content_Types].xml` DANS le dossier
        temporaire en retirant les slides > up_to_slide : au deuxième appel, les
        slides déjà retirées avaient disparu du temp dir et ne revenaient jamais
        — le PPTX final se serait retrouvé amputé de toutes ses slides sauf la
        première. On calcule donc les fichiers de contrôle trimmés EN MÉMOIRE et
        on les écrit directement dans le zip ; le temp dir reste intact pour la
        suite du traitement (et pour les appels suivants).

        Parcourt TOUS les fichiers réels du dossier temporaire (pas seulement
        ceux listés dans [Content_Types].xml : les .rels et certains médias n'y
        figurent pas). Écriture ATOMIQUE (tmp + rename) : un lecteur (conversion
        d'aperçu) ne voit jamais un zip à moitié écrit."""
        temp_path = self._get_temp_dir()

        # Parcourir TOUS les fichiers réels du dossier temporaire
        all_files: list[str] = []
        for root, _dirs, files in os.walk(temp_path):
            for fn in files:
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, temp_path).replace("\\", "/")
                all_files.append(rel)
        all_set = set(all_files)

        # Numéros de TOUTES les slides réellement présentes. On ne s'arrête plus
        # au premier trou : `only_slides` peut ne garder qu'une slide isolée, et
        # tout ce qui n'est pas gardé doit être exclu, pas seulement la queue.
        toutes = set()
        for rel in all_set:
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", rel)
            if m:
                toutes.add(int(m.group(1)))

        garder = (set(only_slides) if only_slides is not None
                  else {n for n in toutes if n <= up_to_slide})
        garder &= toutes

        exclus = toutes - garder

        def _excluded(rel: str) -> bool:
            """La slide est identifiée par son NUMÉRO, jamais par un préfixe.

            Un test `startswith("ppt/slides/slide1")` retire aussi slide12 et
            slide19. Invisible tant qu'on ne coupait qu'une queue contiguë
            (slide1 exclue ⇒ tout l'est) ; faux dès qu'on garde une slide isolée.
            """
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", rel)
            if m is None:
                m = re.fullmatch(r"ppt/slides/_rels/slide(\d+)\.xml\.rels", rel)
            return m is not None and int(m.group(1)) in exclus

        include = [rel for rel in all_files if not _excluded(rel)]

        # ── Fichiers de CONTRÔLE recalculés EN MÉMOIRE (jamais réécrits sur disque) ──
        control_bytes: dict[str, bytes] = {}

        # 1. ppt/_rels/presentation.xml.rels — retirer les relations des slides exclues
        rels_rel = "ppt/_rels/presentation.xml.rels"
        rels_path = temp_path / "ppt" / "_rels" / "presentation.xml.rels"
        r_id_to_slide_num: dict[str, int] = {}
        if rels_path.exists():
            rels_tree = etree.parse(str(rels_path))
            rels_root = rels_tree.getroot()
            slide_rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
            for rel in list(rels_root):
                if rel.get("Type") == slide_rel_type:
                    m = re.search(r"slide(\d+)\.xml", rel.get("Target", ""))
                    if m:
                        slide_num = int(m.group(1))
                        if slide_num in garder:
                            r_id_to_slide_num[rel.get("Id")] = slide_num
                        else:
                            rels_root.remove(rel)
            control_bytes[rels_rel] = etree.tostring(
                rels_tree, encoding="utf-8", xml_declaration=True)

        # 2. ppt/presentation.xml — ne garder dans sldIdLst que les slides conservées
        pres_rel = "ppt/presentation.xml"
        pres_path = temp_path / "ppt" / "presentation.xml"
        if pres_path.exists():
            pres_tree = etree.parse(str(pres_path))
            sld_id_list = pres_tree.xpath("//p:sldIdLst", namespaces=NAMESPACES)
            if sld_id_list:
                sld_list = sld_id_list[0]
                for el in list(sld_list):
                    r_id = el.get(f"{{{NAMESPACES['r']}}}id")
                    if r_id not in r_id_to_slide_num:
                        sld_list.remove(el)
            control_bytes[pres_rel] = etree.tostring(
                pres_tree, encoding="utf-8", xml_declaration=True)

        # 3. [Content_Types].xml — retirer les Override des slides exclues
        ct_rel = "[Content_Types].xml"
        content_types_path = temp_path / "[Content_Types].xml"
        if content_types_path.exists():
            ct_tree = etree.parse(str(content_types_path))
            types_elem = ct_tree.getroot()
            for ov in list(types_elem):
                part_name = ov.get("PartName", "")
                m = re.fullmatch(r"/ppt/slides/slide(\d+)\.xml", part_name)
                if m and int(m.group(1)) not in garder:
                    types_elem.remove(ov)
            control_bytes[ct_rel] = etree.tostring(
                ct_tree, encoding="utf-8", xml_declaration=True)

        # 4. Numéros de diapositive FIGÉS. Le champ `<a:fld type="slidenum">`
        # est recalculé au rendu d'après la position dans le deck PARTIEL : une
        # slide isolée (aperçu progressif) s'affichait « 1 » quelle que soit sa
        # place. On le remplace par un run portant son numéro dans le deck
        # COMPLET (ordre de sldIdLst + firstSlideNum), seulement quand des
        # slides sont exclues : le fichier final garde ses champs vivants.
        if exclus and rels_path.exists() and pres_path.exists():
            control_bytes.update(
                self._figer_numeros(temp_path, rels_path, pres_path, garder))

        # ── Reconstruire le ZIP (atomique) : contrôle depuis la mémoire, le reste du disque ──
        tmp_out = output_path + ".tmp"
        with zipfile.ZipFile(tmp_out, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in sorted(include):
                if rel in control_bytes:
                    zf.writestr(rel, control_bytes[rel])
                    continue
                part_path = temp_path / rel
                if part_path.exists():
                    zf.write(part_path, rel)
        os.replace(tmp_out, output_path)

    @staticmethod
    def _figer_numeros(temp_path, rels_path, pres_path,
                       garder: set[int]) -> dict[str, bytes]:
        """XML des slides gardées dont les champs slidenum portent leur numéro
        dans le deck complet. Lit les fichiers d'ORIGINE (non trimmés)."""
        slide_rel_type = ("http://schemas.openxmlformats.org/officeDocument/"
                          "2006/relationships/slide")
        rid_to_num: dict[str, int] = {}
        for rel in etree.parse(str(rels_path)).getroot():
            if rel.get("Type") == slide_rel_type:
                m = re.search(r"slide(\d+)\.xml", rel.get("Target", ""))
                if m:
                    rid_to_num[rel.get("Id")] = int(m.group(1))
        pres_root = etree.parse(str(pres_path)).getroot()
        try:
            premier = int(pres_root.get("firstSlideNum", "1"))
        except ValueError:
            premier = 1
        affiche: dict[int, int] = {}
        for pos, el in enumerate(pres_root.xpath(
                "//p:sldIdLst/p:sldId", namespaces=NAMESPACES)):
            num = rid_to_num.get(el.get(f"{{{NAMESPACES['r']}}}id"))
            if num is not None:
                affiche[num] = premier + pos

        a = NAMESPACES["a"]
        out: dict[str, bytes] = {}
        for num in garder:
            if num not in affiche:
                continue
            path = temp_path / "ppt" / "slides" / f"slide{num}.xml"
            if not path.exists():
                continue
            tree = etree.parse(str(path))
            champs = tree.xpath('//a:fld[@type="slidenum"]',
                                namespaces=NAMESPACES)
            if not champs:
                continue
            for fld in champs:
                run = etree.Element(f"{{{a}}}r")
                rpr = fld.find(f"{{{a}}}rPr")
                if rpr is not None:
                    run.append(rpr)
                t = etree.SubElement(run, f"{{{a}}}t")
                t.text = str(affiche[num])
                fld.getparent().replace(fld, run)
            out[f"ppt/slides/slide{num}.xml"] = etree.tostring(
                tree, encoding="utf-8", xml_declaration=True, standalone=True)
        return out

    # ── Aperçus des objets OLE Excel ─────────────────────────────────────────
    #
    # PROBLÈME : un classeur Excel incorporé dans un PPTX est affiché via une
    # IMAGE de remplacement (.emf) générée par Office à la création. Cette image
    # n'est JAMAIS régénérée par une modification XML : on traduit bien les
    # cellules du xlsx incorporé, mais l'aperçu visuel reste en langue d'origine
    # tant que PowerPoint n'active pas l'objet. Mesuré : le xlsx dit « Theoretical
    # test during training », l'EMF montre encore « Test théorique durant
    # formation ». LibreOffice, à l'affichage comme à l'export PDF, rend cette
    # même EMF périmée — donc l'aperçu de l'app est touché autant que PowerPoint.
    #
    # SOLUTION (sans licence Windows, sans macro) : on rend le classeur TRADUIT
    # en image avec LibreOffice, on recadre au contenu, et on remplace le média
    # de remplacement. Un seul correctif rend l'aperçu ET le fichier téléchargé
    # cohérents. L'ancienne piste COM/VBA (`_auto_refresh_powerpoint`,
    # `generate_autorefresh_pptm`) est abandonnée : elle exigeait PowerPoint et
    # déclenchait l'avertissement de sécurité macro.

    def regenerate_ole_previews(self, soffice_path: str | None = None,
                                progress_callback=None,
                                only_slides: set[int] | None = None) -> int:
        """Régénère l'image de remplacement de chaque objet OLE Excel depuis le
        classeur TRADUIT (déjà réinjecté dans le dossier temporaire). À appeler
        APRÈS l'injection, AVANT le repack. Retourne le nombre d'aperçus refaits.

        `only_slides` — ne traiter que ces diapositives. L'aperçu progressif
        affiche une diapositive dès qu'elle est traduite : sans ce filtre, il
        faudrait attendre la fin du document pour que les tableaux Excel
        passent en langue cible, et une diapositive « terminée » montrerait
        encore un tableau en langue source. Le classeur d'une diapositive est
        traduit en même temps qu'elle : on peut donc régénérer son aperçu tout
        de suite.

        Best-effort : en cas d'échec (LibreOffice absent, classeur illisible),
        on laisse l'EMF d'origine plutôt que d'interrompre la traduction."""
        if soffice_path is None:
            soffice_path = _find_soffice_engine()
        if not soffice_path:
            return 0
        temp_path = self._get_temp_dir()
        slides_dir = temp_path / "ppt" / "slides"
        if not slides_dir.exists():
            return 0

        R = NAMESPACES['r']
        # 1. Collecte : pour chaque graphicFrame OLE Excel, le couple
        #    (classeur incorporé, image de remplacement) via le XML + les rels.
        tasks = []
        rels_cache = {}
        for slide_path in sorted(slides_dir.glob("slide*.xml")):
            if only_slides is not None:
                m = re.fullmatch(r"slide(\d+)\.xml", slide_path.name)
                if m is None or int(m.group(1)) not in only_slides:
                    continue
            rels_path = slides_dir / "_rels" / (slide_path.name + ".rels")
            if not rels_path.exists():
                continue
            try:
                rels_tree = etree.parse(str(rels_path))
                slide_tree = etree.parse(str(slide_path))
            except Exception:
                continue
            rid_to_target = {r.get("Id"): r.get("Target")
                             for r in rels_tree.getroot()}
            found = False
            for gf in slide_tree.iter("{%s}graphicFrame" % NAMESPACES['p']):
                ole = gf.find(".//p:oleObj", NAMESPACES)
                if ole is None or "Excel" not in (ole.get("progId") or ""):
                    continue
                blip = gf.find(".//a:blip", NAMESPACES)
                if blip is None:
                    continue
                xlsx_rel = rid_to_target.get(ole.get("{%s}id" % R))
                img_rid = blip.get("{%s}embed" % R)
                img_rel = rid_to_target.get(img_rid)
                if not xlsx_rel or not img_rel:
                    continue
                xlsx_path = (slides_dir / xlsx_rel).resolve()
                if (not xlsx_path.exists()
                        or xlsx_path.suffix.lower() not in (".xlsx", ".xlsm")):
                    continue
                # Proportions du CADRE d'affichage : PowerPoint y étire l'image
                # de remplacement sans se soucier de ses proportions propres.
                # Une image aux mauvaises proportions y apparaît déformée — et
                # c'est ce qui donnait des tableaux étalés sur toute la largeur.
                ratio = None
                ext = gf.find("./p:xfrm/a:ext", NAMESPACES)
                if ext is None:
                    ext = gf.find(".//a:ext", NAMESPACES)
                try:
                    cx, cy = int(ext.get("cx")), int(ext.get("cy"))
                    if cx > 0 and cy > 0:
                        ratio = cx / cy
                except (AttributeError, TypeError, ValueError):
                    ratio = None            # cadre illisible : pas de complément
                tasks.append({"xlsx": xlsx_path, "img_rel": img_rel,
                              "img_rid": img_rid, "rels_path": str(rels_path),
                              "ratio": ratio})
                found = True
            if found:
                rels_cache[str(rels_path)] = rels_tree
        if not tasks:
            return 0

        # 2. Rendu de TOUS les classeurs uniques en une SEULE invocation
        #    LibreOffice (le coût de démarrage est amorti sur tous les fichiers).
        import subprocess
        uniq = sorted({str(t["xlsx"]) for t in tasks})
        # Ratio du cadre par classeur. Un même classeur affiché dans deux cadres
        # de proportions différentes est un cas tordu et rare : on prend le
        # premier vu, plutôt que de rendre deux fois.
        ratio_de = {}
        for t in tasks:
            ratio_de.setdefault(str(t["xlsx"]), t.get("ratio"))
        png_for = {}
        # `ignore_cleanup_errors` : le profil LibreOffice (`prof/user/…`) créé
        # ici reste verrouillé par Windows un instant après la conversion.
        # Sans ce drapeau, une régénération RÉUSSIE levait `PermissionError`
        # au nettoyage — l'aperçu était produit puis perdu sur une erreur.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            # On convertit des COPIES ajustées à une page, pas les classeurs
            # livrés : sans cela LibreOffice pagine, et tout ce qui tombe au-delà
            # de la première page disparaît de l'aperçu.
            rendu_de = {}
            for i, x in enumerate(uniq):
                copie = os.path.join(td, f"rendu{i}_{Path(x).stem}.xlsx")
                rendu_de[x] = copie if self._xlsx_ajuste_une_page(x, copie) else x
            # PROFIL PARTAGE. Cette conversion construisait le sien dans `td`,
            # donc payait 4,3 s de démarrage à CHAQUE appel (mesuré). Avec
            # l'aperçu progressif, elle est appelée une fois par diapositive
            # porteuse d'un classeur : le profil jetable la rendait inabordable.
            from engines.office import profil_argument
            cmd = ([soffice_path, "--headless", "--norestore", "--nolockcheck",
                    profil_argument(),
                    "--convert-to", "pdf", "--outdir", td]
                   + list(rendu_de.values()))
            # MÊME VERROU que les autres conversions. LibreOffice ne supporte
            # pas deux invocations concurrentes, et cet appel-ci s'en
            # affranchissait : il pouvait tomber en même temps que la
            # conversion d'un aperçu.
            #
            # Le verrou vient d'`engines.office`, un VOISIN. Il venait
            # auparavant de l'application, par un import tardif destiné à
            # masquer le cycle : un moteur qui importe son application n'est
            # plus réutilisable ailleurs.
            from engines.office import preview_lock
            try:
                with preview_lock:
                    subprocess.run(cmd, capture_output=True, timeout=600)
            except Exception:
                return 0
            for x in uniq:
                pdf = os.path.join(td, Path(rendu_de[x]).stem + ".pdf")
                if os.path.exists(pdf):
                    png_for[x] = self._crop_xlsx_pdf_to_png(
                        pdf, ratio_cible=ratio_de.get(x))

        # 3. Écrire les PNG et construire la carte {image_emf → image_png}.
        media_dir = slides_dir.parent / "media"
        media_dir.mkdir(exist_ok=True)
        emf_to_png = {}
        for t in tasks:
            png = png_for.get(str(t["xlsx"]))
            if not png:
                continue
            emf_name = t["img_rel"].rsplit("/", 1)[-1]        # image37.emf
            png_name = Path(emf_name).stem + ".png"           # image37.png
            with open(media_dir / png_name, "wb") as f:
                f.write(png)
            emf_to_png[emf_name] = png_name
        if not emf_to_png:
            return 0

        # 4. Repointer TOUTES les relations qui référencent ces EMF vers le PNG.
        #    CRUCIAL : un objet OLE est affiché via la branche `mc:Choice` (VML),
        #    dont l'image est référencée par les rels du `vmlDrawing`, PAS par le
        #    blip DrawingML du `mc:Fallback`. Ne repointer que le fallback (ce
        #    qu'on faisait) laissait PowerPoint ET LibreOffice afficher l'EMF
        #    d'origine, en langue source. On balaie donc TOUS les .rels du
        #    paquet et on repointe par nom de fichier — les deux branches suivent.
        IMG_TYPE = ("http://schemas.openxmlformats.org/officeDocument/2006/"
                    "relationships/image")
        for rels_file in temp_path.rglob("*.rels"):
            try:
                tree = etree.parse(str(rels_file))
            except Exception:
                continue
            changed = False
            for rel in tree.getroot():
                tgt = rel.get("Target") or ""
                base = tgt.rsplit("/", 1)[-1]
                if base in emf_to_png:
                    rel.set("Target", tgt[:len(tgt) - len(base)] + emf_to_png[base])
                    rel.set("Type", IMG_TYPE)
                    changed = True
            if changed:
                tree.write(str(rels_file), xml_declaration=True, encoding="UTF-8")

        # 5. Supprimer les EMF devenus orphelins (plus aucune relation n'y
        #    pointe) : une part non référencée fait afficher à PowerPoint un
        #    avertissement de réparation.
        for emf_name in emf_to_png:
            old = media_dir / emf_name
            try:
                if old.exists():
                    old.unlink()
            except OSError:
                pass

        self._ensure_png_content_type(temp_path)
        if progress_callback:
            progress_callback(f"{len(emf_to_png)} aperçu(s) Excel régénéré(s)")
        return len(emf_to_png)

    def _xlsx_ajuste_une_page(self, src: str, dst: str) -> bool:
        """Copie le classeur en forçant l'impression sur UNE SEULE PAGE.

        POURQUOI. Un classeur incorporé tient couramment un tableau ET un
        graphique posés côte à côte dans la feuille. LibreOffice imprime cette
        feuille sur du A4 : trop étroit, il PAGINE — le tableau page 1, le
        graphique page 2. On ne rendait que la première page : le graphique
        disparaissait de l'aperçu, et l'image restante, bien plus étroite que le
        cadre d'origine, s'y affichait ÉTIRÉE (mesuré : classeur rendu en ratio
        1,2 posé dans un cadre de ratio 2,33).

        `fitToPage` est le mécanisme OOXML prévu pour ça, et LibreOffice le
        respecte : la feuille entière tient sur une page, dans sa disposition
        réelle. Vérifié sur le classeur qui a révélé le défaut : 2 pages → 1,
        graphique inclus.

        La modification ne touche QUE la copie de rendu. Le classeur livré dans
        le PPTX garde ses réglages d'impression : ce sont ceux de l'utilisateur,
        on n'a pas à en décider.
        """
        S = NAMESPACES['s']
        try:
            with zipfile.ZipFile(src) as zin, \
                    zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if (item.filename.startswith("xl/worksheets/sheet")
                            and item.filename.endswith(".xml")):
                        data = self._forcer_fit_to_page(data, S)
                    zout.writestr(item, data)
            return True
        except Exception:
            return False

    @staticmethod
    def _forcer_fit_to_page(xml_bytes: bytes, S: str) -> bytes:
        """`fitToPage` + paysage sur une feuille de calcul.

        L'ORDRE DES ÉLÉMENTS COMPTE dans un schéma OOXML : `pageSetup` se place
        après le contenu de la feuille, `sheetPr` tout au début. Un élément mal
        placé rend le classeur illisible — on les insère donc aux bons endroits
        plutôt qu'en fin d'arbre.
        """
        root = etree.fromstring(xml_bytes)
        sheet_pr = root.find("{%s}sheetPr" % S)
        if sheet_pr is None:
            sheet_pr = etree.Element("{%s}sheetPr" % S)
            root.insert(0, sheet_pr)
        setup_pr = sheet_pr.find("{%s}pageSetUpPr" % S)
        if setup_pr is None:
            setup_pr = etree.SubElement(sheet_pr, "{%s}pageSetUpPr" % S)
        setup_pr.set("fitToPage", "1")

        page_setup = root.find("{%s}pageSetup" % S)
        if page_setup is None:
            page_setup = etree.SubElement(root, "{%s}pageSetup" % S)
        page_setup.set("fitToWidth", "1")
        page_setup.set("fitToHeight", "1")
        page_setup.set("orientation", "landscape")
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    def _crop_xlsx_pdf_to_png(self, pdf_path: str, dpi: int = 150,
                              margin: int = 8,
                              ratio_cible: float | None = None) -> bytes | None:
        """Rend le PDF d'un classeur en PNG, recadré au contenu.

        TOUTES les pages sont rendues, empilées verticalement. `fitToPage` en
        produit normalement une seule ; s'il en reste plusieurs (zones
        d'impression multiples), les empiler garde le contenu — en jeter était
        la cause de la disparition du graphique.

        Le recadrage reste indispensable : LibreOffice pose le contenu dans un
        coin de la page, sans lui l'aperçu serait à 90 % du blanc.

        `ratio_cible` — proportions du cadre d'affichage dans la diapositive.
        L'image y est complétée pour les atteindre, jamais rognée : PowerPoint
        étire l'image sur tout le cadre, et une image aux mauvaises proportions
        y apparaît déformée. Compléter, c'est laisser un peu d'air ; rogner, ce
        serait perdre du contenu.

        L'image produite est TRANSPARENTE là où rien n'est peint. Un aperçu OLE
        remplace une image VECTORIELLE (EMF) qui ne peint que ses traits : ce
        qu'un utilisateur a posé sous le cadre — flèche, filigrane, bandeau —
        reste visible à travers. Un aplat opaque le masquerait. MESURÉ sur une
        diapositive réelle : la flèche « Groupe 13 », dessinée avant le cadre
        OLE et située dedans, disparaissait sous notre blanc ; et l'EMF
        d'origine ne contient AUCUN rectangle couvrant son cadre. MESURÉ aussi
        côté LibreOffice : son PDF ne peint pas de fond (0 dessin rempli
        couvrant la page, 99,9 % des pixels à alpha=0 en rendu alpha=True) —
        d'où le rendu direct en RGBA, sans détourage à deviner.

        Corollaire : le contenu se repère à ce qui est PEINT (alpha > 0), non
        plus à ce qui est sombre. C'est plus juste — une cellule au fond blanc
        appartient au tableau et le recadrage la gardait à tort dehors.
        """
        try:
            import fitz
            import numpy as np
            from PIL import Image
            doc = fitz.open(pdf_path)
            if doc.page_count == 0:
                doc.close()
                return None
            morceaux = []
            for page in doc:
                pix = page.get_pixmap(dpi=dpi, alpha=True)
                im = Image.frombytes("RGBA", (pix.width, pix.height),
                                     pix.samples)
                mask = np.asarray(im.getchannel("A")) > 0
                if not mask.any():
                    continue                      # page vide : rien à montrer
                ys, xs = np.where(mask)
                x0 = max(0, int(xs.min()) - margin)
                y0 = max(0, int(ys.min()) - margin)
                x1 = min(im.width, int(xs.max()) + margin + 1)
                y1 = min(im.height, int(ys.max()) + margin + 1)
                morceaux.append(im.crop((x0, y0, x1, y1)))
            doc.close()
            if not morceaux:
                return None

            if len(morceaux) == 1:
                out = morceaux[0]
            else:
                larg = max(m.width for m in morceaux)
                haut = sum(m.height for m in morceaux)
                out = Image.new("RGBA", (larg, haut), (0, 0, 0, 0))
                y = 0
                for m in morceaux:
                    out.paste(m, (0, y))
                    y += m.height

            if ratio_cible and ratio_cible > 0:
                out = self._completer_au_ratio(out, ratio_cible)

            buf = io.BytesIO()
            out.save(buf, "PNG")
            return buf.getvalue()
        except Exception:
            return None

    @staticmethod
    def _completer_au_ratio(im, ratio_cible: float):
        """Complète l'image de VIDE, centrée, pour atteindre `ratio_cible`.

        Le complément est transparent, pas blanc : c'est de la marge ajoutée
        pour la seule mise à l'échelle, elle n'a rien à cacher de la
        diapositive qui est dessous.
        """
        from PIL import Image
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        actuel = im.width / max(1, im.height)
        if abs(actuel - ratio_cible) < 0.01:
            return im
        if actuel < ratio_cible:                  # trop étroite : élargir
            larg, haut = int(round(im.height * ratio_cible)), im.height
        else:                                     # trop large : rehausser
            larg, haut = im.width, int(round(im.width / ratio_cible))
        fond = Image.new("RGBA", (max(larg, im.width), max(haut, im.height)),
                         (0, 0, 0, 0))
        fond.paste(im, ((fond.width - im.width) // 2,
                        (fond.height - im.height) // 2))
        return fond

    def _ensure_png_content_type(self, temp_path: Path) -> None:
        """Garantit que [Content_Types].xml déclare le type PNG (le média de
        remplacement passe de .emf à .png)."""
        ct_path = temp_path / "[Content_Types].xml"
        if not ct_path.exists():
            return
        CT = "http://schemas.openxmlformats.org/package/2006/content-types"
        try:
            tree = etree.parse(str(ct_path))
            root = tree.getroot()
            for d in root.findall("{%s}Default" % CT):
                if (d.get("Extension") or "").lower() == "png":
                    return
            el = etree.SubElement(root, "{%s}Default" % CT)
            el.set("Extension", "png")
            el.set("ContentType", "image/png")
            tree.write(str(ct_path), xml_declaration=True, encoding="UTF-8")
        except Exception:
            pass
