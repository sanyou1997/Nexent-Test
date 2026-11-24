"""Exact article lookup against checksummed local source files; no network access."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent / '本地法规库'


def load_snapshot():
    config = json.loads((ROOT / 'integrity.json').read_text(encoding='utf-8'))
    blobs = {}
    for filename, expected in config['files'].items():
        data = (ROOT / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('本地快照校验失败：' + filename)
        blobs[filename] = data
    manifest = json.loads(blobs['manifest.json'])
    articles = json.loads(blobs['articles.json'])
    locations = json.loads(blobs['source-locations.json'])
    docs = {m['filename']: m for m in manifest}
    assert len(articles) == sum(m['article_count'] for m in manifest)
    for row in articles:
        doc = docs[row['filename']]
        text = blobs['入库文档/' + row['filename']].decode('utf-8')
        if row['text'] not in text or row['title'] != doc['title']:
            raise ValueError('本地条文与原文件不一致')
    return manifest, articles, locations


def source_check():
    try:
        manifest, articles, _ = load_snapshot()
        return dict(ok=True, mode='offline_snapshot', documents=len(manifest), articles=len(articles),
                    snapshot_date='2026-11-23', scope='本地存档文件与条文校验一致；未联网核验后续修订，适用版本需按行为时间复核。')
    except Exception as exc:
        return dict(ok=False, mode='offline_snapshot', status='本地法规校验失败，停止引用', detail=str(exc))


def article(title, number):
    manifest, rows, locations = load_snapshot()
    title = title.strip().strip('《》')
    base = title
    version = ''
    # Existing MCP schema stays compatible: an explicit year may be appended to title.
    match = re.search(r'[（(]([^()（）]*\d{4}[^()（）]*)[）)]$', title)
    if match:
        base = title[:match.start()]
        version = match.group(1)
    candidates = [m for m in manifest if m['title'] == base]
    if version:
        year = re.search(r'\d{4}', version).group()
        candidates = [m for m in candidates if year in m['version']]
    elif candidates:
        candidates = [max(candidates, key=lambda m: m['effective'])]
    if len(candidates) != 1:
        return dict(status='本地快照未收录此法规或版本', title=title, number=number)
    doc = candidates[0]
    matches = [r for r in rows if r['filename'] == doc['filename'] and str(r['number']) == str(number)]
    if len(matches) != 1:
        return dict(status='本地快照未收录此条文', title=title, number=number, version=doc['version'])
    row = matches[0]
    return dict(**row, id=f"local-{doc['code']}-{doc['effective']}-{row['number']}",
                url=locations[doc['filename']], historical=doc['historical'],
                snapshot_date=doc['snapshot_date'], source_type='local_file')
