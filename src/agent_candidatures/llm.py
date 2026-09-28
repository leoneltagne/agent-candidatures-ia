"""Petit wrapper autour de l'API Claude qui renvoie du JSON validé."""
from __future__ import annotations

import json
import os
import re


class LLM:
    def __init__(self, model: str, client=None):
        self.model = model
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.client = client

    def json(self, system: str, user: str, max_tokens: int = 2000) -> dict:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system + "\nRéponds UNIQUEMENT avec un objet JSON valide, sans texte autour.",
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"Réponse non JSON : {text[:200]}")
        return json.loads(m.group(0))
