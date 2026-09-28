"""Évalue l'adéquation offre / profil avec Claude."""
from __future__ import annotations

import json

from .llm import LLM
from .models import Job

SYSTEM = """Tu es un conseiller en recrutement. Évalue l'adéquation entre une offre et un candidat.
Sois sévère et factuel : ne surestime pas. Renvoie :
{"score": entier 0-100, "reasons": [3 raisons courtes], "red_flags": [éventuels problèmes :
exigences manquantes, offre qui n'est pas un stage/alternance, arnaque probable, etc.],
"language": "fr" ou "en" (langue de l'offre),
"letter_required": true UNIQUEMENT si l'offre demande explicitement une lettre de motivation / cover letter}"""


def score_job(job: Job, profile: dict, criteria: dict, llm: LLM) -> dict:
    user = json.dumps({
        "offre": {"titre": job.title, "entreprise": job.company, "lieu": job.location,
                  "description": job.description[:6000]},
        "profil": {k: profile[k] for k in ("headline_title", "specialties", "summary", "skills", "experience", "education")
                   if k in profile},
        "criteres": criteria,
    }, ensure_ascii=False)
    res = llm.json(SYSTEM, user, max_tokens=600)
    res["score"] = max(0, min(100, int(res.get("score", 0))))
    res["letter_required"] = bool(res.get("letter_required"))
    if res.get("red_flags"):
        res["score"] = max(0, res["score"] - 10 * len(res["red_flags"]))
    return res
