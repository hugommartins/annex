#!/usr/bin/env python3
"""Build the annex: Obsidian book notes (YAML frontmatter) -> static site.

    python build.py --content ~/vault/Books --out dist

A note counts as a book when its `tags` include `book`. Everything else in the
folder is ignored. Frontmatter keys are case-insensitive.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import html
import json
import re
import shutil
import sys
import unicodedata
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    import markdown as md_lib
except ImportError:  # body text falls back to escaped paragraphs
    md_lib = None
try:
    from PIL import Image
except ImportError:  # covers are copied as-is instead of resized to WebP
    Image = None

ROOT = Path(__file__).resolve().parent

BOOK_TAGS = {"book", "ebook"}  # a note counts as a book if `tags` includes either (hyphens/spaces ignored, so 'e-book' matches)
STATUS_MAP = {
    "read": "read", "finished": "read", "done": "read",
    "reading": "reading", "in progress": "reading", "current": "reading",
    "to read": "want", "want to read": "want", "want": "want", "tbr": "want", "unread": "want",
}
STATUS_ORDER = ["reading", "read", "want"]
STATUS_LABEL = {"reading": "reading", "read": "read", "want": "to read"}
KIND_ORDER = ["fiction", "nonfiction"]
KIND_LABEL = {"fiction": "fiction", "nonfiction": "non-fiction"}

COVER_MAX = (320, 480)      # px; ~2x the largest size the site displays
COVER_VERSION = "v1"        # bump to invalidate processed covers after changing COVER_MAX
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

WARNINGS: list[str] = []
FAILED_URLS: set[str] = set()  # downloads that failed earlier in this process; dev.py won't retry them on every rebuild


class BuildError(Exception):
    """A problem the user can fix (bad path, no books). Shown as a message, not a traceback."""


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"  ! {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Frontmatter normalisation
# --------------------------------------------------------------------------- #

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
WIKI_RE = re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
EMBED_RE = re.compile(r"!\[\[[^\]]*\]\]")
DATAVIEW_RE = re.compile(r"^```dataview(?:js)?\b.*?^```[ \t]*$", re.S | re.M)
COMMENT_RE = re.compile(r"%%.*?%%", re.S)
TRANSLIT = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss", "æ": "ae", "œ": "oe"})


def split_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    return (data if isinstance(data, dict) else {}), text[match.end():]


def unwiki(text: str) -> str:
    return WIKI_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)


def flatten(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from flatten(item)
    elif value is not None:
        yield value


def as_list(value) -> list[str]:
    """Scalar or list -> clean list of strings. Unquoted `- [[Name]]` parses as a nested list; flatten handles it."""
    out = []
    for item in flatten(value):
        text = unwiki(str(item)).strip()
        if text:
            out.append(text)
    return out


def as_tags(value) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    return [t.lstrip("#").lower() for t in as_list(value)]


def as_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False
    return None


def exclusion_reason(rel: Path, keys: dict, patterns: list[str]) -> str | None:
    """None if the note should be published; otherwise why it was left out, for --list-skipped."""
    if as_bool(keys.get("publish")) is False:
        return "publish: false"
    if as_bool(keys.get("draft")) is True:
        return "draft: true"
    posix = rel.as_posix()
    for pattern in patterns:
        pattern = pattern.strip().strip("/")
        if pattern and (
            fnmatch.fnmatch(posix, pattern)
            or fnmatch.fnmatch(posix, pattern + "/*")
            or fnmatch.fnmatch(rel.name, pattern)
        ):
            return f"--exclude {pattern!r}"
    return None


def as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    for fmt, length in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            return datetime.strptime(text[:length], fmt).date()
        except ValueError:
            continue
    return None


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(TRANSLIT)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------------------- #
# Book model
# --------------------------------------------------------------------------- #

@dataclass
class Book:
    slug: str
    title: str
    path: Path
    source_hash: str
    authors: list[str]
    types: list[str]
    series: list[str]
    order: int | None
    status: str
    pages: int
    started: date | None
    finished: date | None
    year_field: int | None
    cover_source: str
    body: str
    cover: str = ""
    cover_w: int = 0
    cover_h: int = 0
    body_html: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def primary_series(self) -> str:
        """`Order` is taken to belong to the first entry in `Series`."""
        return self.series[0] if self.series else ""

    @property
    def read_year(self) -> str:
        if self.status != "read":
            return ""
        if self.finished:
            return str(self.finished.year)
        return str(self.year_field) if self.year_field else ""

    @property
    def sort_date(self) -> str:
        d = self.finished or self.started
        return d.isoformat() if d else ""

    def _tokens(self) -> set[str]:
        return {re.sub(r"[\s_-]+", "", t.lower()) for t in (*self.types, *self.tags)}

    @property
    def manga(self) -> bool:
        """Manga has its own collection: 'manga' in Type or tags."""
        return "manga" in self._tokens()

    @property
    def kind(self) -> str:
        """'fiction', 'nonfiction' or '' — read from Type, then tags. Accepts Non-fiction / Non Fiction / nonfiction. Manga has no kind."""
        tokens = self._tokens()
        if "manga" in tokens:
            return ""
        if "nonfiction" in tokens:
            return "nonfiction"
        return "fiction" if "fiction" in tokens else ""

    @property
    def search_text(self) -> str:
        parts = [self.title, *self.authors, *self.series, *self.types]
        return " ".join(parts).lower()


def load_books(content: Path, exclude: list[str] | None = None) -> tuple[list[Book], list[dict]]:
    """Returns (books, skipped). `skipped` lists every .md note in --content left out of the
    site, each as {"path", "reason"} — pass --list-skipped to print it and track down a book
    you expected to see but don't."""
    books: list[Book] = []
    skipped: list[dict] = []
    used: set[str] = set()
    for path in sorted(content.rglob("*.md")):
        rel = path.relative_to(content)
        if any(part.startswith(".") for part in rel.parts):  # .obsidian, .trash
            continue
        try:
            source = path.read_text(encoding="utf-8-sig")
            fm, body = split_frontmatter(source)
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            warn(f"{rel}: unreadable frontmatter ({exc.__class__.__name__}), skipped")
            skipped.append({"path": str(rel), "reason": f"unreadable frontmatter ({exc.__class__.__name__})"})
            continue
        keys = {str(k).lower(): v for k, v in fm.items()}
        tags = as_tags(keys.get("tags"))
        norm_tags = {re.sub(r"[\s_-]+", "", t) for t in tags}
        if not norm_tags & BOOK_TAGS:
            skipped.append({"path": str(rel), "reason": "no 'book' tag" if not tags else f"tags are {tags}, none is 'book' or 'e-book'"})
            continue
        reason = exclusion_reason(rel, keys, exclude or [])
        if reason:
            skipped.append({"path": str(rel), "reason": reason})
            continue

        title = (as_list(keys.get("title")) or [path.stem])[0]
        slug = base = slugify(title) or "book-" + hashlib.sha1(title.encode()).hexdigest()[:6]
        n = 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        if slug != base:
            warn(f"{rel}: duplicate title, using slug '{slug}'")
        used.add(slug)

        raw_status = (as_list(keys.get("status")) or [""])[0]
        status = STATUS_MAP.get(raw_status.lower())
        if status is None:
            warn(f"{rel}: " + (f"unknown Status '{raw_status}'" if raw_status else "no Status set") + ", treated as 'to read'")
            status = "want"

        books.append(Book(
            slug=slug, title=title, path=path,
            source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            authors=as_list(keys.get("author")),
            types=as_list(keys.get("type")),
            series=as_list(keys.get("series")),
            order=as_int(keys.get("order")),
            status=status,
            pages=as_int(keys.get("pages")) or 0,
            started=as_date(keys.get("starteddate")),
            finished=as_date(keys.get("finisheddate")),
            year_field=as_int(keys.get("year")),
            cover_source=(as_list(keys.get("cover")) or [""])[0],
            body=body, tags=tags,
        ))
    return books, skipped


