from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

project = "oauthpy"
author = "Philipp Flotho"
copyright = "2026, Philipp Flotho"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.extlinks",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_autodoc_typehints",
]

html_theme = "pydata_sphinx_theme"
html_theme_options = {
    "github_url": "https://github.com/brotherlattice/oauthpy",
    "logo": {"text": "oauthpy"},
    "navbar_end": ["navbar-icon-links"],
    "show_nav_level": 2,
}

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "tasklist",
    "attrs_inline",
    "linkify",
]
myst_heading_anchors = 3
myst_url_schemes = ["http", "https"]

napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_use_param = False
napoleon_use_rtype = False

autoclass_content = "both"
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "undoc-members": True,
    "exclude-members": "__weakref__",
    "show-inheritance": True,
}
autosummary_generate = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

extlinks = {
    "gh": ("https://github.com/brotherlattice/oauthpy/%s", ""),
}
