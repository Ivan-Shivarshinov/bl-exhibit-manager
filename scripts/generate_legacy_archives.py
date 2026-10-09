"""Generate synthetic archives with the actual released v0.3/v0.4 serializer."""
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parent.parent

for tag in ('v0.3.0','v0.4.0'):
    destination=(ROOT/'output/legacy-archives'/tag/'legacy-project.zip').resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists(): destination.unlink()
    source=subprocess.check_output(['git','archive','--format=tar',tag],cwd=ROOT)
    with TemporaryDirectory(prefix='exhibit-legacy-') as tmp:
        with tarfile.open(fileobj=BytesIO(source)) as tar:tar.extractall(tmp,filter='data')
        script="from exhibit.project import Store; from exhibit.samples import make_pdf; from exhibit.backup import save_archive; import sys; s=Store('synthetic-data'); p=s.create('Released archive'); s.upload(p,'Legacy.pdf',make_pdf('Legacy',[['Synthetic old source']])); d=p['documents'][0]; s.update(p,d['id'],{'number':1}); s.approve(p,d['id'],'document'); s.export(p); save_archive(s,p['id'],sys.argv[1])"
        subprocess.run([sys.executable,'-c',script,str(destination)],cwd=tmp,check=True)
    print(tag,str(destination))
