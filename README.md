# annex

Obsidian book notes -> static site (Jinja2). Same paper/moss palette as the digital garden, built as its own thing: bigger covers, a serif display face for titles and section headings, and a page that scrolls normally instead of sitting in a boxed app pane.

```
pip install -r requirements.txt

python dev.py   --content ~/vault/Books --vault ~/vault     # build, serve, live reload
python build.py --content ~/vault/Books --vault ~/vault --out dist   # production build
```

`--vault` (optional) is where local cover file names like `![[cover.jpg]]` are looked up. Defaults to `--content`.

## Development

`dev.py` builds the site, serves it at http://localhost:8000, and rebuilds when a note, template, stylesheet, script, `series.yml`, or `build.py` changes. The browser reloads itself after each rebuild. It takes the same options as `build.py`, plus:

- `--port N`: port to serve on (uses the next free one if busy)
- `--host 0.0.0.0`: also serve on your network, so you can check the site on your phone
- `--open`: open the browser on start

If a rebuild fails, the last good build keeps being served and the error appears at the bottom of the page. `dev.py` never writes its reload script into `dist/`, so `dist/` is always production-clean.

`build.py` builds into a staging folder and replaces `dist/` only when the build succeeds, so a failed build never leaves a half-empty `dist/`.

Builds reuse unchanged book pages from the previous output. A changed note rebuilds its page and any pages affected by its links, series shelf, or author recommendations; shared library, stats, and search files are refreshed each time. Incremental state lives in `.cache/`, outside the published site. Use `--no-incremental` to force every book page to be rendered.

## Frontmatter

A note is a book if `tags` contains `book` or `ebook` (`e-book` also matches). Keys are case-insensitive. Title = file name (or `title:`).

| key | notes |
|---|---|
| `Cover` | `https://…` URL, `file://…`, or a file in the vault. Downloaded once, resized to WebP, cached in `.cache/`. Missing or broken -> generated cover |
| `Author`, `Type`, `Series` | lists; `[[wikilinks]]` are stripped |
| `Order` | position in `Series[0]` |
| `Status` | `Read`, `Reading`, `To Read` |
| `Pages`, `Year` | numbers or numeric strings |
| `StartedDate`, `FinishedDate` | `YYYY-MM-DD` |

The note body is rendered as markdown on the book page. `[[Other Book]]` links between books; `dataview` blocks and `%%comments%%` are stripped.

## Manga

A note is manga when `Type` or `tags` contains `manga`. Manga lives in its own collection, so it never appears on the main page:

- `/manga/` is a full library of its own (shelf, series, filters, search), with volumes instead of books
- `/manga/stats/` has its own stats
- the books / manga switch in the top bar moves between the two; it only appears when you have manga
- series progress, "up next", search and "more by" stay inside the collection, and `series.yml` works for manga series too (`Vagabond: 37`)

## Fiction / non-fiction

On the books page, a "kind" filter pill splits fiction from non-fiction. It reads `Type` (falling back to `tags`) and accepts `Fiction`, `Nonfiction`, `Non-fiction` and `Non Fiction`. Books with neither are still listed but skipped by the filter, and the build warns about them. Manga has no kind.

## Search

The search box lives in the top bar, on every page. On a library page it filters the shelf and series views live. On book and stats pages it opens a dropdown of matches from `search.json`, within the same collection; Enter or "open in library" continues the search on the library page.

## Views

Two ways to browse: **shelf** (grouped by status/year) and **series** (grouped and nested by series — see below). There's no separate index/list view anymore; search and the filter pills narrow the shelf down when you're after one specific book.

## Filters

