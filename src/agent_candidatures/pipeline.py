"""Enchaînement : collecte -> scoring -> adaptation CV + brouillon -> (validation) -> envoi."""
from __future__ import annotations

import json
import os

from . import cv, letter as letter_mod, mailer, scoring, sources
from .db import DB
from .llm import LLM

KEYWORDS_OK = lambda job, crit: not any(
    x.lower() in f"{job.title} {job.description}".lower() for x in crit.get("exclude_keywords", []))


def fetch(cfg: dict, db: DB) -> int:
    new = 0
    for job in sources.collect(cfg["sources"]):
        if db.add_if_new(job):
            new += 1
    return new


def score_new(cfg: dict, profile: dict, db: DB, llm: LLM):
    from .models import Job
    for row in db.by_status("new"):
        job = sources.fetch_description(Job(**{k: row[k] for k in (
            "id", "title", "url", "company", "description", "source", "location", "contact_email")}))
        if not KEYWORDS_OK(job, cfg["criteria"]):
            db.update(job.id, status="skipped", reasons="mot-clé exclu")
            continue
        res = scoring.score_job(job, profile, cfg["criteria"], llm)
        status = "scored" if res["score"] >= cfg.get("min_score", 65) else "low_score"
        db.update(job.id, description=job.description, contact_email=job.contact_email,
                  score=res["score"], reasons=json.dumps(res, ensure_ascii=False), status=status)


def prepare(cfg: dict, profile: dict, db: DB, llm: LLM, out_dir: str = "outputs"):
    from .models import Job
    for row in db.by_status("scored"):
        job = Job(**{k: row[k] for k in ("id", "title", "url", "company", "description",
                                          "source", "location", "contact_email")})
        info = json.loads(row["reasons"])
        folder = os.path.join(out_dir, job.id)
        os.makedirs(folder, exist_ok=True)
        tailored = cv.tailor(profile, job, llm)
        cv.render_docx(profile, tailored, os.path.join(folder, "CV.docx"))
        need_letter = bool(info.get("letter_required") or cfg.get("letter", {}).get("always"))
        if need_letter:
            lt = letter_mod.write_letter(profile, job, tailored, info.get("language", "fr"), llm)
            with open(os.path.join(folder, "letter.json"), "w", encoding="utf-8") as f:
                json.dump(lt, f, ensure_ascii=False, indent=2)
            letter_mod.render_letter_pdf(profile, lt, os.path.join(folder, "Lettre.pdf"))
            letter_mod.render_letter_docx(profile, lt, os.path.join(folder, "Lettre.docx"))
        mail = mailer.draft_email(job, profile, tailored, info.get("language", "fr"), llm,
                                  has_letter=need_letter)
        with open(os.path.join(folder, "tailoring.json"), "w", encoding="utf-8") as f:
            json.dump(tailored, f, ensure_ascii=False, indent=2)   # trace de ce qui a été adapté
        with open(os.path.join(folder, "email.json"), "w", encoding="utf-8") as f:
            json.dump(mail, f, ensure_ascii=False, indent=2)
        # Sans adresse de contact : on ne peut pas envoyer, on prépare le dossier pour postuler à la main.
        db.update(job.id, folder=folder, status="drafted" if job.contact_email else "manual")


def send_approved(db: DB, cap: int, dry_run: bool = True) -> list[str]:
    sent = []
    for row in db.by_status("approved"):
        if db.sent_today() + len(sent) >= cap and not dry_run:
            break
        with open(os.path.join(row["folder"], "email.json"), encoding="utf-8") as f:
            mail = json.load(f)
        files = [os.path.join(row["folder"], n) for n in ("CV.docx", "Lettre.pdf")
                 if os.path.exists(os.path.join(row["folder"], n))]
        mailer.send_email(row["contact_email"], mail["subject"], mail["body"], files, dry_run=dry_run)
        if not dry_run:
            db.update(row["id"], status="sent", sent_at=__import__("datetime").datetime.now().isoformat())
        sent.append(row["id"])
    return sent
