# Personal website

A plain, fast personal site: Home, About, Projects, a small wiki ("How systems interact") and Now. It uses no cookies and no third-party requests; the only script is the self-hosted `static/graph.js`.
Pages and wiki entries are Markdown files. Entries can be written in Obsidian and copied in **one at a time**.

**Wiki:** entries live at `/wiki/<entry>/`. The index groups them by topic (an entry's first tag) and lists them alphabetically. Entries show "Updated <date>" (`updated:` in the frontmatter, otherwise `date:`).

**Graph:** like Obsidian's graph view, the wiki page shows every entry as a node, linked by `[[wikilinks]]` and tags. Each entry also shows its own local graph under "Connections". Nodes can be dragged, hovering highlights their neighbours, and clicking opens the entry. The graph appears once at least two entries are published.

**Search engines:** `search_engines = false` in `site.toml` asks Google & co. not to list the site. Set it to `true` when the site is final.

## Preview

```
uv run build.py --serve --drafts
```

Open http://localhost:8000. The site rebuilds whenever you save a file; refresh the browser to see the change.
Every build ends with a **Check before publishing** list: leftover `TODO`s, links to notes that aren't published, long quotes, missing images.

## Make it yours

| What | Where |
| --- | --- |
| Name, tagline, address, footer links | `site.toml` |
| Home page text | `content/index.md` |
| About, Projects, Now | `content/about.md`, `projects.md`, `now.md` |
| Intro of the wiki | `content/wiki.md` |
| Impressum & Datenschutz | `content/imprint.md` |
| Portrait | `static/portrait.jpg`, square and at least 240 × 240 px |
| PDFs, favicon | `static/` |
| Colours and fonts | top of `static/style.css` |

Any page with `nav: <number>` in its frontmatter appears in the menu. A new file such as `content/talks.md` becomes a new page at `/talks/`.

## Publishing a note from Obsidian

Nothing is ever read from your vault. A note goes online only when you copy it into this folder yourself.

1. Copy the note into **`content/drafts/`**. In Obsidian: right-click the note → *Reveal in system explorer*, then copy the file.
   This folder is never uploaded, and its posts only appear in the `--drafts` preview.
2. Copy any images it uses into `content/posts/assets/`.
3. Rewrite it for the web and add frontmatter:
   ```yaml
   ---
   date: 2026-09-18
   description: One sentence for the wiki index and link previews.
   tags: [human-robot-interaction, prediction]
   ---
   ```
   The title is the file name unless you set `title:`. The first tag is the entry's topic. When you revise an entry later, add `updated: <date>`.
4. When it's ready, move it to **`content/posts/`**, preview once more, work through the check list, then commit and push.

**Before you publish, check:**
- **Quotes and images from books, papers or other people.** Short quotes with a source are fine; long passages and copied figures are not. The build flags any quote longer than 80 words.
- **Links to other notes.** A `[[link]]` to a note that isn't published becomes plain text, and the build names it so you can reword it.
- **Private asides.** `%% comments %%` and `<!-- comments -->` are removed from the website. If the repository is public, the Markdown files themselves can still be read on GitHub, so delete private asides before you commit.

### What Obsidian syntax becomes

| In the note | On the site |
| --- | --- |
| `[[Other note]]`, `[[Other note\|text]]`, `[[Note#Heading]]` | a link, if that note is also published |
| `![[figure.png]]`, `![[figure.png\|400]]` | the image, 400 px wide |
| `> [!tip] Title`, `> [!note]-` (foldable) | a callout |
| `$x^2$`, `$$ … $$` | math, rendered as MathML at build time |
| `==highlight==`, `#tag`, footnotes, tables, `- [ ]` tasks | same as in Obsidian |
| `![[Another note]]` (a whole embedded note) | a link, because notes can't be embedded |

`content/drafts/Formatting reference.md` shows all of these. `content/posts/_template.md` is a blank post to copy.

## Putting it online (GitHub Pages)

The site is set up for **https://322simon.github.io**.

1. Create a repository on GitHub named exactly **`322Simon.github.io`**.
   - **Private (recommended):** only the finished website is public; your source files, comments and history stay private. GitHub Pages from a private repository needs GitHub Pro, which is free for students through the [GitHub Student Developer Pack](https://education.github.com/pack).
   - **Public:** works on a free account, but anyone can read every committed file. `content/drafts/` is never committed either way.
2. Push this folder to it:
   ```
   git init -b main
   git add .
   git commit -m "First version"
   git remote add origin https://github.com/322Simon/322Simon.github.io.git
   git push -u origin main
   ```
3. On GitHub, go to **Settings → Pages → Source** and choose **GitHub Actions**. Every push to `main` now rebuilds the site and publishes it within about a minute (see `.github/workflows/deploy.yml`).

**Your own domain** later (about €10 a year): enter it under *Settings → Pages → Custom domain*, add the DNS records GitHub lists there, and update `base_url` in `site.toml`.

**iCloud and git:** this folder lives in iCloud Drive. That's fine for editing. If git ever reports odd errors or files like `index 2`, iCloud has created conflict copies; then keep the repository in a folder outside iCloud instead.

## Files

```
build.py            turns content/ into _site/ (run it with uv; dependencies install automatically)
site.toml           your details and links
content/*.md        pages
content/posts/      published posts, plus assets/ for their images
content/drafts/     unfinished posts; local only, never uploaded
static/             stylesheet, favicon, portrait, PDFs
templates/base.html the frame around every page
```
