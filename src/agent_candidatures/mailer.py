"""Rédaction du mail de candidature et envoi SMTP (uniquement après validation)."""
from __future__ import annotations

import json
import mimetypes
import os
import smtplib
from email.message import EmailMessage

from .llm import LLM
from .models import Job

MAIL_SYSTEM = """Rédige un e-mail de candidature court (120-160 mots), sincère, sans formules creuses,
dans la langue de l'offre. Appuie-toi UNIQUEMENT sur les faits du profil fourni. Ne promets rien
d'inventé. Mentionne les pièces jointes fournies (CV, et lettre de motivation si présente). Termine par le nom du candidat.
Renvoie : {"subject": str, "body": str}"""


def draft_email(job: Job, profile: dict, tailored: dict, language: str, llm: LLM,
                has_letter: bool = False) -> dict:
    user = json.dumps({"langue": language, "pieces_jointes": ["CV"] + (["lettre de motivation"] if has_letter else []), "offre": {"titre": job.title, "entreprise": job.company,
                       "description": job.description[:4000]},
                       "candidat": {"name": profile["name"], "summary": tailored["summary"],
                                    "skills": [c["label"] for c in tailored["skills"]]}}, ensure_ascii=False)
    return llm.json(MAIL_SYSTEM, user, max_tokens=700)


def send_email(to: str, subject: str, body: str, attachments, dry_run: bool = True):
    if isinstance(attachments, str):
        attachments = [attachments]
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["MAIL_USER"], to, subject
    msg.set_content(body)
    for att in attachments:
        ctype = mimetypes.guess_type(att)[0] or "application/octet-stream"
        main, sub = ctype.split("/")
        with open(att, "rb") as f:
            msg.add_attachment(f.read(), maintype=main, subtype=sub, filename=os.path.basename(att))
    if dry_run:
        return msg
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                          int(os.environ.get("SMTP_PORT", 465))) as s:
        s.login(os.environ["MAIL_USER"], os.environ["MAIL_APP_PASSWORD"])
        s.send_message(msg)
    return msg
