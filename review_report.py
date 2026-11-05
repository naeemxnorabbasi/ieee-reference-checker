"""Field-by-field comparison report using the Crossref candidate stored per reference."""
import datetime as dt
import json
import re
import sqlite3
import subprocess
import urllib.parse
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from refcheck import DB, ROOT, infer

DEFAULT_THRESHOLDS = {
    'title': 0.85,
    'authors': 0.75,
    'venue': 0.75,
    'year': 1.00,
    'doi': 1.00,
    'type': 1.00,
}
FIELD_LABELS = {
    'title': 'Title', 'authors': 'Authors', 'venue': 'Venue',
    'year': 'Year', 'doi': 'DOI', 'type': 'Publication type',
}


def clean_text(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r'(?i)\\(?:sub|sup)\s*|/(?:sub|sup)\s*', '', value)
    value = re.sub(r'(?<=\w)-\s+(?=\w)', '', value)
    value = re.sub(r'\bet\s+al\.?', '', value, flags=re.I)
    value = re.sub(r'\band\b', '', value, flags=re.I)
    return re.sub(r'[^a-z0-9]+', '', value.casefold())


def text_similarity(left, right):
    a, b = clean_text(left), clean_text(right)
    if not a or not b:
        return None
    return SequenceMatcher(None, a, b).ratio()


def crossref_authors(metadata):
    people = metadata.get('author') or []
    values = []
    for person in people:
        given = person.get('given', '').strip()
        family = person.get('family', '').strip()
        name = ' '.join(part for part in (given, family) if part)
        if name:
            values.append(name)
    return '; '.join(values)


def crossref_year(metadata):
    for key in ('published-print', 'published-online', 'published', 'issued', 'created'):
        date_parts = (metadata.get(key) or {}).get('date-parts') or []
        if date_parts and date_parts[0]:
            return str(date_parts[0][0])
    return ''


def crossref_venue(metadata):
    return '; '.join(metadata.get('container-title') or [])


def crossref_type(metadata):
    raw = (metadata.get('type') or '').casefold()
    if raw in {'journal-article', 'journal-issue'}:
        return 'Journal paper'
    if raw in {'proceedings-article', 'proceedings'}:
        return 'Conference paper'
    if raw in {'posted-content'}:
        return 'Preprint'
    if raw in {'book', 'book-chapter', 'reference-book', 'book-series'}:
        return 'Book'
    if raw in {'dataset'}:
        return 'Dataset'
    if raw in {'component', 'report', 'standard', 'other'}:
        return raw.replace('-', ' ').title()
    return raw.replace('-', ' ').title()


def doi_key(value):
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(value or '').strip(), flags=re.I)
    return value.strip(' .,;').casefold()


def parse_people(value):
    value = re.sub(r'\bet\s+al\.?', '', str(value or ''), flags=re.I)
    chunks = re.split(r'\s*,\s*|\s+and\s+|\s*;\s*', value, flags=re.I)
    people = []
    for chunk in chunks:
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", unicodedata.normalize('NFKD', chunk))
        words = [w for w in words if not all(unicodedata.combining(ch) for ch in w)]
        words = [w for w in words if w.casefold() not in {'jr', 'sr', 'ii', 'iii', 'iv'}]
        if len(words) >= 2:
            people.append({'family': clean_text(words[-1]), 'initials': ''.join(w[0].casefold() for w in words[:-1])})
    return people


def author_similarity(source, candidate_people):
    source_people = parse_people(source)
    candidate_people = [p for p in candidate_people if p.get('family')]
    if not source_people or not candidate_people:
        return None
    abbreviated = bool(re.search(r'\bet\s+al\.?', str(source), flags=re.I))
    remaining = list(candidate_people)
    total = 0.0
    for person in source_people:
        best_score, best_index = 0.0, None
        for i, candidate in enumerate(remaining):
            family_score = SequenceMatcher(None, person['family'], candidate['family']).ratio()
            if family_score < 0.75:
                continue
            initials_score = 1.0 if (not person['initials'] or not candidate.get('initials') or
                                     person['initials'][0] == candidate['initials'][0]) else 0.0
            score = 0.85 * family_score + 0.15 * initials_score
            if score > best_score:
                best_score, best_index = score, i
        if best_index is not None:
            total += best_score
            remaining.pop(best_index)
    denominator = len(source_people) if abbreviated else max(len(source_people), len(candidate_people))
    return total / denominator if denominator else None


