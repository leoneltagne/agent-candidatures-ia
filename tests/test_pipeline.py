import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("MAIL_USER", "moi@example.com")

import docx, yaml
from agent_candidatures import cv, pipeline, mailer
from agent_candidatures.db import DB
from agent_candidatures.models import Job

PROFILE = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "profile.example.yaml")))
CFG = {"criteria": {"exclude_keywords": ["senior"]}, "min_score": 65}


class FakeLLM:
    """Répond selon le prompt système ; simule aussi un modèle qui triche."""
    def json(self, system, user, max_tokens=0):
        if "conseiller en recrutement" in system:
            return {"score": 82, "reasons": ["a", "b", "c"], "red_flags": [], "language": "fr"}
        if "adaptes un CV" in system:
            return {"summary": "Profil Python orienté données, 99 ans d'expérience.",
                    "target_title": "Data Analyst", "specialties": ["Kubernetes", "SQL", "Python"],
                    "skills": [{"label": "Outils", "items": ["Excel", "Git"]},
                               {"label": "Langages", "items": ["SQL", "Kubernetes", "Python"]},
                               {"label": "Catégorie inventée", "items": ["Rust"]}],
                    "qualities": ["Autonomie", "Génie"],
                    "experience": [{"id": "exp1", "bullets": [
                        {"id": "exp1_b1", "text": "Script Python de nettoyage, temps réduit de 90 %."},  # chiffre inventé
                        {"id": "bidon", "text": "x"}]}]}
        return {"subject": "Candidature stage data", "body": "Bonjour,\nCV ci-joint.\nPrénom Nom"}


def test_tailoring_blocks_invention():
    job = Job(title="Stage Data Analyst F/H", url="u")
    raw = FakeLLM().json("adaptes un CV", "")
    t = cv.validate_tailoring(PROFILE, raw, job)
    labels = [c["label"] for c in t["skills"]]
    assert labels[:2] == ["Outils", "Langages"]                    # ordre du modèle respecté
    assert len(labels) == len(PROFILE["skills"])                   # aucune catégorie ne disparaît
    assert t["summary"] == PROFILE["summary"]                      # "99" inventé -> résumé d'origine
    texts = [b["text"] for b in t["experience"][0]["bullets"]]
    assert "90" not in " ".join(texts)                             # chiffre inventé rejeté
    assert "bidon" not in [b["id"] for b in t["experience"][0]["bullets"]]
    assert "KUBERNETES" not in t["headline"]                       # spécialité inventée rejetée
    assert "PIRATE" not in t["headline"] and t["headline"].startswith("DATA ANALYST")
    langages = next(c for c in t["skills"] if c["label"] == "Langages")
    assert "Kubernetes" not in langages["items"] and set(langages["items"]) == {"Python", "SQL"}


def test_render_docx(tmp_path):
    t = cv.validate_tailoring(PROFILE, {}, None)
    out = tmp_path / "CV.docx"
    cv.render_docx(PROFILE, t, str(out))
    text = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert "Prénom NOM" in text and "Python" in text and "Licence Informatique" in text


def test_full_flow(tmp_path, monkeypatch):
    db = DB(str(tmp_path / "t.db"))
    good = Job(title="Stage data", url="https://x.org/1", company="ONG",
               description="Stage. Contact : rh@ong.org")
    nocontact = Job(title="Stage IA", url="https://x.org/2", description="Postuler via le site")
    senior = Job(title="Data senior", url="https://x.org/3", description="senior")
    for j in (good, nocontact, senior):
        assert db.add_if_new(j)
    assert not db.add_if_new(good)                                 # déduplication
    llm = FakeLLM()
    pipeline.score_new(CFG, PROFILE, db, llm)
    assert db.get(senior.id)["status"] == "skipped"
    pipeline.prepare(CFG, PROFILE, db, llm, out_dir=str(tmp_path))
    assert db.get(good.id)["status"] == "drafted"
    assert db.get(nocontact.id)["status"] == "manual"              # pas d'adresse -> jamais envoyé
    assert os.path.exists(tmp_path / good.id / "CV.docx")
    # Rien n'est envoyé sans approbation
    assert pipeline.send_approved(db, cap=5) == []
    db.update(good.id, status="approved")
    assert pipeline.send_approved(db, cap=5, dry_run=True) == [good.id]
    assert db.get(good.id)["status"] == "approved"                 # dry-run ne change rien


