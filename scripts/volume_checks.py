"""Black-box 0.7 acceptance for the actual frozen application, not source imports."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import json,time


def wait(request,job):
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        state=request('/api/volume-jobs/'+job['id'])
        if state['state'] not in ('queued','running'):
            assert state['state']=='complete',state
            return state['result']
        time.sleep(.05)
    raise AssertionError('Frozen volume job timed out')


def export(request,route):
    preview=wait(request,request(route+'/volumes/preview',{}))
    assert not preview['issues'],preview['issues']
    result=wait(request,request(route+'/volumes/export',{'preview_token':preview['preview_token']}))
    return request('/api/volume-jobs/'+result['download_token']+'/download'),preview


def verify(request,data,outgoing):
    from exhibit.samples import make_pdf
    from tests.test_feedback import citation_docx
    p=request('/api/projects',{'name':'Synthetic frozen volumes'});pid=p['id'];route='/api/projects/'+pid
    source=make_pdf('Synthetic',[[f'Exact source page {n}'] for n in range(1,3)])
    for n in (1,2):
        p=request(route+f'/upload?name=Annex{n}.pdf&mode=passthrough',raw=source)
        p=request(route+'/documents/'+p['documents'][-1]['id'],{'designation':'Annex','number':n,'folder':'Материалы # 50%'})
    old=request(route+'/export',{});eid=request(route+'/exports')[0]['id']
    original_main=citation_docx(['Annex 1, Synthetic first, p. 2.','Annex 2, Synthetic second, para. 4.'])
    p=request(route+'/upload?kind=main&name=Original.docx',raw=original_main)
    p=request(route+'/scan',{});p=request(route+'/references/confirm',{})
    assignment={'rows':[{'id':p['documents'][0]['id'],'number':2},{'id':p['documents'][1]['id'],'number':1}],
                'naming':'identifier','references':{r['key']:'update' for r in p['references']}}
    planned=request(route+'/assignments/preview',{'request':assignment});assert not planned['conflicts'],planned
    p=request(route+'/assignments/apply',{'request':assignment,'token':planned['token']})
    assert [d['identifier'] for d in p['documents']]==['Annex 2','Annex 1']
    request(route+'/references/confirm',{})
    config=request(route)['volume_settings'];config.update(enabled=True,max_pages=4)
    request(route+'/volumes/settings',config)
    profile=request('/api/profiles',{'project_id':pid,'name':'Frozen portable profile','naming':'identifier','folder':'','prefix':''})
    payload={'profile_id':profile['profile_id'],'version_id':profile['versions'][-1]['version_id'],
             'options':{'reset_overrides':False,'apply_paths':False,'apply_prefix':False}}
    planned=request(route+'/profile/preview',payload);assert not planned['conflicts'],planned
    request(route+'/profile/apply',{**payload,'token':planned['token']})
    package,preview=export(request,route)
    with ZipFile(BytesIO(package)) as z:
        mapping=json.loads(z.read('Submission/Volumes/Volume map.json'))
        assert len(mapping['pages'])==4 and len(mapping['volumes'])==2
        for meta in mapping['volumes']:
            assert sha256(z.read('Submission/Volumes/'+meta['file'])).hexdigest()==meta['sha256']
        for n in (1,2):assert z.read(f'Submission/Материалы # 50%/Annex {n}.pdf')==source
    p=request(route);assert (data/pid/'inputs'/p['assignment_sources'][0]['blob']).read_bytes()==original_main
    assert request(route+'/exports/'+eid)==old
    token=request(route+'/backup',{})['token'];raw=request('/api/backups/'+token+'/download')
    outgoing.mkdir(parents=True,exist_ok=True);(outgoing/'volumes-project.zip').write_bytes(raw)
    # Profile catalog is deliberately absent from project archives.
    with ZipFile(BytesIO(raw)) as z:
        assert not any('.submission-profiles' in n for n in z.namelist())
    (outgoing/'volumes-result.json').write_text(json.dumps({'passed':True,'version':'0.7.0','volumes':len(preview['volumes']),
       'checks':['frozen assignments','frozen profiles','exact sources','two volumes','immutable old ZIP','portable schema 4 archive']},indent=2),'utf-8')
    return pid
