# IEEE Reference Checker

A local-first tool for extracting numbered references from TXT files and screenshots, reviewing common IEEE-style fields, checking for scholarly metadata matches, and maintaining a searchable SQLite catalog. It includes a local browser interface for autocomplete-style search and export to plain text, CSV, and BibTeX.

- Crossref: <https://www.crossref.org/> and its public metadata API: <https://api.crossref.org/works>
- Full setup, structure, and operating instructions: [docs/USAGE.md](docs/USAGE.md)
- Repository and database publishing workflow: [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md)

## Quick start

Requirements: Python 3.10+, Pillow (for image input), and Tesseract OCR (for image input). TXT-only intake and the web app use Python's standard library.

```sh
python3 -m pip install -r requirements.txt
python3 refcheck.py process input/files.txt
python3 refcheck.py serve
```

Open <http://127.0.0.1:8765>. The processor checks Crossref by default. For offline processing, run `python3 refcheck.py process input/files.txt --no-online-check`.

Each batch writes `data/references.sqlite3`, a timestamped copy under `snapshots/`, a Markdown report under `reports/`, and `reports/latest.json`. If this checkout has an `origin` Git remote, a successful batch automatically commits and pushes the updated database, report, and snapshot. Use `--no-publish` to keep a batch local.

## Crossref match meaning

Crossref lookup is a discovery aid. A likely title match means Crossref has a similar registered metadata record; it does not certify all citation fields or establish IEEE Xplore indexing. A weak match needs human review. No match does not prove a citation is false. See the report and usage guide for limits and review steps.

## Public data note

The demo database contains bibliographic metadata and source filenames only; local absolute paths are not stored. Raw input files and the manifest are excluded from Git by default. Before pushing a catalog you maintain, review that the references and filenames are appropriate to publish.

## License

MIT. See [LICENSE](LICENSE).