def test_email_message_has_attachment(tmp_path):
    f = tmp_path / "CV.docx"; f.write_bytes(b"x")
    msg = mailer.send_email("rh@ong.org", "Objet", "Corps", str(f), dry_run=True)
    assert msg["To"] == "rh@ong.org"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["CV.docx"]


def test_real_profile_is_neutral_and_renders(tmp_path):
    """Le profil réel ne doit citer aucune entreprise cible d'une autre candidature."""
    real = os.path.join(os.path.dirname(__file__), "..", "profile.yaml")
    if not os.path.exists(real):
        return
    prof = yaml.safe_load(open(real))
    dumped = yaml.safe_dump(prof, allow_unicode=True)
    for term in prof.get("forbidden_terms", []):                  # ex. noms d'autres entreprises cibles
        assert term not in dumped
    cv.render_docx(prof, cv.validate_tailoring(prof, {}), str(tmp_path / "CV.docx"))


def _tailor_with(profile, job, raw):
    return cv.validate_tailoring(profile, raw, job)


def test_every_offer_gets_its_own_title_skills_summary(tmp_path):
    """Deux offres différentes -> deux CV différents (titre, spécialités, compétences, résumé)."""
    prof = PROFILE
    bi = _tailor_with(prof, Job(title="Stage Data & Digital Analyst F/H", url="a"), {
        "target_title": "Data & Digital Analyst", "specialties": ["SQL", "Reporting & KPI", "Python"],
        "summary": "Orienté reporting et indicateurs.",
        "skills": [{"label": "Outils", "items": ["Excel", "Git"]}], "qualities": ["Rigueur"]})
    ml = _tailor_with(prof, Job(title="Stage Machine Learning Engineer", url="b"), {
        "target_title": "Machine Learning Engineer", "specialties": ["Machine Learning", "Python", "SQL"],
        "summary": "Orienté modèles.", "skills": [{"label": "Langages", "items": ["Python", "SQL"]}],
        "qualities": ["Autonomie"]})
    assert bi["headline"] != ml["headline"]
    assert bi["headline"].startswith("DATA & DIGITAL ANALYST (STAGE)")
    assert ml["headline"].startswith("MACHINE LEARNING ENGINEER (STAGE)")
    assert bi["skills"][0]["label"] == "Outils" and ml["skills"][0]["label"] == "Langages"
    assert bi["summary"] != ml["summary"] and bi["qualities"] != ml["qualities"]


def test_title_must_come_from_the_offer():
    t = _tailor_with(PROFILE, Job(title="Stage Data Analyst", url="c"),
                     {"target_title": "Directeur général IA"})
    assert t["headline"].startswith(PROFILE["headline_title"].upper())   # titre inventé -> repli


def test_relabeled_category_keeps_real_items():
    t = _tailor_with(PROFILE, Job(title="Stage Data Analyst", url="d"), {
        "skills": [{"label": "Programmation et requêtes", "items": ["SQL", "Python"]}]})
    assert t["skills"][0]["label"] == "Programmation et requêtes"
    assert set(t["skills"][0]["items"]) == {"Python", "SQL"}


# ------------------------------------------------------------------ lettre de motivation
from datetime import date
from agent_candidatures import letter as lt_mod

GOOD_LETTER = {
    "recipient_org": "N'importe quoi", "recipient_dept": "Équipe Excellence Opérationnelle",
    "subject": "Candidature — Stage Data & Digital Analyst F/H",
    "paragraphs": ["Votre offre m'intéresse car elle associe données industrielles et digitalisation.",
                   "J'ai conçu et déployé de bout en bout une application en production depuis 2 mois.",
                   "Je me forme en continu aux outils Low-Code.",
                   "Rigoureux et autonome, je serais heureux de rejoindre Framatome."],
    "evidence": ["exp1_b1"]}


