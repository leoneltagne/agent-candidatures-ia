"""Collecte des offres : RSS, pages carrières, alertes e-mail.

Le scraping direct de LinkedIn est volontairement absent : il viole leurs conditions
d'utilisation et expose votre compte. On passe par leurs alertes e-mail à la place.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import date, timedelta
from email.header import decode_header

import feedparser
import requests
from bs4 import BeautifulSoup

from .models import Job

HEADERS = {"User-Agent": "agent-candidatures/0.1 (usage personnel)"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
JOB_WORDS = re.compile(r"stage|intern|alternan|stagiaire|apprenti|junior", re.I)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def find_contact_email(text: str) -> str:
    bad = ("noreply", "no-reply", "example.")
    for e in EMAIL_RE.findall(text):
        if not any(b in e.lower() for b in bad):
            return e
    return ""


def from_rss(url: str) -> list[Job]:
    feed = feedparser.parse(url)
    jobs = []
    for e in feed.entries:
        desc = html_to_text(e.get("summary", ""))
        jobs.append(Job(title=e.get("title", ""), url=e.get("link", ""), description=desc,
                        source=f"rss:{url}", contact_email=find_contact_email(desc)))
    return jobs


def from_career_page(name: str, url: str) -> list[Job]:
    html = requests.get(url, headers=HEADERS, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        if label and JOB_WORDS.search(label):
            link = requests.compat.urljoin(url, a["href"])
            jobs.append(Job(title=label, url=link, company=name, source=f"page:{name}"))
    return jobs


def fetch_description(job: Job) -> Job:
    """Complète la description depuis la page de l'offre (si vide)."""
    if job.description:
        job.contact_email = job.contact_email or find_contact_email(job.description)
        return job
    try:
        html = requests.get(job.url, headers=HEADERS, timeout=20).text
        job.description = html_to_text(html)[:8000]
        job.contact_email = job.contact_email or find_contact_email(job.description)
    except requests.RequestException:
        pass
    return job


def from_email_alerts(cfg: dict) -> list[Job]:
    if not cfg.get("enabled"):
        return []
    imap = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
    imap.login(os.environ["MAIL_USER"], os.environ["MAIL_APP_PASSWORD"])
    imap.select("INBOX", readonly=True)
    since = (date.today() - timedelta(days=cfg.get("lookback_days", 3))).strftime("%d-%b-%Y")
    jobs: list[Job] = []
    for sender in cfg.get("from_contains", []):
        _, data = imap.search(None, f'(FROM "{sender}" SINCE {since})')
        for num in data[0].split():
            _, msg_data = imap.fetch(num, "(RFC822)")
            jobs.extend(parse_alert_message(email.message_from_bytes(msg_data[0][1]),
                                            cfg.get("link_patterns", [])))
    imap.logout()
    return jobs


def parse_alert_message(msg, link_patterns: list[str]) -> list[Job]:
    jobs = []
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        soup = BeautifulSoup(part.get_payload(decode=True) or b"", "html.parser")
        for a in soup.find_all("a", href=True):
            if any(p in a["href"] for p in link_patterns) and a.get_text(strip=True):
                jobs.append(Job(title=a.get_text(" ", strip=True), url=a["href"],
                                source="email-alert"))
    return jobs


def collect(sources_cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for url in sources_cfg.get("rss", []):
        jobs += from_rss(url)
    for page in sources_cfg.get("career_pages", []):
        jobs += from_career_page(page["name"], page["url"])
    jobs += from_email_alerts(sources_cfg.get("email_alerts", {}))
    return jobs
