"""Import du CV Word -> profile.yaml, adaptation par offre, génération du .docx.

Anti-invention : le modèle ne peut choisir/réordonner/reformuler que des éléments existants
du profil. Catégorie de compétences inconnue = ignorée ; tout chiffre absent de la ligne
d'origine fait rejeter la reformulation (on garde alors le texte original).
"""
from __future__ import annotations

import json
import re

import docx
import yaml
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt

from .llm import LLM
from .models import Job

IMPORT_SYSTEM = """Extrais le contenu de ce CV en JSON, sans rien inventer ni reformuler :
{"name","headline_title" (intitulé de poste visé, sans le type de contrat),"contract" ("STAGE"...),
"specialties":[mots-clés de la ligne de titre + outils/domaines maîtrisés],"availability",
"email","phone","location","links":[],"summary",
"skills":[{"label","items":[chaque compétence ou groupe court]}],
"experience":[{"id":"exp1","role","org","type","period","location","bullets":[{"id":"exp1_b1","text"}]}],
"education":[{"school","degree","period","note"}],"languages","qualities":[]}.
Utilise "" si une info est absente. Le "summary" doit être NEUTRE : retire toute mention
d'une entreprise cible précise."""

TAILOR_SYSTEM = """Tu adaptes un CV à UNE offre précise : tout ce qui est modulable doit refléter l'offre.
Tu renvoies :
{"target_title": intitulé de poste repris de l'offre (sans F/H ni « stage »),
 "specialties": 3 éléments choisis dans profil.specialties, les plus pertinents pour l'offre,
 "summary": résumé réécrit pour cette offre (5 lignes max),
 "skills": [{"label": nom de catégorie (existant, ou reformulé avec le vocabulaire de l'offre),
             "items": éléments de CETTE catégorie, les plus pertinents en premier}],
 "experience": [{"id": id existant, "bullets": [{"id": id existant, "text": texte}]}],
 "qualities": qualités choisies dans profil.qualities, les plus pertinentes en premier}
Catégories de compétences : les plus pertinentes en premier.
RÈGLES STRICTES :
- N'invente AUCUNE expérience, compétence, outil, diplôme, chiffre ou domaine absent du profil.
- Les items de compétences, spécialités et qualités doivent être copiés tels quels du profil.
- Tu peux reformuler une puce avec le vocabulaire de l'offre SANS changer les faits ; choisis et
  ordonne les puces les plus pertinentes.
- Ne cite jamais d'autre entreprise que celle de l'offre."""


def docx_text(path: str) -> str:
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells if c.text.strip()]
    return "\n".join(parts)


def import_cv(docx_path: str, llm: LLM) -> dict:
    return llm.json(IMPORT_SYSTEM, docx_text(docx_path), max_tokens=4000)


def save_profile(profile: dict, path: str = "profile.yaml"):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(profile, f, allow_unicode=True, sort_keys=False, width=100)


def load_profile(path: str = "profile.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", s))


def tailor(profile: dict, job: Job, llm: LLM) -> dict:
    user = json.dumps({"profil": profile, "offre": {"titre": job.title, "entreprise": job.company,
                       "description": job.description[:6000]}}, ensure_ascii=False)
    return validate_tailoring(profile, llm.json(TAILOR_SYSTEM, user, max_tokens=3500), job)


_STOP = {"f", "h", "stage", "stagiaire", "alternance", "intern", "internship", "de", "en", "et",
         "&", "-", "–", "the", "of", "and", "la", "le", "les", "des", "du", "d", "l", "/", "hf", "fh"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w]+", text.lower()) if w not in _STOP}


def _pick(wanted, pool, limit=None):
    """Garde uniquement les éléments existants du pool (insensible à la casse), sans doublon."""
    known = {x.lower(): x for x in pool}
    out = []
    for w in wanted or []:
        x = known.get(str(w).strip().lower())
        if x and x not in out:
            out.append(x)
    return out[:limit] if limit else out


def build_headline(profile: dict, title: str, specialties: list[str]) -> str:
    contract = profile.get("contract", "STAGE")
    return f"{title.upper()} ({contract}) — {' · '.join(s.upper() for s in specialties)}"


