#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "markdown-it-py>=3.0",
#     "mdit-py-plugins>=0.4",
#     "linkify-it-py>=2.0",
#     "pyyaml>=6.0",
#     "latex2mathml>=3.76",
# ]
# ///
"""Builds the website from content/ into _site/.

    uv run build.py                    build once
    uv run build.py --serve            build, preview on http://localhost:8000 and rebuild on save
    uv run build.py --serve --drafts   the same, including posts marked `draft: true`

Posts are Markdown files in content/posts/. They may use Obsidian syntax:
[[wikilinks]], ![[embeds]], > [!callouts], ==highlights==, #tags, $math$ and %%comments%%.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import functools
import html
import http.server
import json
import re
import shutil
import sys
import threading
import time
import tomllib
import traceback
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from urllib.parse import quote, unquote, urlparse

import yaml
from latex2mathml.converter import convert as latex_to_mathml
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
POSTS = CONTENT / "posts"
DRAFTS = CONTENT / "drafts"  # git-ignored: only ever shown in the local --drafts preview
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
OUT = ROOT / "_site"

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg"}
LONG_QUOTE_WORDS = 80
LATEST_POSTS = 5
FEED_POSTS = 20

LINK_LABELS = {
    "email": "Email", "github": "GitHub", "gitlab": "GitLab", "linkedin": "LinkedIn",
    "scholar": "Google Scholar", "orcid": "ORCID", "researchgate": "ResearchGate",
    "bluesky": "Bluesky", "mastodon": "Mastodon", "instagram": "Instagram", "youtube": "YouTube",
    "goodreads": "Goodreads", "letterboxd": "Letterboxd", "strava": "Strava", "cv": "CV",
}

STRINGS = {
    "en": {
        "months": ["January", "February", "March", "April", "May", "June", "July",
                   "August", "September", "October", "November", "December"],
        "day_sep": "",
        "min_read": "min read",
        "mentioned_in": "Mentioned in",
        "connections": "Connections",
        "graph_label": "Map of the posts and how they link to each other",
        "all_posts": "All entries",
        "notes": "Wiki",
        "no_posts": "No entries yet.",
        "updated": "Updated",
        "other": "Other",
        "feed": "Subscribe via RSS",
        "draft": "draft",
        "skip": "Skip to content",
        "not_found": "Page not found",
        "not_found_text": 'There is nothing here. Try the <a href="/">home page</a>.',
    },
    "de": {
        "months": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                   "August", "September", "Oktober", "November", "Dezember"],
        "day_sep": ".",
        "min_read": "Min. Lesezeit",
        "mentioned_in": "Erwähnt in",
        "connections": "Verbindungen",
        "graph_label": "Karte der Beiträge und ihrer Verbindungen",
        "all_posts": "Alle Einträge",
        "notes": "Wiki",
        "no_posts": "Noch keine Einträge.",
        "updated": "Aktualisiert",
        "other": "Sonstiges",
        "feed": "Per RSS abonnieren",
        "draft": "Entwurf",
        "skip": "Zum Inhalt springen",
        "not_found": "Seite nicht gefunden",
        "not_found_text": 'Hier gibt es nichts. Zur <a href="/">Startseite</a>.',
    },
}

# Obsidian's callout types, grouped into the few colours the stylesheet knows.
# Anything not listed (note, info, abstract, todo, ...) uses the accent colour.
CALLOUT_FAMILIES = {
    "tip": "tip", "hint": "tip", "important": "tip", "success": "tip", "check": "tip", "done": "tip",
    "question": "question", "help": "question", "faq": "question", "example": "question",
    "warning": "warning", "caution": "warning", "attention": "warning",
    "danger": "danger", "error": "danger", "failure": "danger", "fail": "danger",
    "missing": "danger", "bug": "danger",
    "quote": "quote", "cite": "quote",
}

FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?", re.S | re.M)
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.S | re.M)
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)", re.S)
COMMENT = re.compile(r"((?<!`)(`+)(?!`).+?(?<!`)\2(?!`))|%%.*?%%|<!--.*?-->", re.S)  # group 1: code, kept
CALLOUT = re.compile(r"^>[ \t]?\[!([\w-]+)\]([+-]?)[ \t]*(.*)$")
DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)
INLINE_MATH = re.compile(r"(?<![\\$\w])\$(?=[^\s$])(.+?)(?<=[^\s\\$])\$(?![\w$])")
EMBED = re.compile(r"!\[\[([^\]\n]+)\]\]")
WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
HIGHLIGHT = re.compile(r"==(?=\S)(.+?)(?<=\S)==")
TAG = re.compile(r"(?:(?<=\s)|^)#([\w/-]*[^\W\d][\w/-]*)", re.M)
DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})[ _-]*")


@dataclass(eq=False)
class Doc:
    source: Path
    kind: str  # "page" (content/*.md) or "post" (content/posts/**.md)
    meta: dict
    body: str
    title: str
    url: str  # root-relative, e.g. "/notes/my-post/"
    date: dt.date | None = None
    updated: dt.date | None = None  # `updated:` in the frontmatter, otherwise the date
    draft: bool = False
    tags: list[str] = field(default_factory=list)
    html: str = ""
    summary: str = ""
    minutes: int = 1
    links_to: set[str] = field(default_factory=set)


# ---------------------------------------------------------------- helpers

def esc(text) -> str:
    return html.escape(str(text), quote=True)


def slugify(text) -> str:
    text = str(text).strip().lower()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, plain)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "untitled"


def heading_slug(text: str) -> str:
    """Anchor ids for headings; also used to resolve [[Note#Heading]]."""
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", text.strip().lower()))


def strip_tags(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def first_paragraph(fragment: str, limit: int = 180) -> str:
    for paragraph in re.findall(r"<p>(.*?)</p>", fragment, re.S):
        text = strip_tags(paragraph)
        if len(text) >= 40:
            break
    else:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:.-–—") + "…"


def to_date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and (m := re.match(r"(\d{4})-(\d{2})-(\d{2})", value.strip())):
        return dt.date(*map(int, m.groups()))
    return None


def as_list(value, separators: str = r",") -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return [v.strip() for v in re.split(separators, str(value)) if v.strip()]


def split_target(raw: str) -> tuple[str, str]:
    """'Note#Heading|alias' -> ('Note#Heading', 'alias'). Tables escape the pipe as \\|."""
    target, _, alias = raw.replace("\\|", "|").partition("|")
    return target.strip(), alias.strip()


def entities(text: str) -> str:
    """Hides an email address from the simplest spam harvesters."""
    return "".join(f"&#{ord(c)};" for c in text)


def relativize(page: str, prefix: str) -> str:
    """Rewrites root-relative links ("/about/") so the site works from any folder or subpath."""
    return re.sub(
        r'\b(href|src)="/(?!/)([^"]*)"',
        lambda m: f'{m.group(1)}="{prefix + m.group(2) or "./"}"',
        page,
    )


# ---------------------------------------------------------------- markdown

class Renderer:
    """Turns one document's Obsidian-flavoured Markdown into HTML.

    Obsidian syntax is replaced by placeholders before the Markdown parser runs,
    and the placeholders are swapped for finished HTML afterwards, so the parser
    never mangles math, links or callouts.
    """

    def __init__(self, site: Site, doc: Doc):
        self.site, self.doc = site, doc
        self.held: list[str] = []

    def hold(self, fragment: str) -> str:
        self.held.append(fragment)
        return f"OBSPH{len(self.held) - 1}X"

    def document(self, text: str) -> str:
        self._check_quotes(text)
        return self._fix_urls(self.render(text))

    def render(self, text: str) -> str:
        code: list[str] = []

        def keep(m: re.Match) -> str:
            code.append(m.group(0))
            return f"OBSCODE{len(code) - 1}X"

        text = FENCE.sub(keep, text)
        text = COMMENT.sub(lambda m: m.group(1) or "", text)  # %% private %% and <!-- --> comments never reach the site
        text = self._callouts(text)
        text = INLINE_CODE.sub(keep, text)
        if "\\$" in text:
            text = text.replace("\\$", self.hold("&#36;"))
        text = DISPLAY_MATH.sub(lambda m: self._math(m.group(1), display=True), text)
        text = INLINE_MATH.sub(lambda m: self._math(m.group(1), display=False), text)
        text = EMBED.sub(lambda m: self._embed(*split_target(m.group(1))), text)
        text = WIKILINK.sub(lambda m: self._wikilink(*split_target(m.group(1))), text)
        text = HIGHLIGHT.sub(r"<mark>\1</mark>", text)
        text = TAG.sub(self._tag, text)
        text = re.sub(r"OBSCODE(\d+)X", lambda m: code[int(m.group(1))], text)

        out = self.site.md.render(text)
        out = re.sub(r"<p>OBSPH(\d+)X</p>", lambda m: self.held[int(m.group(1))], out)
        return re.sub(r"OBSPH(\d+)X", lambda m: self.held[int(m.group(1))], out)

    def inline(self, text: str) -> str:
        out = self.render(text).strip()
        m = re.fullmatch(r"<p>(.*)</p>", out, re.S)
        return m.group(1) if m else out

    def _callouts(self, text: str) -> str:
        lines, out, i = text.split("\n"), [], 0
        while i < len(lines):
            m = CALLOUT.match(lines[i])
            if not m:
                out.append(lines[i])
                i += 1
                continue
            kind, fold, title = m.group(1).lower(), m.group(2), m.group(3).strip()
            body = []
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                body.append(re.sub(r"^>[ \t]?", "", lines[i]))
                i += 1
            family = CALLOUT_FAMILIES.get(kind, "note")
            title_html = self.inline(title) if title else esc(kind.replace("-", " ").capitalize())
            inner = ""
            if any(line.strip() for line in body):
                inner = f'<div class="callout-body">{self.render(chr(10).join(body))}</div>'
            if fold:
                opened = " open" if fold == "+" else ""
                block = (f'<details class="callout callout-{family}"{opened}>'
                         f'<summary class="callout-title">{title_html}</summary>{inner}</details>')
            else:
                block = (f'<div class="callout callout-{family}">'
                         f'<p class="callout-title">{title_html}</p>{inner}</div>')
            out += ["", self.hold(block), ""]
        return "\n".join(out)

    def _math(self, tex: str, display: bool) -> str:
        tex = tex.strip()
        try:
            mathml = latex_to_mathml(tex, display="block" if display else "inline")
        except Exception as error:  # latex2mathml raises many different exception types
            self.site.warn(self.doc, f"couldn't render the math `{tex[:40]}` ({error.__class__.__name__})")
            return self.hold(f'<code class="math-error">{esc(tex)}</code>')
        return self.hold(f'<span class="math-display">{mathml}</span>' if display else mathml)

    def _embed(self, target: str, option: str) -> str:
        name = target.split("#")[0].strip()
        suffix = Path(name).suffix.lower()
        if not suffix or suffix == ".md":
            self.site.warn(self.doc, f"![[{target}]] embeds a whole note, which the site can't do; it is shown as a link")
            return self._wikilink(target, option)
        src = self.site.media_url(name)
        if src is None:
            self.site.warn(self.doc, f"can't find {name}; put it in content/posts/assets/")
            return ""
        if suffix not in IMAGE_TYPES:
            return self.hold(f'<a href="{src}">{esc(option or Path(name).name)}</a>')
        size = re.fullmatch(r"(\d+)(?:x(\d+))?", option)
        attrs = f' alt="{esc("" if size else option)}"'
        if size:
            attrs += f' width="{size.group(1)}"' + (f' height="{size.group(2)}"' if size.group(2) else "")
        return self.hold(f'<img src="{src}"{attrs} loading="lazy">')

    def _wikilink(self, target: str, alias: str = "") -> str:
        page, _, heading = target.partition("#")
        page, heading = page.strip().removesuffix(".md"), heading.strip()
        if alias:
            label = alias
        elif page and heading:
            label = f"{page} › {heading.lstrip('^')}"
        else:
            label = page or heading.lstrip("^")
        if not page:
            return self.hold(f'<a href="#{heading_slug(heading)}">{esc(label)}</a>')
        doc = self.site.lookup(page)
        if doc is None:
            self.site.warn(self.doc, f"[[{page}]] is not published here, so it is shown as plain text")
            return self.hold(esc(label))
        if doc is not self.doc:
            self.doc.links_to.add(doc.url)
        anchor = f"#{heading_slug(heading)}" if heading and not heading.startswith("^") else ""
        return self.hold(f'<a class="internal" href="{doc.url}{anchor}">{esc(label)}</a>')

    def _tag(self, m: re.Match) -> str:
        tag = m.group(1)
        if self.doc.kind != "post":
            return self.hold(esc(f"#{tag}"))
        if slugify(tag) not in {slugify(t) for t in self.doc.tags}:
            self.doc.tags.append(tag)
        return self.hold(f'<a class="tag" href="{self.site.tag_url(tag)}">#{esc(tag)}</a>')

    def _fix_urls(self, fragment: str) -> str:
        """Resolves relative links: other notes (Note.md) and files (assets/figure.png)."""
        def fix(m: re.Match) -> str:
            attr, url = m.group(1), m.group(2)
            if not url or re.match(r"^(?:[a-z][a-z0-9+.-]*:|/|#|\?)", url, re.I):
                return m.group(0)
            path, _, anchor = url.partition("#")
            name = unquote(path)
            if name.lower().endswith(".md"):
                doc = self.site.lookup(name[:-3])
                if doc is None:
                    self.site.warn(self.doc, f"links to {name}, which is not published here")
                    return m.group(0)
                self.doc.links_to.add(doc.url)
                return f'{attr}="{doc.url}' + (f"#{heading_slug(unquote(anchor))}" if anchor else "") + '"'
            src = self.site.media_url(name)
            if src is None:
                self.site.warn(self.doc, f"can't find {name}; put it in content/posts/assets/")
                return m.group(0)
            return f'{attr}="{src}"'

        return re.sub(r'\b(href|src)="([^"]*)"', fix, fragment)

    def _check_quotes(self, text: str) -> None:
        text = FENCE.sub("", text)
        for block in re.findall(r"(?:^>.*\n?)+", text, re.M):
            head = CALLOUT.match(block.split("\n", 1)[0])
            if head and head.group(1).lower() not in ("quote", "cite"):
                continue
            words = len(re.findall(r"\w+", block))
            if words > LONG_QUOTE_WORDS:
                self.site.warn(self.doc, f"has a {words}-word quote; check that you may publish it "
                                         "(short quotes with a source are usually fine)")


# ---------------------------------------------------------------- site

class Site:
    def __init__(self, drafts: bool = False):
        self.config = tomllib.loads((ROOT / "site.toml").read_text(encoding="utf-8"))
        info = self.config.get("site", {})
        self.name = info.get("name", "My website")
        self.tagline = info.get("tagline", "")
        self.description = info.get("description", "")
        self.base_url = info.get("base_url", "").rstrip("/")
        self.lang = info.get("language", "en")
        self.t = STRINGS.get(self.lang, STRINGS["en"])
        self.since = info.get("since")
        self.section = self.config.get("writing", {}).get("path", "notes").strip("/")
        self.drafts = drafts
        self.template = Template((TEMPLATES / "base.html").read_text(encoding="utf-8"))
        self.md = (
            MarkdownIt("commonmark", {"html": True, "linkify": True,
                                      "breaks": info.get("obsidian_line_breaks", True)})
            .enable(["table", "strikethrough", "linkify"])
            .use(footnote_plugin)
            .use(tasklists_plugin)
            .use(anchors_plugin, min_level=2, max_level=4, slug_func=heading_slug)
        )
        self.md.linkify.set({"fuzzy_link": False})  # only link bare URLs with https://, not "M.Sc."
        self.media = {p.name.lower(): p for p in CONTENT.rglob("*")
                      if p.is_file() and p.suffix.lower() != ".md"}
        self.warnings: list[str] = []
        self.written: set[Path] = set()
        self.pages: list[Doc] = []
        self.posts: list[Doc] = []
        self.by_name: dict[str, Doc] = {}
        self.tags: dict[str, tuple[str, list[Doc]]] = {}

    def build(self) -> None:
        self.load()
        for doc in self.posts + self.pages:  # posts first: pages list them
            self.render(doc)
        self.write_all()

    # -- reading

    def read(self, path: Path) -> tuple[dict, str]:
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        m = FRONTMATTER.match(text)
        if not m:
            return {}, text
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            self.warn(path, "the frontmatter is not valid YAML and was ignored")
            meta = {}
        return (meta if isinstance(meta, dict) else {}), text[m.end():]

    def load(self) -> None:
        if self.name == "Your Name":
            self.warn(ROOT / "site.toml", "set your name")
        if not self.base_url or "example.com" in self.base_url:
            self.warn(ROOT / "site.toml", "set base_url to the site's final address")

        for path in sorted(CONTENT.glob("*.md")):
            if path.name.startswith(("_", ".")):
                continue
            meta, body = self.read(path)
            slug = "" if path.stem == "index" else slugify(meta.get("slug") or path.stem)
            title = str(meta.get("title") or path.stem.replace("-", " ").capitalize())
            self.pages.append(Doc(path, "page", meta, body, title, f"/{slug}/" if slug else "/"))

        taken = {doc.url for doc in self.pages}
        sources = [(folder, path) for folder in (POSTS, DRAFTS) if folder.is_dir()
                   for path in sorted(folder.rglob("*.md"))]
        for folder, path in sources:
            if any(part.startswith(("_", ".")) for part in path.relative_to(folder).parts):
                continue
            meta, body = self.read(path)
            draft = folder == DRAFTS or meta.get("draft") is True or meta.get("publish") is False
            if draft and folder == POSTS:
                self.warn(path, "is a draft but lives in content/posts/, so it is still uploaded to GitHub; "
                                "move it to content/drafts/ to keep it private")
            if draft and not self.drafts:
                continue
            name = DATE_PREFIX.sub("", path.stem) or path.stem
            date = to_date(meta.get("date")) or to_date(path.stem)
            if date is None:
                self.warn(path, "has no date; add `date: YYYY-MM-DD` to the frontmatter")
                date = dt.date.fromtimestamp(path.stat().st_mtime)
            url = f"/{self.section}/{slugify(meta.get('slug') or name)}/"
            if url in taken:
                self.warn(path, f"another post already uses the address {url}; give it a different `slug:`")
                continue
            taken.add(url)
            tags = [t.lstrip("#") for t in as_list(meta.get("tags"), r"[,\s]+") if t.lstrip("#")]
            title = str(meta.get("title") or name)
            updated = max(date, to_date(meta.get("updated")) or date)
            self.posts.append(Doc(path, "post", meta, body, title, url, date=date, updated=updated,
                                  draft=draft, tags=tags))
        self.posts.sort(key=lambda d: (d.updated, d.title), reverse=True)  # most recently updated first

        for doc in self.pages + self.posts:
            for alias in [doc.source.stem, doc.title, *as_list(doc.meta.get("aliases"))]:
                self.by_name.setdefault(alias.strip().lower(), doc)

    def lookup(self, name: str) -> Doc | None:
        name = name.replace("\\", "/").split("/")[-1].strip().lower()
        return self.by_name.get(name)

    def media_url(self, name: str) -> str | None:
        src = self.media.get(Path(unquote(name)).name.lower())
        if src is None:
            return None
        self.copy(src, Path("media") / src.name)
        return "/media/" + quote(src.name)

    def tag_url(self, tag: str) -> str:
        return f"/{self.section}/tags/{slugify(tag.replace('/', '-'))}/"

    def warn(self, where: Doc | Path, message: str) -> None:
        path = where.source if isinstance(where, Doc) else where
        entry = f"{path.relative_to(ROOT).as_posix()}: {message}"
        if entry not in self.warnings:
            self.warnings.append(entry)

    # -- rendering

    def render(self, doc: Doc) -> None:
        doc.html = Renderer(self, doc).document(doc.body)
        if doc.kind == "page":
            doc.html = self.shortcodes(doc, doc.html)
        words = len(re.findall(r"\w+", strip_tags(doc.html)))
        doc.minutes = max(1, round(words / 220))
        doc.summary = str(doc.meta.get("description") or first_paragraph(doc.html))
        if todos := doc.body.count("TODO"):
            self.warn(doc, f"still contains {todos} TODO placeholder{'s' if todos > 1 else ''}")
        if doc.kind == "post":
            for tag in doc.tags:
                self.tags.setdefault(slugify(tag.replace("/", "-")), (tag, []))[1].append(doc)

    def shortcodes(self, doc: Doc, fragment: str) -> str:
        def expand(m: re.Match) -> str:
            name = m.group(1)
            if name == "latest_posts":  # heading and list, or nothing while there are no posts
                if not self.posts:
                    return ""
                intro = next((d for d in self.pages if d.url == f"/{self.section}/"), None)
                title = intro.title if intro else self.t["notes"]
                more = ""
                if len(self.posts) > LATEST_POSTS:
                    more = f'<p class="back"><a href="/{self.section}/">{self.t["all_posts"]} →</a></p>'
                return f"<h2>{esc(title)}</h2>\n{self.post_list(self.posts[:LATEST_POSTS])}{more}"
            if name == "links":
                return f'<p class="links">{self.links}</p>'
            self.warn(doc, f"unknown shortcode {{{{ {name} }}}}")
            return m.group(0)

        return re.sub(r"(?:<p>)?\{\{\s*(\w+)\s*\}\}(?:</p>)?", expand, fragment)

    def long_date(self, d: dt.date) -> str:
        return f'{d.day}{self.t["day_sep"]} {self.t["months"][d.month - 1]} {d.year}'

    def short_date(self, d: dt.date, year: bool = False) -> str:
        text = f'{d.day}{self.t["day_sep"]} {self.t["months"][d.month - 1][:3]}'
        return f"{text} {d.year}" if year else text

    @functools.cached_property
    def links(self) -> str:
        out = []
        for key, value in self.config.get("links", {}).items():
            value = str(value).strip()
            if not value:
                continue
            label = esc(LINK_LABELS.get(key, key.replace("_", " ").title()))
            if key == "email":
                out.append(f'<a href="{entities("mailto:" + value.removeprefix("mailto:"))}">{label}</a>')
            elif re.match(r"^[a-z][a-z0-9+.-]*:", value, re.I):
                out.append(f'<a href="{esc(value)}" rel="me">{label}</a>')
            else:
                out.append(f'<a href="/{esc(value.lstrip("/"))}">{label}</a>')
        if self.posts:
            out.append('<a href="/feed.xml">RSS</a>')
        return "\n      ".join(out)

    def post_list(self, posts: list[Doc]) -> str:
        """Recently updated entries, with dates (home page)."""
        def item(post: Doc) -> str:
            draft = f' <span class="draft">{self.t["draft"]}</span>' if post.draft else ""
            summary = f'<p class="summary">{esc(post.summary)}</p>' if post.summary else ""
            return (f'<li><time datetime="{post.updated.isoformat()}">{self.short_date(post.updated, year=True)}</time>'
                    f'<div><a href="{post.url}">{esc(post.title)}</a>{draft}{summary}</div></li>')

        return '<ul class="post-list">' + "".join(map(item, posts)) + "</ul>"

    def entry_list(self, posts: list[Doc]) -> str:
        """Entries in alphabetical order, without dates, like a wiki index."""
        items = []
        for post in sorted(posts, key=lambda p: p.title.lower()):
            draft = f' <span class="draft">{self.t["draft"]}</span>' if post.draft else ""
            summary = f'<p class="summary">{esc(post.summary)}</p>' if post.summary else ""
            items.append(f'<li><a href="{post.url}">{esc(post.title)}</a>{draft}{summary}</li>')
        return '<ul class="entry-list">' + "".join(items) + "</ul>"

    def topic_index(self, posts: list[Doc]) -> str:
        """Entries grouped by topic: an entry's first tag. Entries without tags go under "Other"."""
        topics: dict[str, list[Doc]] = {}
        for post in posts:
            topics.setdefault(post.tags[0] if post.tags else "", []).append(post)
        parts = []
        for topic in sorted(topics, key=lambda t: (t == "", t.lower())):
            name = topic.replace("-", " ").replace("_", " ")
            heading = name[:1].upper() + name[1:] if name else self.t["other"]
            parts.append(f'<h2 class="topic">{esc(heading)}</h2>\n{self.entry_list(topics[topic])}')
        return "\n".join(parts)

    def page_body(self, doc: Doc) -> str:
        if doc.meta.get("layout") == "home":
            portrait = ""
            src = doc.meta.get("portrait")
            if src and src.startswith("/") and not (ROOT / src.lstrip("/")).is_file():
                stem = (ROOT / src.lstrip("/")).with_suffix("")  # accept portrait.png etc. as well
                found = next((stem.with_suffix(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif")
                              if stem.with_suffix(ext).is_file()), None)
                if found:
                    src = "/" + found.relative_to(ROOT).as_posix()
                else:
                    self.warn(doc, f"the portrait {src.lstrip('/')} doesn't exist yet; the home page shows no photo until it does")
                    src = None
            if src:
                alt = doc.meta.get("portrait_alt") or f"Portrait of {self.name}"
                portrait = f'<img class="portrait" src="{esc(src)}" alt="{esc(alt)}" width="112" height="112">'
            tagline = f'<p class="tagline">{esc(self.tagline)}</p>' if self.tagline else ""
            return (f'<section class="intro">{portrait}<div><h1>{esc(self.name)}</h1>{tagline}</div></section>\n'
                    f'<div class="prose">{doc.html}</div>')
        return f'<article class="page">\n<h1>{esc(doc.title)}</h1>\n<div class="prose">{doc.html}</div>\n</article>'

    def post_body(self, post: Doc, mentions: list[Doc]) -> str:
        meta = (f'{self.t["updated"]} <time datetime="{post.updated.isoformat()}">{self.long_date(post.updated)}</time>'
                f' · {post.minutes} {self.t["min_read"]}')
        if post.draft:
            meta += f' <span class="draft">{self.t["draft"]}</span>'
        tags = "".join(f'<a class="tag" href="{self.tag_url(t)}">#{esc(t)}</a>' for t in post.tags)
        parts = [
            '<article class="post">',
            f'<header class="post-header">\n<h1>{esc(post.title)}</h1>\n<p class="post-meta">{meta}</p>'
            + (f'\n<p class="tags">{tags}</p>' if tags else "") + "\n</header>",
            f'<div class="prose">{post.html}</div>',
        ]
        mentions = [d for d in dict.fromkeys(mentions) if d is not post]
        neighbours = [p for p in self.posts if p is not post and (p.url in post.links_to or p in mentions)]
        if neighbours:
            parts.append(self.graph_html([post, *neighbours], tags_of=[post], current=post,
                                         height=260, caption=self.t["connections"]))
        if mentions:
            items = "".join(f'<li><a href="{d.url}">{esc(d.title)}</a></li>' for d in mentions)
            parts.append(f'<aside class="backlinks">\n<h2>{self.t["mentioned_in"]}</h2>\n<ul>{items}</ul>\n</aside>')
        parts.append(f'<p class="back"><a href="/{self.section}/">← {self.t["all_posts"]}</a></p>')
        parts.append("</article>")
        return "\n".join(parts)

    def section_body(self, intro: Doc | None) -> str:
        title = intro.title if intro else self.t["notes"]
        parts = ['<article class="page">', f"<h1>{esc(title)}</h1>"]
        if intro:
            parts.append(f'<div class="prose">{intro.html}</div>')
        if len(self.posts) > 1:
            parts.append(self.graph_html(self.posts, tags_of=self.posts))
        if self.tags:
            ranked = sorted(self.tags.values(), key=lambda tp: (-len(tp[1]), tp[0].lower()))
            parts.append('<p class="topics">' + "".join(
                f'<a class="tag" href="{self.tag_url(tag)}">#{esc(tag)} <span>{len(posts)}</span></a>'
                for tag, posts in ranked) + "</p>")
        if self.posts:
            parts.append(self.topic_index(self.posts))
            parts.append(f'<p class="feed-link"><a href="/feed.xml">{self.t["feed"]}</a></p>')
        else:
            parts.append(f'<p>{self.t["no_posts"]}</p>')
        parts.append("</article>")
        return "\n".join(parts)

    def graph_html(self, posts: list[Doc], tags_of: list[Doc], current: Doc | None = None,
                   height: int = 380, caption: str = "") -> str:
        """An Obsidian-style graph of posts, their links and tags; static/graph.js draws it."""
        nodes: list[dict] = []
        index: dict[str, int] = {}
        edges: set[tuple[int, int]] = set()

        def node(url: str, title: str, kind: str) -> int:
            if url not in index:
                index[url] = len(nodes)
                nodes.append({"url": url, "title": title, "kind": kind})
            return index[url]

        for post in posts:
            node(post.url, post.title, "current" if post is current else "post")
        for post in posts:
            for url in post.links_to:
                if url in index and url != post.url:
                    edges.add(tuple(sorted((index[post.url], index[url]))))
        for post in tags_of:
            for tag in post.tags:
                edges.add(tuple(sorted((index[post.url], node(self.tag_url(tag), f"#{tag}", "tag")))))

        data = json.dumps({"nodes": nodes, "links": sorted(edges)}, ensure_ascii=False).replace("</", "<\\/")
        caption_html = f"<figcaption>{esc(caption)}</figcaption>" if caption else ""
        # The hidden link is rewritten like every other link, so the script knows the site's root.
        return (f'<figure class="graph" style="--graph-height: {height}px" aria-label="{esc(self.t["graph_label"])}">'
                f'{caption_html}<a class="graph-root" href="/" hidden></a>'
                f'<script type="application/json">{data}</script></figure>\n'
                '<script src="/static/graph.js" defer></script>')

    def frame(self, url: str, title: str, body: str, description: str = "", og_type: str = "website") -> str:
        nav = []
        for doc in sorted((d for d in self.pages if "nav" in d.meta), key=lambda d: d.meta["nav"]):
            current = url == doc.url or (doc.url != "/" and url.startswith(doc.url))
            attr = ' aria-current="page"' if current else ""
            nav.append(f'<a href="{doc.url}"{attr}>{esc(doc.meta.get("nav_title") or doc.title)}</a>')
        footer_pages = "".join(f' · <a href="{d.url}">{esc(d.title)}</a>' for d in self.pages if d.meta.get("footer"))
        year = dt.date.today().year
        years = f"{self.since}–{year}" if self.since and int(self.since) < year else str(year)
        return self.template.substitute(
            lang=esc(self.lang),
            title=esc(self.name if url == "/" else f"{title} · {self.name}"),
            og_title=esc(title),
            description=esc(description or self.description),
            canonical=esc(self.base_url + url),
            og_type=og_type,
            site_name=esc(self.name),
            robots="" if self.config.get("site", {}).get("search_engines", True)
            else '\n  <meta name="robots" content="noindex, nofollow">',
            skip=self.t["skip"],
            nav="\n      ".join(nav),
            content=body,
            links=self.links,
            years=years,
            footer_pages=footer_pages,
        )

    # -- writing

    def write_page(self, url: str, title: str, body: str, **kwargs) -> None:
        rel = Path(url.strip("/"), "index.html")
        page = self.frame(url, title, body, **kwargs)
        self.write(rel, relativize(page, "../" * (len(rel.parts) - 1)))

    def write_all(self) -> None:
        OUT.mkdir(exist_ok=True)
        for src in STATIC.rglob("*"):
            if src.is_file():
                self.copy(src, Path("static") / src.relative_to(STATIC))

        mentions: dict[str, list[Doc]] = {}
        for doc in self.posts + self.pages:
            for url in doc.links_to:
                mentions.setdefault(url, []).append(doc)

        intro = None
        for doc in self.pages:
            if doc.url == f"/{self.section}/":
                intro = doc
                continue
            self.write_page(doc.url, doc.title, self.page_body(doc), description=doc.summary)

        for post in self.posts:
            self.write_page(post.url, post.title, self.post_body(post, mentions.get(post.url, [])),
                            description=post.summary, og_type="article")

        self.write_page(f"/{self.section}/", intro.title if intro else self.t["notes"], self.section_body(intro),
                        description=intro.summary if intro else "")
        for tag, posts in self.tags.values():
            body = (f'<article class="page">\n<h1>#{esc(tag)}</h1>\n'
                    f'<p class="back"><a href="/{self.section}/">← {self.t["all_posts"]}</a></p>\n'
                    f'{self.entry_list(posts)}\n</article>')
            self.write_page(self.tag_url(tag), f"#{tag}", body)

        not_found = (f'<article class="page">\n<h1>{self.t["not_found"]}</h1>\n'
                     f'<p>{self.t["not_found_text"]}</p>\n</article>')
        root = urlparse(self.base_url).path.rstrip("/") + "/"  # 404.html is served at any depth
        self.write(Path("404.html"), relativize(self.frame("/404.html", self.t["not_found"], not_found), root))

        self.write_feed()
        self.write_sitemap()
        self.cleanup()

    def write_feed(self) -> None:
        items = []
        for post in [p for p in self.posts if not p.draft][:FEED_POSTS]:
            published = email.utils.format_datetime(dt.datetime.combine(post.updated, dt.time(12), dt.timezone.utc))
            items.append(
                "  <item>\n"
                f"    <title>{esc(post.title)}</title>\n"
                f"    <link>{esc(self.base_url + post.url)}</link>\n"
                f"    <guid>{esc(self.base_url + post.url)}</guid>\n"
                f"    <pubDate>{published}</pubDate>\n"
                f"    <description>{esc(relativize(post.html, self.base_url + '/'))}</description>\n"
                "  </item>"
            )
        self.write(Path("feed.xml"), (
            '<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0">\n<channel>\n'
            f"  <title>{esc(self.name)}</title>\n"
            f"  <link>{esc(self.base_url + '/')}</link>\n"
            f"  <description>{esc(self.description)}</description>\n"
            f"  <language>{esc(self.lang)}</language>\n"
            + "\n".join(items) + "\n</channel>\n</rss>\n"
        ))

    def write_sitemap(self) -> None:
        urls = [d.url for d in self.pages] + [p.url for p in self.posts if not p.draft]
        self.write(Path("sitemap.xml"), (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(f"  <url><loc>{esc(self.base_url + u)}</loc></url>\n" for u in dict.fromkeys(urls))
            + "</urlset>\n"
        ))
        self.write(Path("robots.txt"), f"User-agent: *\nAllow: /\nSitemap: {self.base_url}/sitemap.xml\n")

    def write(self, rel: Path, text: str) -> None:
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8", newline="\n")
        self.written.add(path)

    def copy(self, src: Path, rel: Path) -> None:
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_mtime_ns < src.stat().st_mtime_ns or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        self.written.add(dest)

    def cleanup(self) -> None:
        """Removes files from earlier builds that no longer exist (deleted posts, renamed pages)."""
        for path in sorted(OUT.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file() and path not in self.written:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()


# ---------------------------------------------------------------- commands

def build(drafts: bool = False) -> bool:
    started = time.perf_counter()
    site = Site(drafts)
    try:
        site.build()
    except Exception:
        traceback.print_exc()
        return False
    took = (time.perf_counter() - started) * 1000
    posts = f"{len(site.posts)} post" + ("" if len(site.posts) == 1 else "s")
    print(f"Built {len(site.pages)} pages and {posts} into _site/ in {took:.0f} ms"
          + (" (drafts included)" if drafts else ""), flush=True)
    if site.warnings:
        print("Check before publishing:")
        for warning in site.warnings:
            print(f"  - {warning}")
    return True


class PreviewHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_error(self, code, message=None, explain=None):
        page = OUT / "404.html"
        if code != 404 or not page.exists():
            return super().send_error(code, message, explain)
        body = page.read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def snapshot() -> dict[str, int]:
    files = [ROOT / "site.toml", *CONTENT.rglob("*"), *STATIC.rglob("*"), *TEMPLATES.rglob("*")]
    return {str(p): p.stat().st_mtime_ns for p in files if p.is_file()}


def serve(port: int, drafts: bool) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), PreviewHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\nPreview: http://localhost:{port}/  (rebuilds when you save; Ctrl+C to stop)", flush=True)
    state = snapshot()
    try:
        while True:
            time.sleep(0.5)
            if (current := snapshot()) != state:
                state = current
                build(drafts)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the website into _site/.")
    parser.add_argument("--serve", action="store_true", help="preview on localhost and rebuild on changes")
    parser.add_argument("--drafts", action="store_true", help="include posts marked `draft: true`")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ok = build(args.drafts)
    if args.serve:
        serve(args.port, args.drafts)
    elif not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
