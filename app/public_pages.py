"""Public pages required by Google's OAuth verification: home page, privacy policy, terms.

The pages are plain HTML files in static/public/ (edit them like any web page, then redeploy), in English
(name.html) and French (name.fr.html). They are the only pages reachable without the team password. "{{CONTACT}}" in a page is replaced
by FIP_CONTACT_EMAIL (server .env), or by a link to the EEIG GVA YouTube channel when it is not set.
"""
import html
import os

from fip import settings

PAGES_DIR = settings.STATIC_DIR / "public"


LANGS = ("en", "fr")
LANG_COOKIE = "fip_lang"   # shared with the app's EN | FR switch
_LANG_CSS = ("header .lang{margin-left:auto;display:flex;gap:2px;border:1px solid #ebeae6;border-radius:999px;overflow:hidden}"
             "header .lang a{padding:2px 9px;font-size:12px;font-weight:600;text-decoration:none;color:#6b7080}"
             "header .lang a.on{background:#5b2bd6;color:#fff}")


def pick_lang(query: str | None, cookie: str | None, accept: str | None) -> str:
    """?lang= first, then the saved choice, then the browser's language; English otherwise."""
    for v in (query, cookie):
        if v in LANGS:
            return v
    return "fr" if (accept or "").strip().lower().startswith("fr") else "en"


def _contact(lang: str) -> str:
    settings.reload_env()
    email = os.getenv("FIP_CONTACT_EMAIL", "").strip()
    if email:
        e = html.escape(email)
        return f'<a href="mailto:{e}">{e}</a>'
    link = '<a href="https://www.youtube.com/@eeiggva">EEIG GVA YouTube channel</a>'
    return (f"l'équipe EEIG GVA via la {link.replace('EEIG GVA YouTube channel', 'chaîne YouTube EEIG GVA')}"
            if lang == "fr" else f"the EEIG GVA team via the {link}")


def render(name: str, lang: str = "en") -> str:
    f = PAGES_DIR / (f"{name}.fr.html" if lang == "fr" else f"{name}.html")
    switch = '<nav class="lang">' + "".join(
        f'<a href="?lang={code}" class="{"on" if code == lang else ""}" hreflang="{code}">{code.upper()}</a>'
        for code in LANGS) + "</nav>"
    return (f.read_text(encoding="utf-8").replace("{{CONTACT}}", _contact(lang))
            .replace("{{LANGSWITCH}}", switch).replace("{{LANGCSS}}", _LANG_CSS))


def about(lang: str = "en") -> str:
    return render("about", lang)


def privacy(lang: str = "en") -> str:
    return render("privacy", lang)


def terms(lang: str = "en") -> str:
    return render("terms", lang)
