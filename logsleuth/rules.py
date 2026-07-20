"""Known failure patterns: signature regex -> likely cause and fix."""

import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_RULES = os.path.join(os.path.dirname(__file__), "rules.json")


@dataclass
class Rule:
    id: str
    category: str
    role: str                      # "cause" or "symptom"
    weight: float
    pattern: "re.Pattern[str]"
    cause: str
    fix: List[str] = field(default_factory=list)


def load_rules(*paths: str) -> List[Rule]:
    """Load the bundled rules, then any extra files. Later files win on id."""
    by_id = {}
    for path in (DEFAULT_RULES,) + paths:
        with open(path, encoding="utf-8") as fh:
            for raw in json.load(fh):
                by_id[raw["id"]] = Rule(
                    id=raw["id"],
                    category=raw.get("category", "General"),
                    role=raw.get("role", "cause"),
                    weight=float(raw.get("weight", 1.0)),
                    pattern=re.compile(raw["pattern"], re.IGNORECASE),
                    cause=raw["cause"],
                    fix=list(raw.get("fix", [])),
                )
    return list(by_id.values())


def match_rule(rules: List[Rule], text: str) -> Optional[Rule]:
    """The heaviest rule whose pattern matches text, or None.

    Signatures have their numbers replaced, so patterns that hinge on a
    literal number (429, 50x) only work against a raw message; callers
    should try the example message as well as the signature.
    """
    best = None
    for rule in rules:
        if rule.pattern.search(text) and (best is None or rule.weight > best.weight):
            best = rule
    return best
