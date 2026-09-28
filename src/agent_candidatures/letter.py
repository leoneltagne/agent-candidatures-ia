"""Lettre de motivation : rédaction par offre + rendu fidèle au modèle de l'utilisateur.

Ce qui change d'une offre à l'autre : entreprise, service, date, objet, contenu.
Ce qui est fixe : en-tête (nom, téléphone, e-mail, ville), mise en page, signature (image du nom).
"""
from __future__ import annotations

import base64
import json
import re
from datetime import date
from html import escape
from pathlib import Path

from .cv import _numbers, _words
from .llm import LLM
from .models import Job

ASSETS = Path(__file__).resolve().parents[2] / "assets"
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]

LETTER_SYSTEM = """Tu rédiges la lettre de motivation d'un candidat pour UNE offre, dans la langue de l'offre.
Ton : direct, sobre, sincère, sans formules creuses. Style du modèle fourni (exemple) : 4 paragraphes courts.
  1. Ce qui, dans l'offre / le projet / la mission, t'intéresse précisément, et le lien avec ton profil.
  2. Une réalisation concrète du profil qui répond aux besoins de l'offre (faits réels uniquement).
  3. Curiosité / montée en compétences sur les sujets de l'offre, dans le cadre de la formation.
  4. Qualités utiles pour le poste + envie de contribuer à l'entreprise.
RÈGLES STRICTES :
- N'invente AUCUNE expérience, outil, résultat, chiffre ou diplôme absent du profil. Pour un sujet de
  l'offre que le candidat ne maîtrise pas, tu peux dire qu'il est CURIEUX ou se FORME, jamais qu'il l'a pratiqué.
- Ne cite que l'entreprise de l'offre. 130 à 200 mots au total. Pas de liste, pas de puces.
- Ne rédige ni salutation ni formule finale (ajoutées automatiquement).
Renvoie : {"recipient_org": nom de l'entreprise, "recipient_dept": service/équipe si l'offre le précise sinon "",
"subject": "Candidature — <intitulé de l'offre>", "paragraphs": [4 chaînes],
"evidence": [ids de puces ou éléments du profil utilisés]}"""

# Modèle de ton fourni par l'utilisateur (offre VINCI Airports), utilisé en exemple few-shot.
STYLE_EXAMPLE = (
    "Votre offre pour le projet « Smart Data Hub » m'intéresse particulièrement : donner à l'ensemble "
    "des aéroports du groupe la capacité de mieux piloter leur performance grâce à des tableaux de bord "
    "clairs, c'est exactement le type de mission où je veux mettre mes compétences en data visualisation."
)


def french_date(d: date | None = None, city: str = "Paris") -> str:
    d = d or date.today()
    return f"{d.day} {MONTHS_FR[d.month - 1].capitalize()} {d.year}, {city}"


def write_letter(profile: dict, job: Job, tailored: dict | None, language: str, llm: LLM) -> dict:
    user = json.dumps({
        "langue": language,
        "exemple_de_ton": STYLE_EXAMPLE,
        "offre": {"titre": job.title, "entreprise": job.company, "lieu": job.location,
                  "description": job.description[:6000]},
        "profil": {k: profile.get(k) for k in ("summary", "skills", "experience", "education",
                                                "qualities", "languages")},
        "cv_adapte": {"headline": (tailored or {}).get("headline")},
    }, ensure_ascii=False)
    return validate_letter(profile, job, llm.json(LETTER_SYSTEM, user, max_tokens=1500))


