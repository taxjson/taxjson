"""Re-audit 2 (crypto-04 list): fill-crypto's par list, `run --fast`
and the crypto map, the de-peg ATTENTION echo, `elect --set` repeated,
the same exchange export in two crypto accounts and the shared price
caches' save. Synthetic data only; no network.

Formerly: test_fix_a2_crypto_run"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import _paths


_RACER = """
import json, sys
from taxjson.lib.json_cache import save_json_cache
path, tag = sys.argv[1], sys.argv[2]
for i in range(40):
    save_json_cache(path, {f"{tag}-{i}": i}, merge=True, indent=2)
"""
_READER = """
import json, sys, time

bad = 0
t0 = time.time()
while time.time() - t0 < 1.5:
    try:
        d = json.loads(open(sys.argv[1]).read())
        if not isinstance(d, dict):
            bad += 1
    except FileNotFoundError:
        pass
    except ValueError:
        bad += 1
print(bad)
"""


class TestSharedCacheSaves(unittest.TestCase):
    """Formerly: test_fix_a2_crypto_run.TestSharedCacheSaves"""
    def test_concurrent_saves_keep_every_entry_and_readers_see_json(self):
        # A2-0233: one fixed `.part` name: lost saves, an empty-looking
        # cache for a concurrent reader.
        import os
        import subprocess
        env = dict(os.environ, PYTHONPATH=str(
            _paths.REPO / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            cache = str(Path(tmp) / ".crypto_price_cache.json")
            reader = subprocess.Popen([sys.executable, "-c", _READER, cache],
                                      stdout=subprocess.PIPE, text=True,
                                      env=env)
            procs = [subprocess.Popen([sys.executable, "-c", _RACER, cache,
                                       tag], env=env,
                                      stderr=subprocess.PIPE, text=True)
                     for tag in ("a", "b")]
            errs = [p.communicate()[1] for p in procs]
            out, _ = reader.communicate()
            self.assertEqual(errs, ["", ""])
            self.assertEqual(out.strip(), "0")
            got = json.loads(Path(cache).read_text())
            self.assertEqual(len(got), 80)
            self.assertEqual([p.name for p in Path(tmp).iterdir()
                              if p.name.endswith(".part")], [])

    def test_the_three_caches_use_it(self):
        from taxjson.bin import to_base_curr
        from taxjson.lib import crypto_prices, price_chain
        import inspect
        for mod, fn in ((crypto_prices, "save_cache"),
                        (to_base_curr, "save_cache"),
                        (price_chain, "_save_cache")):
            self.assertIn("save_json_cache",
                          inspect.getsource(getattr(mod, fn)), mod.__name__)


if __name__ == "__main__":
    unittest.main()
