"""anical.__version__ is the one place the version is set; everything else must agree."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import anical

ROOT = Path(__file__).resolve().parent.parent


def test_newest_metainfo_release_is_the_current_version():
    # Software centres show these entries as release notes, so each release adds one by hand.
    tree = ET.parse(ROOT / "packaging" / "io.github.dhopson.AniCal.metainfo.xml")  # noqa: S314 (our own file)
    releases = tree.getroot().find("releases")
    assert releases[0].get("version") == anical.__version__


def test_pyproject_and_pkgbuild_read_the_version_instead_of_repeating_it():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = { attr = "anical.__version__" }' in pyproject
    assert not re.search(r"^pkgver=[0-9]", (ROOT / "packaging" / "PKGBUILD").read_text(), re.MULTILINE)