def unsupported_terms(text: str, profile: dict, job: Job) -> list[str]:
    """Termes techniques de la lettre (Sigles, NomsPropres) absents du profil : à vérifier avant envoi."""
    known = json.dumps(profile, ensure_ascii=False).lower() + " " + (job.company or "").lower()
    found = set()
    for sent in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-zÀ-ÿ0-9][\w'’./+#-]*", sent)
        for i, w in enumerate(words):
            w = w.strip(".,;:!?")
            technical = (w.isupper() and len(w) >= 2) or (i > 0 and w[:1].isupper() and len(w) > 2)
            if technical and w.lower() not in known:
                found.add(w)
    return sorted(found)


def validate_letter(profile: dict, job: Job, raw: dict) -> dict:
    paras = [re.sub(r"\s+", " ", str(p)).strip() for p in raw.get("paragraphs", []) if str(p).strip()]
    if not 3 <= len(paras) <= 5:
        raise ValueError(f"La lettre doit avoir 3 à 5 paragraphes (reçu {len(paras)})")
    if sum(len(p.split()) for p in paras) > 260:
        raise ValueError("Lettre trop longue (> 260 mots) : elle ne tiendrait pas sur une page")
    allowed = _numbers(json.dumps(profile, ensure_ascii=False))
    for p in paras:
        if not _numbers(p) <= allowed:
            raise ValueError(f"Chiffre absent du profil dans : {p[:60]}…")
    subject = str(raw.get("subject", "")).strip()
    if not subject.lower().startswith("candidature") or not _words(subject) <= _words(
            "candidature " + job.title):
        subject = f"Candidature — {job.title}"
    org = job.company or str(raw.get("recipient_org", "")).strip()
    letter = {"recipient_org": org, "recipient_dept": str(raw.get("recipient_dept", "")).strip(),
              "subject": subject, "paragraphs": paras, "evidence": raw.get("evidence", []),
              "date": french_date(city=profile.get("letter", {}).get("city", "Paris"))}
    letter["to_check"] = unsupported_terms(" ".join(paras), profile, job)
    return letter


# ---------------------------------------------------------------- rendu PDF (fidèle au modèle)

def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def letter_html(profile: dict, letter: dict, body_pt: float = 11.6) -> str:
    L = profile.get("letter", {})
    fonts = ASSETS / "fonts"
    face = "".join(
        f"@font-face{{font-family:'{fam}';font-weight:{w};font-style:{st};"
        f"src:url(data:font/woff2;base64,{_b64(fonts / f)}) format('woff2');}}"
        for fam, w, st, f in [("Montserrat", 700, "normal", "montserrat-latin-700-normal.woff2"),
                              ("Open Sans", 400, "normal", "open-sans-latin-400-normal.woff2"),
                              ("Open Sans", 700, "normal", "open-sans-latin-700-normal.woff2"),
                              ("Open Sans", 700, "italic", "open-sans-latin-700-italic.woff2")])
    sig_path = ASSETS / L.get("signature_image", "signature.png")
    if sig_path.exists():
        sig_html = f'<img class="sig" src="data:image/png;base64,{_b64(sig_path)}">'
    else:  # repli si l'image de signature n'est pas fournie : le nom en écriture cursive
        sig_html = f'<div class="sig" style="font:italic 26pt cursive;white-space:nowrap;margin-left:340pt">{escape(L.get("name", profile["name"]))}</div>'
    dept = f"<br>{escape(letter['recipient_dept'])}" if letter.get("recipient_dept") else ""
    body = "".join(f"<p>{escape(p)}</p>" for p in letter["paragraphs"])
    return f"""<!doctype html><html lang="fr"><meta charset="utf-8"><style>{face}
@page{{size:595.5pt 842.25pt;margin:0}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:595.5pt;height:842.25pt;overflow:hidden;font-family:'Open Sans',sans-serif;color:#111}}
.abs{{position:absolute}}
.name{{left:39pt;top:61pt;font:700 31.2pt/1 'Montserrat',sans-serif;letter-spacing:.3pt}}
.contact{{left:41pt;top:106pt;font:700 11.3pt/15pt 'Open Sans';letter-spacing:.2pt}}
.a{{top:144pt;font:700 9pt/1 'Open Sans'}}
.to{{left:381pt;width:186pt;top:161pt;font:700 13.2pt/18pt 'Open Sans';text-align:center;text-wrap:balance}}
.date{{right:59pt;top:244pt;font:italic 700 11.4pt/1.3 'Open Sans';text-align:right}}
.subject{{left:41pt;top:289pt;font:700 13pt/1.3 'Open Sans';letter-spacing:.35pt;width:530pt}}
.body{{left:32pt;top:336pt;width:518pt;font:400 {body_pt}pt/16.4pt 'Open Sans';letter-spacing:.8pt;text-align:justify}}
.body p{{margin-bottom:16.4pt}}
.close{{margin-top:0}}
.sig{{display:block;margin:8pt 0 0 374pt;width:132pt}}
</style><body>
<div class="abs name">{escape(L.get('name', profile['name']))}</div>
<div class="abs contact">{escape(L.get('phone', profile.get('phone', '')))}<br>{escape(L.get('email', profile.get('email', '')))}<br>{escape(L.get('location', profile.get('location', '')))}</div>
<div class="abs a" style="left:484pt">À</div>
<div class="abs to">{escape(letter['recipient_org'])}{dept}</div>
<div class="abs date">{escape(letter['date'])}</div>
<div class="abs subject">Objet : {escape(letter['subject'])}</div>
<div class="abs body"><p>Madame, Monsieur,</p>{body}
<div class="close">Je reste à votre disposition pour un entretien.<br>Cordialement,</div>
{sig_html}</div>
</body></html>"""


def render_letter_pdf(profile: dict, letter: dict, path: str) -> int:
    """Écrit le PDF ; réduit légèrement la police si la lettre déborde d'une page. Renvoie le nb de pages."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for size in (11.6, 11.2, 10.8, 10.4, 10.0):
            page.set_content(letter_html(profile, letter, size))
            page.evaluate("document.fonts.ready")
            page.pdf(path=path, prefer_css_page_size=True, print_background=True)
            pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", Path(path).read_bytes()))
            if pages == 1:
                break
        browser.close()
    return pages


def render_letter_docx(profile: dict, letter: dict, path: str):
    """Version modifiable (mêmes textes ; polices Montserrat / Open Sans si installées)."""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH as A
    from docx.shared import Cm, Pt
    L = profile.get("letter", {})
    d = docx.Document()
    s = d.sections[0]
    s.left_margin = s.right_margin = Cm(1.6)
    s.top_margin = s.bottom_margin = Cm(1.6)

    def para(text="", size=11, bold=False, italic=False, font="Open Sans", align=None, after=0):
        p = d.add_paragraph()
        p.alignment = align
        p.paragraph_format.space_after = Pt(after)
        r = p.add_run(text)
        r.font.name, r.font.size, r.bold, r.italic = font, Pt(size), bold, italic
        return p

    para(L.get("name", profile["name"]), 26, True, font="Montserrat", after=6)
    for line in (L.get("phone", ""), L.get("email", ""), L.get("location", "")):
        para(line, 10, True)
    para("À", 9, True, align=A.RIGHT)
    para(letter["recipient_org"], 12, True, align=A.RIGHT)
    if letter.get("recipient_dept"):
        para(letter["recipient_dept"], 12, True, align=A.RIGHT)
    para(letter["date"], 9.5, True, True, align=A.RIGHT, after=14)
    para(f"Objet : {letter['subject']}", 11.5, True, after=14)
    para("Madame, Monsieur,", after=11)
    for t in letter["paragraphs"]:
        para(t, align=A.JUSTIFY, after=11)
    para("Je reste à votre disposition pour un entretien.")
    para("Cordialement,", after=6)
    sig = ASSETS / L.get("signature_image", "signature.png")
    if sig.exists():
        d.add_picture(str(sig), width=Cm(4.6))
        d.paragraphs[-1].alignment = A.RIGHT
    else:
        para(L.get("name", profile["name"]), 20, italic=True, align=A.RIGHT)
    d.save(path)