# --------------------------------------------------------------------------- #
# Note body (markdown -> html)
# --------------------------------------------------------------------------- #

def render_body(book: Book, by_title: dict[str, Book]) -> str:
    text = COMMENT_RE.sub("", DATAVIEW_RE.sub("", EMBED_RE.sub("", book.body))).strip()
    if not text:
        return ""

    def link(match: re.Match) -> str:
        target, alias = match.group(1).strip(), match.group(2)
        label = (alias or target).strip()
        other = by_title.get(target.lower())
        return f"[{label}](../{other.slug}/)" if other else label

    text = WIKI_RE.sub(link, text)
    if md_lib is None:
        return "".join(f"<p>{html.escape(p)}</p>" for p in re.split(r"\n{2,}", text))
    extensions, config = ["extra", "sane_lists", "admonition"], {}
    try:
        import pygments  # noqa: F401
        extensions.append("codehilite")
        config["codehilite"] = {"css_class": "highlight", "guess_lang": False}
    except ImportError:
        pass
    return md_lib.markdown(text, extensions=extensions, extension_configs=config)


# --------------------------------------------------------------------------- #
# Covers: download -> resize -> WebP, with a generated fallback
# --------------------------------------------------------------------------- #

PALETTES = [
    ("#1d3557", "#f1e9d2", "#e9c46a"), ("#5b2a3c", "#f6e7d8", "#e07a5f"),
    ("#264653", "#f4ecd8", "#e9c46a"), ("#3d405b", "#f4f1de", "#e07a5f"),
    ("#2f4f3a", "#efe6c8", "#d9a441"), ("#6b2d1a", "#f5e6c8", "#e8b04a"),
    ("#22333b", "#eae0d5", "#c6ac8f"), ("#4a3f6b", "#f2e9e4", "#f2b880"),
    ("#1f3d3a", "#f0ead6", "#e76f51"), ("#7a5c2e", "#f8f0dc", "#3d2b1f"),
]