There's no sidebar. On a library page, filter pills for kind and status sit under the shelf/series toggle; click one to narrow the shelf, click "clear" to reset. "Year read", "type" and "series" filters exist but aren't shown as pills — add a loop for one in `templates/library.html` (there's a comment there showing how) to turn it into pills too. Their `?year=`, `?type=` and `?series=` URLs already work without that — the stats page links to `?year=`, and series/type chips on book pages already use `?series=`/`?type=`.

## Why is a book missing?

The build always prints a one-line breakdown, e.g. `15 books, 1 not tagged 'book', 2 excluded, 1 unreadable`. Add `--list-skipped` to print every left-out note and why:

```
python build.py --list-skipped
    skip  Reading goals.md  (tags are ['planning'], none is 'book')
    skip  Some Note.md  (draft: true)
```

The most common reasons, in order of how often they bite:

- **It's manga.** A note with `manga` in `Type` or `tags` goes to `/manga/`, not the main page — see below.
- **No `book` tag**, or a typo like `Book` in a language YAML lowercases differently, or `tags:` written as a single unquoted string.
- **`publish: false` or `draft: true`** in the frontmatter, or a folder/name matched by `--exclude`.
- **Frontmatter that fails to parse** — an unquoted colon or apostrophe in a title, author, or series name is the usual cause (e.g. `Series: Kingkiller: Chronicle` needs quotes: `Series: "Kingkiller: Chronicle"`). These show up as warnings during the build even without `--list-skipped`.

## Excluding notes

Three ways to keep a book note out of the site:

- `publish: false` in its frontmatter
- `draft: true` in its frontmatter
- `--exclude PATTERN` (repeatable): a folder (`--exclude Private`), a file name glob (`--exclude "*draft*"`), or a path glob (`--exclude "Archive/*.md"`)

Excluded notes don't get a page, cover, or entry in the filter counts, series progress, or stats. A published note that `[[links]]` to an excluded book keeps the text but loses the link. Each build starts from an empty `dist/`, so a note you exclude later disappears from the next build. Notes with unreadable frontmatter are skipped too.

## Series progress

`series.yml` (next to `build.py`) gives each series its total, so progress reads "4 of 41 read":

```yaml
Discworld: 41
Dune Chronicles: 6
```

Series not listed still show progress against the books in your library. "Up next" continues from the furthest point you've reached: the first unread book you own after your highest `Order` read or reading. If you own none, it names the next number in the series ("not in library"). Unread books you own before that point are listed as "earlier unread". Typos in `series.yml` are reported as warnings.

When a series has sub-series — a book's `Series` list has a second entry, like Discworld's `[Discworld, Rincewind Novels]` — the series view and each book's page nest books under those sub-series headings instead of listing the whole series flat. Sub-series are ordered by where they first appear in the parent series (Rincewind Novels before Witches Novels, and so on), and a Discworld book with no second `Series` entry falls into an "other Discworld" group. A series with only one sub-series covering every book in it — or none at all, like Dune Chronicles — stays a single flat shelf, since nesting one group into itself adds nothing.

## Stats

`/stats/` (and `/manga/stats/`) shows books and pages finished per year, linked from the top bar. Each year is a column; the chart opens on the newest year and scrolls sideways, so it stays readable with decades of history. Years come from `FinishedDate`, falling back to `Year`. Click a year to see that year's books.

## Output

`dist/index.html`, `dist/stats/`, `dist/manga/` (with its own `stats/`), `dist/search.json`, `dist/books/<slug>/index.html`, `dist/covers/`, `dist/static/`. Paths are relative, so the site works under any subpath (serve it over HTTP; `file://` doesn't resolve folder URLs). Filters and views live in the URL, e.g. `/?series=Discworld&view=series`.

Options: `--exclude`, `--series` (path to series totals), `--list-skipped`, `--offline` (cache only), `--strict` (fail on warnings), `--title`, `--cache`, `--no-incremental` (force all book pages to render).

## Design

There's no sidebar — the page is just `.topbar` (wordmark, collections switch, search, stats link, theme toggle) and `.main` (whatever the page renders), with the top bar sticky and everything else scrolling normally. Colors and fonts are tokens at the top of `static/style.css`. Book titles, the site title, and section headings ("Discworld", "read in 2025"...) are set in a serif display face (Fraunces); everything else — labels, dates, counts, tags, buttons — stays in the garden's monospace. The favicon is an inline SVG in `templates/base.html`; swap the `<link rel="icon">` there to change it.