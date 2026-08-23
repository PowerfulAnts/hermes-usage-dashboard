"""Registry discovery tests — synthetic adapters dir, no real data.

Covers: valid adapter loading, broken files skipped into REGISTRY_ERRORS
(syntax error / missing NAME / missing scan / duplicate NAME), and ORDER
sorting of collect_all output.
"""

from __future__ import annotations

import sources


VALID_A = '''
NAME = "alpha"
LABEL = "Alpha"
ORDER = 5

def scan(days=30):
    return {"available": False, "days": days, "totals": {}, "daily": {},
            "models": {}, "meta": {}, "error": "synthetic"}
'''

VALID_B = '''
NAME = "beta"
LABEL = "Beta"
ORDER = 1

def scan(days=30):
    return {"available": False, "days": days, "totals": {}, "daily": {},
            "models": {}, "meta": {}, "error": "synthetic"}
'''

BROKEN_SYNTAX = 'NAME = "x"\ndef scan(:'  # SyntaxError at import

NO_NAME = '''
def scan(days=30):
    return {"available": False}
'''

NO_SCAN = 'NAME = "noscan"\nLABEL = "NoScan"'


def _write_adapters(tmp_path, files: dict[str, str]):
    d = tmp_path / "adapters"
    d.mkdir()
    for fname, content in files.items():
        (d / fname).write_text(content, encoding="utf-8")
    return str(d)


def test_finds_valid_adapters_and_skips_broken(tmp_path):
    adir = _write_adapters(tmp_path, {
        "alpha.py": VALID_A,
        "beta.py": VALID_B,
        "broken.py": BROKEN_SYNTAX,
        "noname.py": NO_NAME,
        "noscan.py": NO_SCAN,
        "_private.py": VALID_A,  # underscore files are never adapters
    })
    mods = sources.discover_adapters(adapters_dir=adir)
    assert set(mods.keys()) == {"alpha", "beta"}
    # every rejected file is reported, nothing raised
    reported = {e["adapter"] for e in sources.REGISTRY_ERRORS}
    assert {"broken.py", "noname.py", "noscan.py"} <= reported


def test_duplicate_name_keeps_first(tmp_path):
    adir = _write_adapters(tmp_path, {
        "one.py": VALID_A,                # NAME alpha, ORDER 5
        "two.py": VALID_A.replace('ORDER = 5', 'ORDER = 9'),
    })
    mods = sources.discover_adapters(adapters_dir=adir)
    assert set(mods.keys()) == {"alpha"}
    assert getattr(mods["alpha"], "ORDER") == 5  # first file (sorted) wins
    assert any("duplicate" in e["error"] for e in sources.REGISTRY_ERRORS)


def test_collect_all_sorts_by_order(tmp_path, monkeypatch):
    adir = _write_adapters(tmp_path, {"alpha.py": VALID_A, "beta.py": VALID_B})
    monkeypatch.setattr(sources, "ADAPTERS_DIR", adir)
    out = sources.collect_all(days=7)
    # beta has ORDER 1 < alpha's 5 → beta first despite alphabetical order
    names = list(out["sources"].keys())
    assert names == ["beta", "alpha"]
    # meta is embedded for the UI
    assert out["sources"]["beta"]["meta"]["label"] == "Beta"
    assert out["sources"]["beta"]["meta"]["order"] == 1
