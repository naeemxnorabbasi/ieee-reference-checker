# Structure and use

## What happens in a batch

1. `input/files.txt` lists source files, one per line. Relative paths are resolved from the manifest's directory; absolute paths also work. Blank lines and lines beginning with `#` are ignored.
2. `refcheck.py process` detects TXT or image inputs by extension. Images are OCR'd locally with Tesseract, one column at a time for two-column reference pages. Numbered entries such as `[1]` delimit references.
3. The analyzer extracts a title and simple fields, classifies likely reference type, and flags common missing fields by type. This is a review aid, not a formal IEEE compliance determination.
4. By default, the title is queried against the public Crossref works API. The report labels a close title as likely, a weak title as possible/weak, and records lookup failures. Similar titles can refer to different works; inspect all metadata before treating a citation as verified.
5. Entries and provenance are stored in `data/references.sqlite3`. Provenance is the source basename and reference number; the database does not store the source's absolute local path. A timestamped SQLite snapshot and Markdown report are created for every batch.
6. If Git is configured with an `origin`, the database, report, and snapshot are committed and pushed automatically. `--no-publish` disables this for an individual batch.

## Local setup

Install Python 3.10 or newer. For image input, install Tesseract OCR (for example, `brew install tesseract` on macOS) and Python packages:

```sh
python3 -m pip install -r requirements.txt
```

TXT inputs do not require Tesseract. Add paths to `input/files.txt`, one per line. Example:

```text
# Relative to input/files.txt
txt/my_references.txt
/Users/me/papers/references-screenshot.png
```

For a relative entry in the manifest itself (which lives in `input/`), use `txt/my_references.txt`. Then process:

```sh
python3 refcheck.py process input/files.txt
```

To avoid online lookups:

```sh
python3 refcheck.py process input/files.txt --no-online-check
```

To create the report and local database without a Git commit or push:

```sh
python3 refcheck.py process input/files.txt --no-publish
```

The image OCR and metadata lookup can make a batch slower. Review the generated report, especially OCR'd accents, math, URLs, and weak Crossref matches.

## Search and export

Start the local-only web server:

```sh
python3 refcheck.py serve
```

Open <http://127.0.0.1:8765>. Search by title text, author text, venue, citation text, or source filename. Select results into the list, then create a plain-text, CSV, or BibTeX export. The server binds to `127.0.0.1`, so it is available on the local machine only.

## Data files

- `data/references.sqlite3`: current queryable catalog.
- `snapshots/references-<timestamp>.sqlite3`: database state saved for each intake batch.
- `reports/report-<timestamp>.md`: per-batch review report.
- `reports/latest.json`: pointer to the most recent batch report and snapshot.
- `input/files.txt`: local manifest; excluded from Git.
- `input/txt/` and `input/img/`: raw intake samples; excluded from Git by default.

## Interpreting types and formatting flags

The analyzer recognizes common journal, conference, preprint, and website/software patterns. It reports missing fields such as a journal volume, page range, conference name, or year when detected. It does not fully normalize author lists, guarantee every IEEE punctuation/order rule, or resolve all publication types (such as every book, standard, dataset, or library record). Treat the report as a checklist and manually verify the authoritative publication page when exactness matters.

## Checking whether a work exists

Crossref is a DOI registration agency and a broad metadata source: <https://www.crossref.org/>. This tool queries its public works API using parsed titles: <https://api.crossref.org/works>. A matching Crossref result indicates a similar deposited record. To confirm IEEE Xplore coverage, search the title or DOI on IEEE Xplore: <https://ieeexplore.ieee.org/>. Other records may be found in publisher proceedings, ACM Digital Library, arXiv, OpenAlex, or the work's official project page. This version does not query those services directly.