def wrap_text(text: str, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def generated_cover_svg(book: Book) -> str:
    h = zlib.crc32(book.title.encode())
    bg, fg, ac = PALETTES[h % len(PALETTES)]
    motif = (h >> 4) % 5
    art = [
        f'<circle cx="100" cy="88" r="46" fill="{ac}"/><circle cx="120" cy="76" r="40" fill="{bg}"/>',
        f'<rect y="34" width="200" height="8" fill="{ac}"/><rect y="52" width="200" height="4" fill="{ac}"/><rect y="66" width="200" height="2" fill="{ac}"/>',
        f'<polygon points="0,300 0,232 55,172 105,236 150,196 200,252 200,300" fill="{ac}"/>',
        f'<g fill="none" stroke="{ac}" stroke-width="3"><circle cx="100" cy="88" r="20"/><circle cx="100" cy="88" r="34"/><circle cx="100" cy="88" r="48"/></g>',
        f'<polygon points="0,0 200,0 200,60 0,110" fill="{ac}"/>',
    ][motif]
    y0 = (175, 130, 66, 175, 175)[motif]
    size, lines = 22, wrap_text(book.title, 13)
    if len(lines) > 4:
        size, lines = 17, wrap_text(book.title, 17)
    font = "font-family=\"Georgia,'Times New Roman',serif\""
    title = "".join(
        f'<text x="100" y="{y0 + i * size * 1.2:.1f}" text-anchor="middle" font-size="{size}" '
        f'font-weight="700" fill="{fg}" {font}>{html.escape(line, quote=False)}</text>'
        for i, line in enumerate(lines)
    )
    name = (book.authors[0] if book.authors else "").upper()
    name = name if len(name) <= 24 else name[:23] + "…"
    author_size, author_spacing = (11, 1.5) if len(name) <= 14 else (9, 1) if len(name) <= 18 else (8, 0.8)
    author = html.escape(name, quote=False)
    author_y = 30 if motif == 2 else 278
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300">'
        f'<rect width="200" height="300" fill="{bg}"/>{art}{title}'
        f'<text x="100" y="{author_y}" text-anchor="middle" font-size="{author_size}" letter-spacing="{author_spacing}" '
        f'fill="{fg}" {font}>{author}</text></svg>'
    )


