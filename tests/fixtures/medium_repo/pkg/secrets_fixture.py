"""File with hardcoded secrets — ``hardcoded_secret`` rule positive cases.

Filename intentionally NOT just ``secrets.py`` because that conflicts
with the stdlib ``secrets`` module on import.

The Stripe-style key check is deliberately exercised by a
**runtime-assembled** test string in ``tests/test_detection_rules_phase2``
rather than by a literal in this file — GitHub Push Protection
(rightly) flags Stripe-shaped strings even when synthetic, so we
keep this file Stripe-free and verify the regex pattern via direct
unit test instead.

Cases here:

* ``AWS_KEY``     — known AWS access-key pattern (GitHub allowlists the
  documented EXAMPLE key, so this is safe to commit).
* ``API_TOKEN``   — generic high-entropy token assigned to a clearly
  suspicious name; exercises the entropy heuristic.
* ``DATABASE_URL`` — Postgres URL with embedded credentials.
"""

from __future__ import annotations

# AWS access key style — uses AWS's documented EXAMPLE key, allowlisted.
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"

# Generic high-entropy token in a name the rule clearly recognises.
API_TOKEN = "R8dGq2VfX9p4mLk7N1zT_3a9PqMxKvB"

# A Postgres URL with embedded credentials. Whether the rule flags this
# depends on the heuristic's name list; the suspicious-name regex
# deliberately excludes 'url' to avoid noise on legitimate URL constants.
DATABASE_URL = "postgres://user:R8dGq2VfX9p4mLk7N1zT@db.example.com/prod"
