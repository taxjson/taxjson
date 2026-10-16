"""scripts/private-values, the publisher's fingerprint provider: what it leaves out.

Run on made-up lists in a throwaway directory (TAXJSON_PII_DENYLIST, PII_SCAN_PRIVATE).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestPrivateValues(unittest.TestCase):
    def run_provider(self, private: dict, denylist: str = "") -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            deny, priv = Path(td) / "denylist", Path(td) / "private.json"
            deny.write_text(denylist)
            priv.write_text(json.dumps(private))
            env = dict(os.environ, TAXJSON_PII_DENYLIST=str(deny), PII_SCAN_PRIVATE=str(priv))
            r = subprocess.run([sys.executable, "-I", str(REPO / "scripts" / "private-values")],
                               capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [json.loads(ln)["value"] for ln in r.stdout.splitlines()]

    def test_an_allowlisted_address_is_public(self):
        # A project's published contact: allowlisted, even when it is also the owner's
        # address or on the denylist. A fingerprint hit cannot be excepted, so it stays out.
        contact = "contact@example.org"
        values = self.run_provider(
            {"owner_identity": ["Owner Name", contact],
             "email_allowlist": [contact.upper()],
             "email_watchlist": ["watched@example.org"]},
            denylist="contact@example\\.org\nAnother Name\n")
        self.assertNotIn(contact, [v.lower() for v in values])
        self.assertIn("watched@example.org", values)
        self.assertIn("Another Name", values)
        self.assertNotIn("Owner Name", values)


if __name__ == "__main__":
    unittest.main()
