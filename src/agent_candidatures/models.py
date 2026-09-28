from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Job:
    title: str
    url: str
    company: str = ""
    description: str = ""
    source: str = ""
    location: str = ""
    contact_email: str = ""
    id: str = field(default="")

    def __post_init__(self):
        if not self.id:
            self.id = hashlib.sha1(self.url.split("?")[0].encode()).hexdigest()[:12]
