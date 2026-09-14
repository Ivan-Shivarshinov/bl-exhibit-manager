"""Document labels shared by numbering, matching, and PDF stamps."""
def identifier(doc):
    if doc.get('number') is None:return ''
    if doc.get('prefix'):return f"{doc['prefix']}-{doc['number']}"
    return ' '.join(str(x) for x in (doc.get('designation',''),doc['number']) if x != '')


def stamp_label(doc):
    value=identifier(doc)
    return ' '.join(x for x in (doc.get('designation',''),value) if x) if doc.get('prefix') else value