def compare_field(field, source, candidate, threshold, candidate_people=None):
    if not str(source or '').strip() or not str(candidate or '').strip():
        return {'source': source or '', 'candidate': candidate or '', 'score': None,
                'threshold': threshold, 'state': 'MISSING'}
    if field == 'authors':
        score = author_similarity(source, candidate_people or parse_people(candidate))
    elif field in {'title', 'venue'}:
        score = text_similarity(source, candidate)
    elif field == 'doi':
        score = 1.0 if doi_key(source) == doi_key(candidate) else 0.0
    elif field in {'year', 'type'}:
        score = 1.0 if clean_text(source) == clean_text(candidate) else 0.0
    else:
        score = None
    state = 'PASS' if score is not None and score >= threshold else 'REVIEW'
    return {'source': source, 'candidate': candidate, 'score': score,
            'threshold': threshold, 'state': state}


def md_cell(value):
    return str(value or '—').replace('|', '\\|').replace('\n', ' ').strip()


def pct(value):
    return '—' if value is None else f'{value:.0%}'


def load_thresholds(config_path):
    data = json.loads(Path(config_path).read_text())
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(data.get('thresholds', {}))
    unknown = set(thresholds) - set(FIELD_LABELS)
    if unknown:
        raise ValueError('Unsupported threshold field(s): ' + ', '.join(sorted(unknown)))
    for field, value in thresholds.items():
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f'Threshold for {field} must be between 0.0 and 1.0.')
    return thresholds


def publish_review(batch, report_path):
    def git(*args):
        return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)
    if git('rev-parse', '--is-inside-work-tree').returncode:
        print('Git publish skipped: initialize this folder as a Git repository and configure origin.')
        return
    if git('remote', 'get-url', 'origin').returncode:
        print('Git publish skipped: no origin remote is configured.')
        return
    files = [str(report_path.relative_to(ROOT)), 'reports/latest_review.json']
    added = git('add', '--', 'refcheck.py', 'review_report.py', 'review_config.json',
                'README.md', 'docs/USAGE.md', *files)
    if added.returncode:
        raise RuntimeError('Could not stage review report: ' + added.stderr.strip())
    committed = git('commit', '-m', f'Add field comparison review report {batch}')
    if committed.returncode:
        raise RuntimeError('Could not commit review report: ' + committed.stderr.strip())
    branch = git('branch', '--show-current').stdout.strip()
    pushed = git('push', '-u', 'origin', branch)
    if pushed.returncode:
        raise RuntimeError('Review report committed locally but push failed: ' + pushed.stderr.strip())
    print(f'Review report committed and pushed on {branch}.')