def sniff_extension(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def to_webp(data: bytes) -> tuple[bytes, int, int]:
    img = Image.open(BytesIO(data))
    img.load()
    img = img.convert("RGB")
    img.thumbnail(COVER_MAX, Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, "WEBP", quality=82, method=6)
    return buf.getvalue(), img.width, img.height


class Covers:
    def __init__(self, content: Path, vault: Path, cache: Path, out: Path, offline: bool) -> None:
        self.content, self.vault, self.offline = content, vault, offline
        self._name_index: dict[str, Path] | None = None
        self.raw_dir, self.webp_dir = cache / "raw", cache / "webp"
        self.out_dir = out / "covers"
        for d in (self.raw_dir, self.webp_dir, self.out_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.stats = {"downloaded": 0, "cached": 0, "generated": 0}

    def prepare(self, book: Book) -> None:
        if book.cover_source and self._real(book):
            return
        (self.out_dir / f"{book.slug}.svg").write_text(generated_cover_svg(book), encoding="utf-8")
        book.cover, book.cover_w, book.cover_h = f"covers/{book.slug}.svg", 200, 300
        self.stats["generated"] += 1

    def input_fingerprint(self, book: Book) -> str:
        source = book.cover_source
        local = None if re.match(r"(https?|file)://", source, re.I) else self._local_path(source) if source else None
        if local is not None:
            stat = local.stat()
            source_data = (source, str(local), stat.st_mtime_ns, stat.st_size)
        else:
            source_data = (source, None)
        return stable_hash({
            "source": source_data,
            "fallback": (book.title, book.authors[0] if book.authors else ""),
        })

    # -- real covers -------------------------------------------------------- #

    def _real(self, book: Book) -> bool:
        source = book.cover_source
        remote = re.match(r"(https?|file)://", source, re.I) is not None
        local = None if remote else self._local_path(source)
        if not remote and local is None:
            warn(f"{book.path.name}: cover '{source}' not found, using generated cover")
            return False

        stamp = source if remote else f"{local}:{local.stat().st_mtime_ns}"
        key = hashlib.sha1(stamp.encode()).hexdigest()[:16]
        cached_webp = self.webp_dir / f"{key}-{COVER_VERSION}.webp"

        if Image is not None and cached_webp.exists():
            dest = self.out_dir / f"{book.slug}.webp"
            shutil.copyfile(cached_webp, dest)
            with Image.open(dest) as img:
                book.cover, book.cover_w, book.cover_h = f"covers/{dest.name}", img.width, img.height
            self.stats["cached"] += 1
            return True

        raw_path = self.raw_dir / key
        fetched = False
        if local is not None:
            data = local.read_bytes()
        elif raw_path.exists():
            data = raw_path.read_bytes()
            self.stats["cached"] += 1
        elif self.offline:
            warn(f"{book.path.name}: cover not cached and --offline set, using generated cover")
            return False
        else:
            data = self._download(source, book)
            if data is None:
                return False
            raw_path.write_bytes(data)
            fetched = True

        try:
            if Image is not None:
                payload, w, h = to_webp(data)
                ext = "webp"
                cached_webp.write_bytes(payload)
            else:
                ext, payload, w, h = sniff_extension(data), data, 0, 0
                if ext is None:
                    raise ValueError("not an image")
        except Exception as exc:  # noqa: BLE001 - any decode failure means "not a usable image"
            warn(f"{book.path.name}: cover is not a valid image ({exc}), using generated cover")
            raw_path.unlink(missing_ok=True)
            return False

        if fetched:
            self.stats["downloaded"] += 1
        dest = self.out_dir / f"{book.slug}.{ext}"
        dest.write_bytes(payload)
        book.cover, book.cover_w, book.cover_h = f"covers/{dest.name}", w, h
        return True

    def _local_path(self, source: str) -> Path | None:
        """Explicit path first; otherwise resolve by file name anywhere in the vault, like Obsidian does."""
        name = unwiki(source).strip()
        for base in (self.vault, self.content):
            candidate = (base / name).expanduser()
            if candidate.is_file():
                return candidate
        candidate = Path(name).expanduser()
        if candidate.is_file():
            return candidate
        if self._name_index is None:
            self._name_index = {
                p.name: p for p in sorted(self.vault.rglob("*"))
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
                and not any(part.startswith(".") for part in p.relative_to(self.vault).parts)
            }
        return self._name_index.get(Path(name).name)

    @staticmethod
    def _download(url: str, book: Book) -> bytes | None:
        # Keep '+' and existing %-escapes intact; only encode spaces / non-ASCII.
        if url in FAILED_URLS:
            warn(f"{book.path.name}: cover download failed earlier, not retrying (restart to retry)")
            return None
        safe_url = quote(url, safe=":/?#[]@!$&'()*+,;=%~._-")
        request = Request(safe_url, headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
        try:
            with urlopen(request, timeout=25) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - network errors vary widely
            FAILED_URLS.add(url)
            warn(f"{book.path.name}: cover download failed ({exc}), using generated cover")
            return None


# --------------------------------------------------------------------------- #
# Grouping for the templates
# --------------------------------------------------------------------------- #

def by_recent(books: list[Book]) -> list[Book]:
    books = sorted(books, key=lambda b: b.title.lower())
    return sorted(books, key=lambda b: b.sort_date, reverse=True)  # stable: ties stay A-Z


def by_order(books: list[Book]) -> list[Book]:
    return sorted(books, key=lambda b: (b.order if b.order is not None else 10**6, b.title.lower()))


def fmt_pages(books: list[Book]) -> str:
    return f"{sum(b.pages for b in books):,}"


def cover_groups(books: list[Book], noun: str = "books") -> list[dict]:
    groups = []
    reading = by_recent([b for b in books if b.status == "reading"])
    if reading:
        groups.append({"title": "reading now", "meta": f"{len(reading)} in progress", "books": reading})
    read = [b for b in books if b.status == "read"]
    years = sorted({b.read_year for b in read if b.read_year}, reverse=True)
    for year in years:
        items = by_recent([b for b in read if b.read_year == year])
        groups.append({"title": f"read in {year}", "meta": f"{len(items)} {noun} · {fmt_pages(items)} pages", "books": items})
    undated = by_recent([b for b in read if not b.read_year])
    if undated:
        groups.append({"title": "read, undated", "meta": f"{len(undated)} {noun}", "books": undated})
    want = sorted((b for b in books if b.status == "want"), key=lambda b: b.title.lower())
    if want:
        groups.append({"title": "to read", "meta": f"{len(want)} waiting", "books": want})
    return groups


def series_groups(books: list[Book], progress: dict[str, dict], noun: str = "books") -> list[dict]:
    """One group per primary series (`Series[0]`), in `Order`. When a series has sub-series —
    a second `Series` entry, like Discworld's 'Rincewind Novels' — books nest under those
    instead of listing flat; `subgroups` holds each sub-series (ordered by where it starts in
    the parent series) and `loose` holds books in the parent series with no sub-series of their
    own. Nesting only kicks in when it would actually group something: a single sub-series
    covering the whole shelf isn't worth a subheading."""
    buckets: dict[str, list[Book]] = {}
    for b in books:
        buckets.setdefault(b.primary_series, []).append(b)
    named = sorted(((k, v) for k, v in buckets.items() if k), key=lambda kv: (-len(kv[1]), kv[0].lower()))

    groups = []
    for name, items in named:
        ordered = by_order(items)
        subs: dict[str, list[Book]] = {}
        loose: list[Book] = []
        for b in ordered:
            sub = b.series[1] if len(b.series) > 1 else None
            (subs.setdefault(sub, []) if sub else loose).append(b)
        subgroups = None
        if len(subs) > 1 or (len(subs) == 1 and loose):
            first_order = lambda kv: min((b.order for b in kv[1] if b.order is not None), default=10**6)  # noqa: E731
            subgroups = [{"name": sub_name, "books": sub_items} for sub_name, sub_items in sorted(subs.items(), key=first_order)]
        groups.append({
            "title": name, "numbered": True, "books": ordered,
            "subgroups": subgroups, "loose": loose if subgroups else [],
            "progress": progress[name],
        })

    return groups


def load_series_totals(path: Path, books: list[Book]) -> dict[str, int]:
    """series.yml maps a series name to how many books it has in total, e.g. `Discworld: 41`."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as exc:
        warn(f"{path.name}: unreadable ({exc.__class__.__name__}), series totals ignored")
        return {}
    if not isinstance(data, dict):
        warn(f"{path.name}: expected `Series name: total` pairs, series totals ignored")
        return {}
    known = {name.lower() for b in books for name in b.series}
    totals: dict[str, int] = {}
    for name, value in data.items():
        key, total = unwiki(str(name)).strip().lower(), as_int(value)
        if total is None or total < 1:
            warn(f"{path.name}: '{name}' needs a positive number, skipped")
            continue
        if key not in known:
            warn(f"{path.name}: '{name}' matches no series in your books (typo?)")
        totals[key] = total
    return totals


def series_progress(books: list[Book], totals: dict[str, int]) -> dict[str, dict]:
    """Per-series progress. Counts use every series a book lists; 'up next' uses Order, which belongs to Series[0]."""
    result: dict[str, dict] = {}
    for name in sorted({n for b in books for n in b.series}, key=str.lower):
        members = [b for b in books if name in b.series]
        read = sum(b.status == "read" for b in members)
        reading = sum(b.status == "reading" for b in members)
        want = len(members) - read - reading
        total = totals.get(name.lower())
        if total is not None and total < len(members):
            warn(f"series.yml: '{name}' total ({total}) is lower than the {len(members)} books in your library")
        denom = max(total or 0, len(members))

        # "Up next" continues from the furthest point you've reached (highest Order read or reading),
        # so owning an early book you skipped doesn't pin the suggestion to it.
        ordered = by_order([b for b in members if b.primary_series == name and b.order is not None])
        reached = max((b.order for b in ordered if b.status in ("read", "reading")), default=0)
        unread = [b for b in ordered if b.status == "want"]
        after = [b for b in unread if b.order > reached]
        earlier = [b for b in unread if b.order < reached]
        up_next = after[0] if after else None
        missing = reached + 1 if (up_next is None and total and 0 < reached < total) else None

        pct = lambda n: round(n / denom * 100, 2)  # noqa: E731
        result[name] = {
            "name": name, "total": total, "in_library": len(members),
            "read": read, "reading": reading, "want": want,
            "label": f"{read} of {denom} read",
            "read_pct": pct(read), "reading_pct": pct(reading), "want_pct": pct(want),
            "up_next": up_next, "missing": missing,
            "earlier": earlier[0] if earlier else None, "earlier_count": len(earlier),
        }
    return result


def year_stats(books: list[Book]) -> dict | None:
    """Books and pages finished per year, oldest to newest, including empty years in between.

    `books_p` / `pages_p` are 0-1 fractions of the busiest year, used for bar heights."""
    read = [b for b in books if b.status == "read"]
    dated = [b for b in read if b.read_year]
    if not dated:
        return None
    per_year: dict[int, dict[str, int]] = {}
    for b in dated:
        row = per_year.setdefault(int(b.read_year), {"books": 0, "pages": 0})
        row["books"] += 1
        row["pages"] += b.pages
    most_books = max(r["books"] for r in per_year.values())
    most_pages = max(r["pages"] for r in per_year.values())
    rows = []
    for year in range(min(per_year), max(per_year) + 1):
        r = per_year.get(year, {"books": 0, "pages": 0})
        rows.append({
            "year": year, "books": r["books"], "pages": r["pages"],
            "books_p": round(r["books"] / most_books, 3),
            "pages_p": round(r["pages"] / most_pages, 3) if most_pages else 0,
        })
    return {
        "rows": rows, "current": date.today().year, "undated": len(read) - len(dated),
        "first": rows[0]["year"], "last": rows[-1]["year"],
        "total_books": len(dated), "total_pages": sum(b.pages for b in dated),
        "max_books": most_books, "max_pages": most_pages,
    }


def sidebar_filters(books: list[Book]) -> dict[str, list[tuple[str, str, int]]]:
    def tally(values_of) -> dict[str, int]:
        counts: dict[str, int] = {}
        for b in books:
            for v in values_of(b):
                counts[v] = counts.get(v, 0) + 1
        return counts

    status = tally(lambda b: [b.status])
    year = tally(lambda b: [b.read_year] if b.read_year else [])
    types = tally(lambda b: b.types)
    series = tally(lambda b: b.series)
    kind = tally(lambda b: [b.kind] if b.kind else [])
    az = lambda counts: sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))  # noqa: E731
    return {
        "kind": [(k, KIND_LABEL[k], kind[k]) for k in KIND_ORDER if k in kind],
        "status": [(s, STATUS_LABEL[s], status[s]) for s in STATUS_ORDER if s in status],
        "year": [(y, y, n) for y, n in sorted(year.items(), reverse=True)],
        "type": [(t, t, n) for t, n in az(types)],
        "series": [(s, s, n) for s, n in az(series)],
    }


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def asset_version() -> str:
    digest = hashlib.sha1()
    for path in sorted((ROOT / "static").rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:8]


def clean_output(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        if child.name in {".git", "CNAME"}:
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()


def stable_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_state_path(cache: Path, out: Path) -> Path:
    key = hashlib.sha256(str(out).encode("utf-8")).hexdigest()[:16]
    return cache / "build-state" / f"{key}.json"


def read_build_state(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        warn(f"incremental build state is unreadable ({exc}), rebuilding all book pages")
        return None
    if not isinstance(state, dict) or not isinstance(state.get("books"), dict):
        warn("incremental build state has an invalid format, rebuilding all book pages")
        return None
    return state


def book_page_data(book: Book) -> dict:
    return {
        "slug": book.slug, "title": book.title, "authors": book.authors,
        "types": book.types, "series": book.series, "order": book.order,
        "status": book.status, "pages": book.pages,
        "cover": book.cover, "cover_w": book.cover_w, "cover_h": book.cover_h,
        "kind": book.kind, "read_year": book.read_year, "sort_date": book.sort_date,
        "search_text": book.search_text,
    }


def group_page_data(group: dict | None) -> dict | None:
    if group is None:
        return None

    def book_list(items: list[Book]) -> list[dict]:
        return [book_page_data(book) for book in items]

    progress = group["progress"]
    return {
        "title": group["title"],
        "numbered": group["numbered"],
        "books": book_list(group["books"]),
        "subgroups": [
            {"name": subgroup["name"], "books": book_list(subgroup["books"])}
            for subgroup in group["subgroups"] or []
        ],
        "loose": book_list(group["loose"]),
        "progress": {
            key: book_page_data(value) if isinstance(value, Book) else value
            for key, value in progress.items()
        } if progress else None,
    }


def book_page_fingerprint(
    book: Book,
    by_title: dict[str, Book],
    group: dict | None,
    more_by: list[Book],
    shared: str,
    root: str,
) -> str:
    links = []
    for match in WIKI_RE.finditer(book.body):
        target = match.group(1).strip()
        linked = by_title.get(target.lower())
        links.append((target.lower(), linked.slug if linked else None))
    return stable_hash({
        "shared": shared,
        "source": book.source_hash,
        "cover": (book.cover, book.cover_w, book.cover_h),
        "links": links,
        "group": group_page_data(group),
        "more_by": [(other.slug, other.title) for other in more_by],
        "root": root,
    })


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--content", type=Path, default=ROOT / "content", help="folder with book .md files (searched recursively)")
    parser.add_argument("--vault", type=Path, default=None, help="vault root for resolving local cover file names (default: --content)")
    parser.add_argument("--series", type=Path, default=ROOT / "series.yml", help="optional YAML of series totals (`Discworld: 41`)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache", help="download/resize cache")
    parser.add_argument("--title", default="annex", help="site title shown in the top bar")
    parser.add_argument("--offline", action="store_true", help="never download; use cached covers only")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                        help="skip notes matching a path/file glob or folder, e.g. --exclude 'Private' --exclude '*draft*' (repeatable)")
    parser.add_argument("--strict", action="store_true", help="exit non-zero if any warning was raised")
    parser.add_argument("--no-incremental", action="store_true", help="render every book page, ignoring incremental build state")
    parser.add_argument("--list-skipped", action="store_true",
                        help="print every .md note left out of the site and why (missing 'book' tag, excluded, unreadable)")
    return parser


def check_project_files() -> None:
    """Fail early, with a clear message, when a template or asset is missing or holds the wrong file's contents."""
    expectations = {
        "templates/base.html": "<!doctype html",
        "templates/_macros.html": "{% macro",
        "templates/library.html": '{% extends "base.html" %}',
        "templates/book.html": '{% extends "base.html" %}',
        "templates/stats.html": '{% extends "base.html" %}',
        "static/style.css": ":root",
        "static/app.js": "(function",
    }
    problems = []
    for name, marker in expectations.items():
        path = ROOT / name
        if not path.is_file():
            problems.append(f"{name} is missing")
        elif marker not in path.read_text(encoding="utf-8", errors="replace").lower():
            problems.append(f"{name} should contain {marker!r} but doesn't; it may hold a different file's contents")
    if problems:
        raise BuildError("Project files look wrong:\n  - " + "\n  - ".join(problems))


def build_site(args: argparse.Namespace) -> dict:
    """Build the site into a staging folder, then swap it into --out."""
    WARNINGS.clear()
    check_project_files()
    content = args.content.expanduser().resolve()
    if not content.is_dir():
        raise BuildError(f"content folder not found: {content}")

    print(f"Reading {content}")
    books, skipped = load_books(content, args.exclude)
    if skipped:
        not_tagged = sum(1 for s in skipped if "tag" in s["reason"])
        unreadable = sum(1 for s in skipped if "unreadable" in s["reason"])
        excluded = len(skipped) - not_tagged - unreadable
        parts = [f"{n} {label}" for n, label in
                 [(not_tagged, "not tagged 'book'"), (excluded, "excluded"), (unreadable, "unreadable")] if n]
        print(f"  {len(books)} books, " + ", ".join(parts) + ("" if args.list_skipped else " (pass --list-skipped to see file names)"))
        if args.list_skipped:
            for item in skipped:
                print(f"    skip  {item['path']}  ({item['reason']})")
    if not books:
        raise BuildError("No notes tagged 'book' found.")

    unsorted = [b.title for b in books if not b.manga and not b.kind]
    if unsorted:
        shown = ", ".join(unsorted[:5]) + (f" (+{len(unsorted) - 5} more)" if len(unsorted) > 5 else "")
        warn(f"{len(unsorted)} book(s) have neither Fiction nor Nonfiction in Type, so the kind filter skips them: {shown}")

    by_title = {b.title.lower(): b for b in books}
    final = args.out.expanduser().resolve()
    cache = args.cache.expanduser().resolve()
    state_path = build_state_path(cache, final)
    old_state = read_build_state(state_path)

    template_files = sorted((ROOT / "templates").rglob("*"))
    template_hash = stable_hash([
        (path.relative_to(ROOT).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in template_files if path.is_file()
    ])
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    asset_v = asset_version()
    main_books = [b for b in books if not b.manga]
    manga_books = [b for b in books if b.manga]
    shared_fingerprint = stable_hash({
        "code": code_hash, "templates": template_hash, "assets": asset_v,
        "title": args.title, "has_manga": bool(manga_books),
    })
    incremental = (
        not getattr(args, "no_incremental", False)
        and final.is_dir()
        and old_state is not None
        and old_state.get("version") == 1
        and old_state.get("shared") == shared_fingerprint
    )

    stage = final.parent / f".{final.name}.build"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        if incremental:
            known_outputs = {"index.html", "search.json", "static", "covers", "books", "stats", "manga"}
            for child in final.iterdir():
                if child.name not in known_outputs:
                    continue
                dest = stage / child.name
                if child.is_dir():
                    shutil.copytree(child, dest)
                else:
                    shutil.copy2(child, dest)
        else:
            (stage / "books").mkdir()

        vault = args.vault.expanduser().resolve() if args.vault else content
        covers = Covers(content, vault, cache, stage, args.offline)
        previous_books = old_state.get("books", {}) if incremental else {}
        for book in books:
            previous = previous_books.get(book.slug, {})
            cover_input = covers.input_fingerprint(book)
            previous_cover = previous.get("cover") if isinstance(previous, dict) else None
            if (
                incremental
                and isinstance(previous, dict)
                and previous.get("cover_input") == cover_input
                and isinstance(previous_cover, str)
                and Path(previous_cover).parts[:1] == ("covers",)
                and (stage / previous_cover).is_file()
            ):
                book.cover = previous_cover
                book.cover_w = previous.get("cover_w", 0)
                book.cover_h = previous.get("cover_h", 0)
            else:
                covers.prepare(book)

        shutil.rmtree(stage / "static", ignore_errors=True)
        shutil.copytree(ROOT / "static", stage / "static", dirs_exist_ok=True)
        progress = series_progress(books, load_series_totals(args.series.expanduser().resolve(), books))

        env = Environment(
            loader=FileSystemLoader(ROOT / "templates"),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True, lstrip_blocks=True,
        )
        env.filters["num"] = lambda n: f"{n:,}"
        env.filters["iso"] = lambda d: d.isoformat() if d else "—"
        shared = {
            "site": {"title": args.title},
            "asset_v": asset_v,
            "status_label": STATUS_LABEL,
            "built": date.today().isoformat(),
        }

        pools = {"books": main_books, "manga": manga_books}
        sidebars = {name: sidebar_filters(items) for name, items in pools.items()}

        def render(template: str, dest: Path, root: str, collection: str, **ctx) -> None:
            manga = collection == "manga"
            context = {
                **shared, **ctx,
                "root": root, "collection": collection, "has_manga": bool(manga_books),
                "home": root + ("manga/" if manga else ""),
                "stats_url": root + ("manga/stats/" if manga else "stats/"),
                "filters": sidebars[collection], "noun": "volumes" if manga else "books",
            }
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(env.get_template(template).render(**context), encoding="utf-8")

        # One series_groups() per collection — reused for both the library's series view
        # and each book's own page, so a book's series section is never built twice.
        collection_groups = {
            "books": {g["title"]: g for g in series_groups(main_books, progress, "books")},
            "manga": {g["title"]: g for g in series_groups(manga_books, progress, "volumes")} if manga_books else {},
        }

        def library(collection: str, items: list[Book], dest: Path, root: str) -> None:
            noun = "volumes" if collection == "manga" else "books"
            read = [b for b in items if b.status == "read"]
            render(
                "library.html", dest, root, collection,
                books=items, cover_groups=cover_groups(items, noun),
                series_groups=list(collection_groups[collection].values()),
                stats={"books": len(items), "read": len(read), "pages": sum(b.pages for b in read)},
            )

        library("books", main_books, stage / "index.html", "")
        if manga_books:
            library("manga", manga_books, stage / "manga" / "index.html", "../")

        current_slugs = {book.slug for book in books}
        for old_slug, old in previous_books.items():
            if old_slug not in current_slugs and re.fullmatch(r"[a-z0-9-]+", old_slug):
                shutil.rmtree(stage / "books" / old_slug, ignore_errors=True)
                old_cover = old.get("cover") if isinstance(old, dict) else None
                if isinstance(old_cover, str) and Path(old_cover).parts[:1] == ("covers",):
                    (stage / old_cover).unlink(missing_ok=True)

        rendered_books = 0
        current_state = {}
        for book in books:
            pool = manga_books if book.manga else main_books
            groups = collection_groups["manga" if book.manga else "books"]
            group = groups.get(book.primary_series)
            blocks = [group] if group and (len(group["books"]) > 1 or (group["progress"] and group["progress"]["total"])) else []
            shown = {book.slug} | ({b.slug for b in group["books"]} if blocks else set())
            more = [b for b in pool if b.slug not in shown and set(b.authors) & set(book.authors)]
            root = "../../"
            fingerprint = book_page_fingerprint(
                book, by_title, blocks[0] if blocks else None, more, shared_fingerprint, root,
            )
            previous = previous_books.get(book.slug, {})
            cover_path = book.cover
            if cover_path and isinstance(previous, dict) and previous.get("cover") != cover_path:
                old_cover = previous.get("cover")
                if isinstance(old_cover, str) and Path(old_cover).parts[:1] == ("covers",):
                    (stage / old_cover).unlink(missing_ok=True)
            current_state[book.slug] = {
                "fingerprint": fingerprint, "cover": cover_path,
                "cover_input": covers.input_fingerprint(book),
                "cover_w": book.cover_w, "cover_h": book.cover_h,
            }
            page_path = stage / "books" / book.slug / "index.html"
            if (
                incremental
                and isinstance(previous, dict)
                and previous.get("fingerprint") == fingerprint
                and page_path.is_file()
            ):
                continue
            book.body_html = render_body(book, by_title)
            render(
                "book.html", page_path, root,
                "manga" if book.manga else "books",
                b=book, series_blocks=blocks, more_by=more,
            )
            rendered_books += 1

        render("stats.html", stage / "stats" / "index.html", "../", "books", year_stats=year_stats(main_books))
        if manga_books:
            render("stats.html", stage / "manga" / "stats" / "index.html", "../../", "manga", year_stats=year_stats(manga_books))
        else:
            shutil.rmtree(stage / "manga", ignore_errors=True)

        # Lets the sidebar search find books from any page (see static/app.js). `m` marks manga.
        index = [{"t": b.title, "a": ", ".join(b.authors), "s": b.slug, "k": b.search_text, "m": int(b.manga)}
                 for b in sorted(books, key=lambda b: b.title.lower())]
        (stage / "search.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        clean_output(final)
        for child in stage.iterdir():
            shutil.move(str(child), final / child.name)

        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_tmp = state_path.with_suffix(".tmp")
        state_tmp.write_text(json.dumps({
            "version": 1, "shared": shared_fingerprint, "books": current_state,
        }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        state_tmp.replace(state_path)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    s = covers.stats
    print(
        f"Built {len(books)} books ({sum(b.manga for b in books)} manga) -> {final}\n"
        f"  book pages: {rendered_books} rendered, {len(books) - rendered_books} unchanged\n"
        f"  covers: {s['downloaded']} downloaded, {s['cached']} from cache, {s['generated']} generated"
        + (f"\n  {len(WARNINGS)} warning(s)" if WARNINGS else "")
    )
    return {
        "books": len(books), "skipped": len(skipped), "warnings": len(WARNINGS),
        "rendered_books": rendered_books, "unchanged_books": len(books) - rendered_books,
    }


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        result = build_site(args)
    except BuildError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 1 if (args.strict and result["warnings"]) else 0


if __name__ == "__main__":
    sys.exit(main())