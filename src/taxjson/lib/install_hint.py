"""Install lines the CLI prints, in one place.

taxjson and taxjson-fetch are NOT published on PyPI yet, so no message
may tell pip to install either by name: from PyPI that installs whatever
package someone else registered under the name (pre-release security
review H1). Every hint names the installer (which installs from the
GitHub release into its own environment) or an editable install from
a checkout instead, and says the PyPI name is not ours.
"""

INSTALLER = 'bash -c "$(curl -fsSL https://taxjson.com/install.sh)"'

NOT_ON_PYPI = ("taxjson is not published on PyPI yet, so a taxjson or "
               "taxjson-fetch package there is not ours")


def extra_hint(extra: str) -> str:
    """How to add an optional extra (fx, ibkr, xlsx...) to taxjson's
    own environment."""
    extras = "fx" if extra == "fx" else f"fx,{extra}"
    return (f"install the [{extra}] extra into taxjson's environment: "
            f"re-run the installer with TAXJSON_EXTRAS={extras} "
            f"(`TAXJSON_EXTRAS={extras} {INSTALLER}`), or from a "
            f"checkout `pip install -e '.[{extra}]'` — {NOT_ON_PYPI}")