def test_french_date_format():
    assert lt_mod.french_date(date(2026, 9, 29), "Paris") == "29 Septembre 2026, Paris"


def test_letter_fields_and_guards():
    job = Job(title="Stage Data & Digital Analyst F/H", url="u", company="Framatome")
    lt = lt_mod.validate_letter(PROFILE, job, dict(GOOD_LETTER))
    assert lt["recipient_org"] == "Framatome"                       # l'entreprise vient de l'offre
    assert lt["subject"] == "Candidature — Stage Data & Digital Analyst F/H"
    assert "Framatome" not in lt["to_check"] and "Low-Code" in lt["to_check"]
    # chiffre inventé -> refus
    bad = dict(GOOD_LETTER, paragraphs=GOOD_LETTER["paragraphs"][:3] + ["J'ai 7 ans d'expérience."])
    try:
        lt_mod.validate_letter(PROFILE, job, bad); assert False
    except ValueError as e:
        assert "Chiffre" in str(e)
    # objet inventé -> repli sur l'intitulé de l'offre
    odd = dict(GOOD_LETTER, subject="Candidature — Directeur général")
    assert lt_mod.validate_letter(PROFILE, job, odd)["subject"] == "Candidature — Stage Data & Digital Analyst F/H"
    # trop long -> refus (ne tiendrait pas sur une page)
    long = dict(GOOD_LETTER, paragraphs=["mot " * 100] * 4)
    try:
        lt_mod.validate_letter(PROFILE, job, long); assert False
    except ValueError:
        pass


def test_unsupported_terms_flags_tools_not_in_profile():
    job = Job(title="Stage", url="u", company="VINCI Airports")
    flagged = lt_mod.unsupported_terms("Curieux des environnements Cloud comme BigQuery et de Power BI.",
                                       PROFILE, job)
    assert "BigQuery" in flagged


def test_letter_pdf_is_single_page_with_expected_content(tmp_path):
    import shutil, subprocess
    import pytest
    if not shutil.which("pdftotext"):
        pytest.skip("poppler-utils (pdftotext) requis")
    job = Job(title="Stage Data & Digital Analyst F/H", url="u", company="Framatome")
    lt = lt_mod.validate_letter(PROFILE, job, dict(GOOD_LETTER))
    out = tmp_path / "Lettre.pdf"
    assert lt_mod.render_letter_pdf(PROFILE, lt, str(out)) == 1
    text = subprocess.run(["pdftotext", "-layout", str(out), "-"], capture_output=True, text=True).stdout
    for needed in ("Framatome", "Objet : Candidature", "Madame, Monsieur,", "Cordialement,", "vous@example.com"):
        assert needed in text
    lt_mod.render_letter_docx(PROFILE, lt, str(tmp_path / "Lettre.docx"))


def test_pipeline_writes_letter_only_when_required(tmp_path):
    class LetterLLM(FakeLLM):
        def json(self, system, user, max_tokens=0):
            if "lettre de motivation d'un candidat" in system:
                return dict(GOOD_LETTER)
            res = super().json(system, user, max_tokens)
            if "conseiller en recrutement" in system:
                res["letter_required"] = "avec lettre" in user
            return res
    db = DB(str(tmp_path / "l.db"))
    with_l = Job(title="Stage Data avec lettre", url="https://x.org/10", company="Framatome",
                 description="Stage avec lettre de motivation. rh@fram.fr")
    without = Job(title="Stage Data sans", url="https://x.org/11", company="Autre", description="Écrire à rh@autre.fr")
    for j in (with_l, without):
        db.add_if_new(j)
    llm = LetterLLM()
    pipeline.score_new(CFG, PROFILE, db, llm)
    pipeline.prepare(CFG, PROFILE, db, llm, out_dir=str(tmp_path))
    assert os.path.exists(tmp_path / with_l.id / "Lettre.pdf")
    assert not os.path.exists(tmp_path / without.id / "Lettre.pdf")
    db.update(with_l.id, status="approved")
    sent = pipeline.send_approved(db, cap=5, dry_run=True)
    assert sent == [with_l.id]
