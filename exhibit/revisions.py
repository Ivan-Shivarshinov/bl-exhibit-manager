"""Conservative transfer of reviewed references between uploaded DOCX editions."""
from collections import Counter
from copy import deepcopy
from . import word, citations


def reconcile(previous, found):
    old_paras = {(p['fid'], p['paragraph']): p['text'] for p in previous.get('footnotes', [])}
    new_paras = {(p['fid'], p['paragraph']): p['text'] for p in found['footnotes']}
    old_counts, new_counts = Counter(old_paras.values()), Counter(new_paras.values())
    report = {'preserved': 0, 'added': 0, 'review': 0, 'removed': 0, 'items': []}
    used = set()
    found['excluded_references'] = []
    for old in previous.get('excluded_references', []):
        text = old_paras.get((old['fid'], old['paragraph']))
        if old_counts[text] != 1 or new_counts[text] != 1:
            continue
        para = next(p for p in found['footnotes'] if p['text'] == text)
        ref = {**deepcopy(old), 'fid': para['fid'], 'paragraph': para['paragraph'], 'footnote': para['footnote']}
        ref['key'] = f"{ref['fid']}:{ref['paragraph']}:{ref['start']}:{ref['end']}"
        found['excluded_references'].append(ref)
    # Recompute scopes after removing exclusions, so neighboring titles can grow.
    for para in found['footnotes']:
        blocked = [r for r in found['excluded_references'] if (r['fid'], r['paragraph']) == (para['fid'], para['paragraph'])]
        found['references'] = [r for r in found['references'] if not any(
            (r['fid'], r['paragraph']) == (b['fid'], b['paragraph']) and r['start'] < b['end'] and r['end'] > b['start'] for b in blocked)]
        refs = [r for r in found['references'] if (r['fid'], r['paragraph']) == (para['fid'], para['paragraph'])]
        if blocked:
            citations.propose(para['text'], refs)
    # Only an identical, unique paragraph can carry an arbitrary manual range.
    # Footnote IDs/ordinals are not stable after editing or pasting in Word.
    for ref in found['references']:
        text = new_paras[(ref['fid'], ref['paragraph'])]
        candidates = [r for r in previous.get('references', [])
                      if old_paras.get((r['fid'], r['paragraph'])) == text
                      and r['start'] == ref['start'] and r['end'] == ref['end'] and r['mention'] == ref['mention']]
        if previous.get('links_reviewed') and old_counts[text] == new_counts[text] == 1 and len(candidates) == 1:
            old = candidates[0]
            for key in ('target', 'manual', 'custom', 'keep_original', 'scope_manual', 'link_start', 'link_end', 'link_text', 'scope_review'):
                if key in old: ref[key] = deepcopy(old[key])
            ref['manual'] = True
            used.add(old['key'])
            status = 'preserved'
        elif ref.get('managed'):
            status = 'review' if ref.get('scope_review') or not ref['target'] else 'preserved'
        else:
            same = [r for r in previous.get('references', []) if word.match_key(r['mention']) == word.match_key(ref['mention'])]
            status = 'review' if same else 'added'
        ref['edition_status'] = status
        report[status] += 1
        report['items'].append({'key': ref['key'], 'footnote': ref['footnote'], 'text': ref.get('link_text', ref['mention']), 'status': status})
    # Preserve custom mentions that the automatic matcher cannot discover.
    for old in previous.get('references', []):
        if old['key'] in used or not old.get('custom') or not previous.get('links_reviewed'):
            continue
        text = old_paras.get((old['fid'], old['paragraph']))
        if old_counts[text] != 1 or new_counts[text] != 1: continue
        para = next(p for p in found['footnotes'] if p['text'] == text)
        start, end = citations.bounds(old)
        if any(r['fid'] == para['fid'] and r['paragraph'] == para['paragraph'] and start < citations.bounds(r)[1] and end > citations.bounds(r)[0] for r in found['references']): continue
        ref = {**deepcopy(old), 'fid': para['fid'], 'paragraph': para['paragraph'], 'footnote': para['footnote'], 'edition_status': 'preserved'}
        ref['key'] = f"{ref['fid']}:{ref['paragraph']}:{ref['start']}:{ref['end']}"
        found['references'].append(ref); used.add(old['key']); report['preserved'] += 1
        report['items'].append({'key': ref['key'], 'footnote': ref['footnote'], 'text': ref.get('link_text', ref['mention']), 'status': 'preserved'})
    new_mentions = Counter(word.match_key(r['mention']) for r in found['references'])
    for old in previous.get('references', []):
        key = word.match_key(old['mention'])
        if new_mentions[key]: new_mentions[key] -= 1
        else:
            report['removed'] += 1
            report['items'].append({'footnote': old['footnote'], 'text': old.get('link_text', old['mention']), 'status': 'removed'})
    return report
