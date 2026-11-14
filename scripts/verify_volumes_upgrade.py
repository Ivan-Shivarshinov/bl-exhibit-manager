"""Released 0.6.0 -> frozen 0.7.0 -> guarded rollback, isolated data only."""
import argparse,json,sys,shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from exhibit import __version__,volume_settings
from exhibit.samples import make_pdf
from verify_index_upgrade import running
from volume_checks import export


def main():
    parser=argparse.ArgumentParser();parser.add_argument('package');parser.add_argument('--output',required=True);a=parser.parse_args()
    package=Path(a.package).resolve();target=package.stem.split('-'+__version__+'-')[-1]
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='bl-volume-upgrade-') as tmp:
        root=Path(tmp);data=root/'data';old=root/f'BLExhibitManager-0.6.0-{target}.zip'
        base='https://github.com/Ivan-Shivarshinov/bl-exhibit-manager/releases/download/v0.6.0/'
        with urlopen(base+'SHA256SUMS.txt',timeout=60) as r:sums=r.read().decode()
        expected=next(line.split()[0] for line in sums.splitlines() if line.endswith(old.name))
        with urlopen(base+old.name,timeout=60) as r,old.open('wb') as destination:shutil.copyfileobj(r,destination)
        assert sha256(old.read_bytes()).hexdigest()==expected
        source=make_pdf('Synthetic upgrade',[['Immutable legacy source']])
        with running(old,root,data,'old-first') as request:
            assert request('/api/health')['version']=='0.6.0'
            p=request('/api/projects',{'name':'Synthetic legacy 0.6'});pid=p['id'];route='/api/projects/'+pid
            p=request(route+'/upload?name=Annex12.pdf&mode=passthrough',raw=source)
            request(route+'/documents/'+p['documents'][0]['id'],{'designation':'Annex','number':12})
            # Schema 3 existed in 0.6.0 and is the rollback boundary.
            cfg={'enabled':True,'language':'en','title':'Legacy exhibits','formats':['pdf'],'columns':['pages'],'order':'identifier'}
            request(route+'/submission-index',cfg,method='PATCH')
            preview=request(route+'/submission-index/preview',{})
            oldzip=request(route+'/export',{'preview_token':preview['preview_token']});eid=request(route+'/exports')[0]['id']
        with running(package,root,data,'new') as request:
            assert request('/api/health')['version']==__version__
            before=(data/pid/'project.json').read_bytes();assert request(route)['schema']==3
            assert (data/pid/'project.json').read_bytes()==before
            p=request(route+'/volumes/settings',{**volume_settings.DEFAULT,'enabled':True});assert p['schema']==4
            recovery=(data/p['schema_backup']).read_bytes()
            result,_=export(request,route)
            with ZipFile(BytesIO(result)) as z:assert z.read('Submission/Annex12.pdf')==source
            assert request(route+'/exports/'+eid)==oldzip
            after=(data/pid/'project.json').read_bytes()
        with running(old,root,data,'old-rollback') as request:
            assert 'Версия проекта' in request(route,expected=400)['detail']
            assert (data/pid/'project.json').read_bytes()==after
            check=request('/api/backups/preview',raw=recovery)
            p=request('/api/backups/'+check['token']+'/restore',{'copy':True});assert p['schema']==3
            assert request('/api/projects/'+p['id']+'/exports/'+eid)==oldzip
        (out/'upgrade-0.6-report.json').write_text(json.dumps({'target':target,'oldVersion':'0.6.0','newVersion':__version__,
            'oldPackageSha256':expected,'openingDidNotMigrate':True,'migrationAndExport':True,
            'oldRefusesWithoutWriting':True,'recoveryInOldApp':True},indent=2),'utf-8')


if __name__=='__main__':main()
