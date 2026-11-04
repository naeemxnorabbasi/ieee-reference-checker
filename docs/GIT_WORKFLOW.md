# GitHub and database update workflow

## First-time setup

The published tool is a normal Git repository. Clone it, install dependencies, edit your local input manifest, and process references:

```sh
git clone https://github.com/OWNER/ieee-reference-checker.git
cd ieee-reference-checker
python3 -m pip install -r requirements.txt
# Add local source paths to input/files.txt
python3 refcheck.py process input/files.txt
```

For the maintainer checkout, configure a GitHub remote once:

```sh
git remote add origin https://github.com/OWNER/ieee-reference-checker.git
git push -u origin main
```

Authenticate Git using GitHub CLI (`gh auth login`) or another HTTPS Git credential manager. Check the remote with `git remote -v`.

## Every new batch

A normal process run updates the SQLite database, writes a timestamped database snapshot and report, then stages, commits, and pushes those catalog artifacts if `origin` exists. The commit message includes the batch timestamp. This gives each batch a Git history entry and a standalone SQLite snapshot.

```sh
python3 refcheck.py process input/files.txt
```

Use `--no-publish` to process locally without committing or pushing. Use `--no-online-check` to skip Crossref lookup. You can combine both flags.

If an automatic push fails, the database and report remain on disk and the commit may already exist locally. Fix authentication or remote settings and run:

```sh
git status
git push
```

Do not run the same manifest repeatedly just to retry a push: it will create another intake batch and snapshot. A plain `git push` retries publication of the existing commit.

## What Git tracks

The repository tracks source code, documentation, the current SQLite catalog, per-batch reports, and database snapshots. `.gitignore` excludes `input/files.txt`, raw files under `input/txt/` and `input/img/`, local OCR work, and Python cache files. Source file basenames are stored to preserve traceability without publishing local absolute paths.

The catalog and reports can reveal which publications a project is using. Review each batch before publication, and do not configure a public remote if its contents should remain private. If needed, use a private GitHub repository or process with `--no-publish`.

## Adding new input files

Put references in a TXT file or an image and add its local path to `input/files.txt`. Paths can be absolute or relative to `input/files.txt`. Do not commit raw files unless you explicitly want them distributed. The resulting catalog records each source basename and reference number.
