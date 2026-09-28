from __future__ import annotations

import argparse
import json
import os

import yaml

from . import cv, pipeline
from .db import DB
from .llm import LLM


def _cfg(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def review(db: DB):
    for row in db.by_status("drafted"):
        mail = json.load(open(os.path.join(row["folder"], "email.json"), encoding="utf-8"))
        print(f"\n=== {row['title']} ({row['company']}) — score {row['score']} ===")
        print(f"URL : {row['url']}\nÀ : {row['contact_email']}\nCV : {row['folder']}/CV.docx")
        t = json.load(open(os.path.join(row["folder"], "tailoring.json"), encoding="utf-8"))
        print(f"Titre du CV : {t['headline']}")
        print("Compétences : " + " | ".join(f"{c['label']}: {', '.join(c['items'][:2])}…" for c in t["skills"][:3]))
        lp = os.path.join(row["folder"], "letter.json")
        if os.path.exists(lp):
            lt = json.load(open(lp, encoding="utf-8"))
            print(f"Lettre : {row['folder']}/Lettre.pdf — objet : {lt['subject']}")
            if lt.get("to_check"):
                print("  ⚠ Termes de la lettre absents de votre profil, à vérifier : " + ", ".join(lt["to_check"]))
        print(f"Objet : {mail['subject']}\n\n{mail['body']}\n")
        ans = input("[a]pprouver / [s]auter / [q]uitter ? ").strip().lower()
        if ans == "a":
            db.update(row["id"], status="approved")
        elif ans == "s":
            db.update(row["id"], status="skipped")
        elif ans == "q":
            break


def main():
    _env()
    p = argparse.ArgumentParser("agent_candidatures")
    sub = p.add_subparsers(dest="cmd", required=True)
    ic = sub.add_parser("import-cv"); ic.add_argument("docx")
    sub.add_parser("run", help="collecte + scoring + préparation")
    sub.add_parser("review", help="relire et approuver les brouillons")
    sd = sub.add_parser("send", help="envoyer les candidatures approuvées")
    sd.add_argument("--yes", action="store_true", help="envoi réel (sinon simulation)")
    sub.add_parser("status")
    a = p.parse_args()

    if a.cmd == "import-cv":
        llm = LLM(_cfg().get("model", "claude-sonnet-4-5"))
        cv.save_profile(cv.import_cv(a.docx, llm))
        print("profile.yaml créé : RELISEZ-LE, c'est la source de vérité de vos CV.")
        return
    cfg, db = _cfg(), DB()
    if a.cmd == "run":
        llm, profile = LLM(cfg.get("model", "claude-sonnet-4-5")), cv.load_profile()
        print(f"{pipeline.fetch(cfg, db)} nouvelles offres")
        pipeline.score_new(cfg, profile, db, llm)
        pipeline.prepare(cfg, profile, db, llm)
        print("Terminé. Lancez : python -m agent_candidatures review")
    elif a.cmd == "review":
        review(db)
    elif a.cmd == "send":
        done = pipeline.send_approved(db, cfg.get("max_sends_per_day", 5), dry_run=not a.yes)
        print(f"{len(done)} candidature(s) {'envoyée(s)' if a.yes else 'simulée(s) (ajoutez --yes)'}")
    elif a.cmd == "status":
        for st in ("new", "scored", "low_score", "drafted", "manual", "approved", "sent", "skipped"):
            print(f"{st:10} {len(db.by_status(st))}")