def generate_review_report(batch_id=None, config_path=None, publish=True):
    config_path = Path(config_path or ROOT / 'review_config.json').expanduser().resolve()
    thresholds = load_thresholds(config_path)
    if not batch_id:
        latest_path = ROOT / 'reports' / 'latest.json'
        if not latest_path.exists():
            raise FileNotFoundError('No processed batch found; run process first or pass --batch.')
        batch_id = json.loads(latest_path.read_text())['batch_id']
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute('SELECT * FROM refs WHERE batch_id=? ORDER BY source_file, CAST(source_number AS INTEGER), id',
                       (batch_id,)).fetchall()
    con.close()
    if not rows:
        raise ValueError(f'No references found for batch {batch_id}.')

    entries = []
    for row in rows:
        source = infer(row['citation'])
        try:
            metadata = json.loads(row['raw_metadata'] or '')
            if not isinstance(metadata, dict):
                metadata = {}
        except (TypeError, ValueError):
            metadata = {}
        candidate = {
            'title': (metadata.get('title') or [row['matched_title'] or ''])[0],
            'authors': crossref_authors(metadata),
            'venue': crossref_venue(metadata),
            'year': crossref_year(metadata),
            'doi': metadata.get('DOI', ''),
            'type': crossref_type(metadata),
            'url': metadata.get('URL', ''),
            'author_records': [{'family': clean_text(p.get('family', '')), 'initials': ''.join(w[0].casefold() for w in re.findall(r'[A-Za-zÀ-ÖØ-öø-ÿ]+', p.get('given', '')))} for p in metadata.get('author', [])],
        }
        if not candidate['doi']:
            candidate['doi'] = row['doi'] or ''
        source_fields = {
            'title': source['title'], 'authors': source['authors'],
            'venue': source['venue'], 'year': source['year'],
            'doi': source['doi'], 'type': source['kind'] if source['kind'] != 'Unclassified' else '',
        }
        comparisons = {f: compare_field(f, source_fields[f], candidate[f], thresholds[f], candidate.get('author_records'))
                      for f in FIELD_LABELS}
        title_score = comparisons['title']['score']
        title_qualified = title_score is not None and title_score >= thresholds['title']
        scholar_q = urllib.parse.urlencode({'q': source['title'] or row['citation']})
        doi_link = ('https://doi.org/' + urllib.parse.quote(doi_key(candidate['doi']), safe='/:._-')
                    if candidate['doi'] else '')
        crossref_link = ('https://api.crossref.org/works/' + urllib.parse.quote(doi_key(candidate['doi']), safe='/:._-')
                         if candidate['doi'] else '')
        entries.append({
            'row': row, 'source': source, 'candidate': candidate,
            'comparisons': comparisons, 'title_qualified': title_qualified,
            'scholar_url': 'https://scholar.google.com/scholar?' + scholar_q,
            'doi_link': doi_link, 'crossref_link': crossref_link,
            'has_candidate': bool(candidate['title'] or candidate['doi'] or candidate['authors']),
        })

    qualified = sum(e['title_qualified'] for e in entries)
    candidates = sum(e['has_candidate'] for e in entries)
    below_threshold = sum(e['has_candidate'] and e['comparisons']['title']['score'] is not None and not e['title_qualified'] for e in entries)
    unscored_candidates = sum(e['has_candidate'] and e['comparisons']['title']['score'] is None for e in entries)
    lines = [
        '# Field-Level Reference Match Review', '',
        f'- Source processing batch: `{batch_id}`',
        f'- References reviewed: {len(entries)}',
        f'- Crossref candidate records available: {candidates}',
        f'- Title score meeting threshold ({thresholds["title"]:.0%}): {qualified}',
        f'- Candidate title below threshold: {below_threshold}',
        f'- Candidate available but title score unavailable: {unscored_candidates}',
        f'- No candidate metadata available: {len(entries) - candidates}', '',
        '## Similarity thresholds', '',
        '| Field | Threshold |', '|---|---:|',
    ]
    for field, label in FIELD_LABELS.items():
        lines.append(f'| {label} | {thresholds[field]:.0%} |')
    lines += ['',
        '**How to read this report:** scores compare the parsed source citation with the single Crossref result stored during processing. `PASS` means the score meets the configured threshold; `REVIEW` means it does not; `MISSING` means one side has no value to compare. A high score is a candidate match, not proof of publication or correctness. The stored Crossref lookup note reflects the original search-time score; the table scores use the configurable field comparisons in this report. Check the linked DOI/publisher record and Scholar search manually, especially when any field is flagged.', '',
        'Google Scholar searches below are browser links for manual review. The tool does not automate Google Scholar queries.', '']

    field_counts = {f: {'pass': 0, 'review': 0, 'missing': 0} for f in FIELD_LABELS}
    for e in entries:
        for field, result in e['comparisons'].items():
            state = result['state'].lower()
            field_counts[field][state] += 1
    lines += ['## Field comparison summary', '', '| Field | Pass | Review | Missing/unavailable |', '|---|---:|---:|---:|']
    for field, label in FIELD_LABELS.items():
        counts = field_counts[field]
        lines.append(f'| {label} | {counts["pass"]} | {counts["review"]} | {counts["missing"]} |')
    lines.append('')

    for e in entries:
        row, source, candidate = e['row'], e['source'], e['candidate']
        cmp = e['comparisons']
        title_result = 'TITLE THRESHOLD MET' if e['title_qualified'] else ('BELOW TITLE THRESHOLD' if e['has_candidate'] else 'NO CROSSREF CANDIDATE')
        lines += [f'## [{row["source_number"]}] {md_cell(source["title"] or "(title not parsed)")}', '',
                  f'- Source file: `{md_cell(row["source_file"])}`',
                  f'- Match decision: **{title_result}** (title {pct(cmp["title"]["score"])}; threshold {thresholds["title"]:.0%})',
                  f'- Stored Crossref lookup note: {md_cell(row["online_status"])}',
                  f'- Manual Google Scholar search: [search this title]({e["scholar_url"]})']
        if row['citation']:
            lines += ['', '**Input citation:**', '', '> ' + md_cell(row['citation'])]
        lines += ['', '### Field-by-field comparison', '',
                  '| Field | Parsed input | Crossref metadata | Score | Threshold | Result |',
                  '|---|---|---|---:|---:|---|']
        for field, label in FIELD_LABELS.items():
            item = cmp[field]
            lines.append(f'| {label} | {md_cell(item["source"])} | {md_cell(item["candidate"])} | {pct(item["score"])} | {item["threshold"]:.0%} | {item["state"]} |')
        if e['has_candidate']:
            lines += ['', '### Crossref candidate metadata', '',
                      f'- Candidate title: {md_cell(candidate["title"])}',
                      f'- Authors: {md_cell(candidate["authors"])}',
                      f'- Venue: {md_cell(candidate["venue"])}',
                      f'- Publication year: {md_cell(candidate["year"])}',
                      f'- Crossref type: {md_cell(candidate["type"])}',
                      f'- DOI: {("[" + md_cell(candidate["doi"]) + "](" + e["doi_link"] + ")") if e["doi_link"] else "—"}',
                      f'- Crossref API record: [{e["crossref_link"]}]({e["crossref_link"]})' if e['crossref_link'] else '- Crossref API record: unavailable',
                      f'- Candidate landing URL: [{candidate["url"]}]({candidate["url"]})' if candidate['url'] else '- Candidate landing URL: unavailable']
        else:
            lines += ['', '### Crossref candidate metadata', '',
                      'No candidate metadata was stored for this reference. This means unresolved by this batch, not that the paper is nonexistent.']
        lines.append('')

    stamp = dt.datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')
    report = ROOT / 'reports' / f'review-report-{batch_id}-{stamp}.md'
    report.parent.mkdir(exist_ok=True)
    report.write_text('\n'.join(lines) + '\n')
    latest_review = ROOT / 'reports' / 'latest_review.json'
    latest_review.write_text(json.dumps({
        'source_batch_id': batch_id,
        'review_report': str(report.relative_to(ROOT)),
        'reference_count': len(entries),
        'thresholds': thresholds,
        'title_threshold_met': qualified,
        'candidate_below_title_threshold': below_threshold,
        'candidate_title_unscored': unscored_candidates,
        'no_candidate_metadata': len(entries) - candidates,
        'candidate_count': candidates,
    }, indent=2) + '\n')
    print(f'Created field-level review report: {report}')
    print(f'Title threshold met: {qualified}/{len(entries)}; Crossref candidates: {candidates}/{len(entries)}')
    if publish:
        publish_review(batch_id, report)
    return report
