"""Conservative citation extents. Matching and the clickable range are separate."""
import re

LOCATOR=re.compile(r',\s*(?:(?:at\s+)?(?:pages?|pp?|paragraphs?|paras?|articles?|arts?|clauses?|sections?|secs?|страниц[аыуе]?|стр|пункт[а-я]*|пп?|стать[яи]|ст|раздел[а-я]*)\.?\s*|at\s+|[¶§]{1,2}\s*)(?=\d|[IVXLCDM]+\b)',re.I)
JOIN=re.compile(r'[\s,]*(?:and|и|&|see also|см\.)\s*$',re.I)


def propose(text, references):
    ordered=sorted(references,key=lambda r:r['start'])
    for index,ref in enumerate(ordered):
        start,end=ref['start'],ref['end']
        limit=text.find(';',end)
        if limit<0:limit=len(text)
        if index+1<len(ordered):limit=min(limit,ordered[index+1]['start'])
        tail=text[end:limit]
        locator=LOCATOR.search(tail)
        # A comma introduces a title or a pinpoint. Ordinary surrounding prose
        # is never silently converted into a hyperlink.
        proposed=end
        uncertain=False
        if locator:
            proposed=end+locator.start()
        elif re.match(r'\s*,',tail):
            proposed=limit;uncertain=True
        suffix=JOIN.search(text[end:proposed])
        if suffix:proposed=end+suffix.start()
        while proposed>end and text[proposed-1] in ' \t\r\n,.;':proposed-=1
        ref.update(link_start=start,link_end=proposed,link_text=text[start:proposed],scope_review=uncertain)
    return references


def bounds(ref):
    return ref.get('link_start',ref['start']),ref.get('link_end',ref['end'])


def validate(text, references):
    previous=-1
    for ref in sorted(references,key=lambda r:bounds(r)[0]):
        start,end=bounds(ref)
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text):
            raise ValueError('Границы ссылки находятся вне текста сноски.')
        if start>ref['start'] or end<ref['end']:
            raise ValueError('Ссылка должна включать найденное упоминание документа.')
        if text[start:end]!=ref.get('link_text',ref['mention']):
            raise ValueError('Текст ссылки изменился. Повторите сопоставление.')
        if start<previous:raise ValueError('Ссылки на разные документы пересекаются. Исправьте их границы.')
        previous=end