def validate_tailoring(profile: dict, raw: dict, job: Job | None = None) -> dict:
    """Ramène la sortie du modèle à ce que le profil autorise."""
    # --- titre : mots de l'intitulé proposé doivent venir de l'intitulé de l'offre
    title = str(raw.get("target_title", "")).strip()
    if not title or (job and not _words(title) <= _words(job.title)) or len(title) > 70:
        title = profile.get("headline_title", "")
    pool = list(profile.get("specialties", []))
    specs = _pick(raw.get("specialties"), pool, 3)
    for d in pool:                       # complète avec les spécialités par défaut si besoin
        if len(specs) >= 3:
            break
        if d not in specs:
            specs.append(d)
    headline = build_headline(profile, title, specs)

    # --- compétences : catégories ordonnées, items de la bonne catégorie uniquement
    cats = {c["label"].lower(): c for c in profile.get("skills", [])}
    skills, used = [], set()
    for entry in raw.get("skills", []):
        if not isinstance(entry, dict):
            continue
        src = cats.get(str(entry.get("label", "")).lower())
        if src is None:                  # libellé reformulé : retrouver la catégorie par ses items
            for c in profile.get("skills", []):
                if c["label"] not in used and _pick(entry.get("items"), c["items"]):
                    src = c
                    break
        if src is None or src["label"] in used:
            continue
        used.add(src["label"])
        label = str(entry.get("label") or src["label"]).strip()
        if len(label) > 60 or not _words(label):
            label = src["label"]
        items = _pick(entry.get("items"), src["items"])
        items += [i for i in src["items"] if i not in items]      # aucun item ne disparaît
        skills.append({"label": label, "items": items})
    skills += [c for c in profile.get("skills", []) if c["label"] not in used]

    # --- expériences : puces de l'existant, chiffres protégés
    exps = {e["id"]: e for e in profile.get("experience", [])}
    orig = {b["id"]: b["text"] for e in exps.values() for b in e["bullets"]}
    out_exp, seen = [], set()
    for e in raw.get("experience", []):
        if e.get("id") not in exps or e["id"] in seen:
            continue
        seen.add(e["id"])
        valid_ids = {x["id"] for x in exps[e["id"]]["bullets"]}
        bullets = []
        for b in e.get("bullets", []):
            if b.get("id") not in valid_ids:
                continue
            text = b.get("text") or orig[b["id"]]
            if not _numbers(text) <= _numbers(orig[b["id"]]):
                text = orig[b["id"]]  # chiffre inventé -> on garde l'original
            bullets.append({"id": b["id"], "text": text})
        out_exp.append({"id": e["id"], "bullets": bullets or exps[e["id"]]["bullets"]})
    for eid, e in exps.items():  # expériences omises par le modèle : conservées telles quelles
        if eid not in seen:
            out_exp.append({"id": eid, "bullets": e["bullets"]})
    order = list(exps)           # ordre chronologique du profil conservé
    out_exp.sort(key=lambda e: order.index(e["id"]))

    summary = raw.get("summary") or profile.get("summary", "")
    if not _numbers(summary) <= _numbers(json.dumps(profile, ensure_ascii=False)):
        summary = profile.get("summary", "")
    quals = _pick(raw.get("qualities"), profile.get("qualities", []), 6) or list(profile.get("qualities", []))
    return {"headline": headline, "summary": summary, "skills": skills,
            "experience": out_exp, "qualities": quals}


# ---------- Rendu Word, calqué sur la mise en page du CV d'origine ----------

def _run(p, text, size, bold=False, italic=False):
    r = p.add_run(text)
    r.font.name, r.font.size, r.bold, r.italic = "Calibri", Pt(size), bold, italic
    return r


def _para(d, align=None, before=0, after=0):
    p = d.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(before), Pt(after)
    return p


def _heading(d, text):
    p = _para(d, before=7, after=3)
    _run(p, text.upper(), 10.5, bold=True)
    pr = p._p.get_or_add_pPr()
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("val", "single"), ("sz", "4"), ("space", "1"), ("color", "999999")):
        bottom.set(qn(f"w:{k}"), v)
    border.append(bottom)
    pr.append(border)


def render_docx(profile: dict, tailored: dict, path: str):
    d = docx.Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = Emu(7560310), Emu(10692130)  # A4
    sec.left_margin = sec.right_margin = Emu(444500)
    sec.top_margin = sec.bottom_margin = Emu(393700)
    width = sec.page_width - sec.left_margin - sec.right_margin
    d.styles["Normal"].font.name = "Calibri"
    C = WD_ALIGN_PARAGRAPH.CENTER

    _run(_para(d, C), profile["name"], 20, bold=True)
    _run(_para(d, C), tailored.get("headline") or profile.get("headline", ""), 10, bold=True)
    if profile.get("availability"):
        _run(_para(d, C), profile["availability"], 9, italic=True)
    contact = "   |   ".join(x for x in [profile.get("phone"), profile.get("email"),
                                         *profile.get("links", [])] if x)
    _run(_para(d, C, after=2), contact, 9)

    _heading(d, "Profil")
    _run(_para(d), tailored["summary"], 9.5)

    _heading(d, "Expériences")
    by_id = {e["id"]: e for e in profile.get("experience", [])}
    for e in tailored["experience"]:
        s = by_id[e["id"]]
        p = _para(d, before=3)
        p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
        _run(p, s["role"], 10, bold=True)
        tail = "  —  ".join(x for x in [s.get("org"), s.get("type")] if x)
        _run(p, f"  —  {tail}" if tail else "", 9.5)
        _run(p, f"\t{s.get('period', '')}", 9, italic=True)
        if s.get("location"):
            _run(_para(d), s["location"], 9, italic=True)
        for b in e["bullets"]:
            bp = d.add_paragraph(style="List Bullet")
            bp.paragraph_format.space_after = Pt(1)
            _run(bp, b["text"], 9.5)

    _heading(d, "Compétences techniques")
    for c in tailored["skills"]:
        p = _para(d, after=1)
        sep = " ; " if any(len(i) > 45 for i in c["items"]) else ", "
        _run(p, f"{c['label']} : ", 9.5, bold=True)
        _run(p, sep.join(c["items"]) + ".", 9.5)

    _heading(d, "Formations")
    for ed in profile.get("education", []):
        p = _para(d)
        _run(p, ed["degree"], 9.5, bold=True)
        _run(p, f" — {ed['school']}  ", 9.5)
        _run(p, f"({ed.get('period', '')})", 9, italic=True)
        if ed.get("note"):
            _run(_para(d), ed["note"], 9)

    _heading(d, "Langues & qualités")
    p = _para(d, after=1)
    _run(p, "Langues : ", 9.5, bold=True); _run(p, profile.get("languages", ""), 9.5)
    p = _para(d)
    _run(p, "Qualités : ", 9.5, bold=True)
    _run(p, " · ".join(tailored.get("qualities") or profile.get("qualities", [])), 9.5)
    d.save(path)
