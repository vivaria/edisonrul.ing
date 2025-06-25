# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'edisonrul.ing'
author = 'vivaria'
copyright = 'vivaria'
version = '0.0.2'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'myst_parser',
    'sphinx_last_updated_by_git',
    'sphinx_immaterial'
]
myst_enable_extensions = [
    "amsmath",
    "attrs_inline",
    "colon_fence",
    "deflist",
    "dollarmath",
    "fieldlist",
    "html_admonition",
    "html_image",
    # "linkify",
    "replacements",
    "smartquotes",
    "strikethrough",
    "substitution",
    "tasklist",
]
templates_path = ['_templates']
exclude_patterns = []
suppress_warnings = ['epub.unknown_project_files']



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "sphinx_immaterial"
html_theme_options = {
    "edit_uri": "blob/main/docs/source",
    "repo_url": "https://github.com/vivaria/edisonrul.ing",
    "repo_name": "edisonrul.ing",
    "icon": {
        "repo": "fontawesome/brands/github",
        "edit": "material/file-edit-outline",
        "logo": "material/cards-playing"
    },
    "features": [
        "content.action.view",
        "content.action.edit",
        "navigation.expand",
        "navigation.footer"
    ],
    "font": {
        "text": "Funnel Sans",  # used for all the pages' text
        "code": "Roboto Mono"  # used for literal code blocks
    },
    "palette": [
        {
            "media": "(prefers-color-scheme: light)",
            "scheme": "default",
            "primary": "brown",
            "toggle": {
                "icon": "material/weather-night",
                "name": "Switch to dark mode",
            }
        },
        {
            "media": "(prefers-color-scheme: dark)",
            "scheme": "slate",
            "primary": "brown",
            "toggle": {
                "icon": "material/weather-sunny",
                "name": "Switch to light mode",
            }
        },
    ]
}
html_title = 'Edison Rulings'
html_favicon = '_static/img/favicon.ico'
html_static_path = ['_static']
html_css_files = ['css/custom.css']
html_context = {
    'display_github': True,
    'github_user': 'vivaria',
    'github_repo': 'edisonrul.ing',
    'conf_py_path': '/docs/source/',
    'commit': 'main',
    'downloads': [
        ('pdf', 'https://edisonrul.ing/_/downloads/en/latest/pdf/'),
        ('epub', 'https://edisonrul.ing/_/downloads/en/latest/epub/'),
    ],
}
html_last_updated_fmt = '%Y-%m-%d'
html_show_copyright = False
html_copy_source = False
html_show_sourcelink = True