"""Actual clicks on exported volumes, restricted to disposable GitHub Windows."""
import argparse,os,sys,json,re,time,subprocess,shutil
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO
from hashlib import sha256
from pypdf import PdfReader
from verify_index_viewer_ci import Desktop,wait_for,page_origin,W


def main():
    a=argparse.ArgumentParser();a.add_argument('--viewer-zip',type=Path,required=True);a.add_argument('--submission',type=Path,required=True);a.add_argument('--output',type=Path,required=True);args=a.parse_args()
    if not (os.name=='nt' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('RUNNER_OS')=='Windows'):
        raise SystemExit('Disposable hosted Windows CI only; never controls a workstation.')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True);tools=out/'tools';tools.mkdir()
    assert sha256(args.viewer_zip.read_bytes()).hexdigest()=='98b33a518d42986856d225064b0cd2d3643ecf78cbf84ab873d26cc51877a544'
    with ZipFile(args.viewer_zip) as z:assert z.namelist()==['SumatraPDF-3.6.1-64.exe'];z.extractall(tools)
    with ZipFile(args.submission) as z:z.extractall(out/'Before')
    before=out/'Before/Submission';after=out/'После переноса #/Submission';shutil.copytree(before,after)
    mapping=json.loads((before/'Volumes/Volume map.json').read_bytes());desk=Desktop();cases=[]
    # Each click has its actual destination page checked from the viewer's saved state.
    root=before/'Volumes';first=mapping['volumes'][0]['file'];r=PdfReader(root/first)
    annotations=[a.get_object() for p in r.pages for a in p.get('/Annots',[]) if a.get_object().get('/BLVolumeLink') and a.get_object().get('/A',{}).get('/S')=='/GoToR']
    remote=next(a for a in annotations if int(a['/A']['/D'][0])>=3) # chosen second prepared page, not the material's start
    page_index=next(i for i,p in enumerate(r.pages) if any(a.get_object() is remote for a in p.get('/Annots',[])))
    target=str(remote['/A']['/F']['/UF']);target_page=int(remote['/A']['/D'][0])+1
    toc=next(a.get_object() for a in r.pages[0]['/Annots'] if a.get_object().get('/A',{}).get('/S')=='/GoToR' and str(a.get_object()['/A']['/F']['/UF'])==target)
    target_reader=PdfReader(root/target);back=next(a.get_object() for a in target_reader.pages[target_page-1]['/Annots'] if a.get_object().get('/BLVolumeLink') and a.get_object()['/A']['/S']=='/GoTo')
    local_toc=next(a.get_object() for a in target_reader.pages[0]['/Annots'] if a.get_object().get('/BLVolumeLink') and a.get_object()['/A']['/S']=='/GoTo' and a.get_object()['/A']['/D'][0].idnum==target_reader.pages[target_page-2].indirect_reference.idnum)
    matrix=[('toc-remote',first,1,toc,target,target_page-1),('main-exact',first,page_index+1,remote,target,target_page),
            ('return',target,target_page,back,target,1),('toc-internal',target,1,local_toc,target,target_page-1)]
    hashes={str(p.relative_to(before)):sha256(p.read_bytes()).hexdigest() for p in before.rglob('*.pdf')}
    try:
        for where,folder in (('before',before),('after',after)):
            for label,source,page,annotation,destination,dest_page in matrix:
                name=where+'-'+label;profile=tools/name;profile.mkdir()
                proc=subprocess.Popen([str(tools/'SumatraPDF-3.6.1-64.exe'),'-appdata',str(profile),'-new-window','-page',str(page),'-zoom','50',str(folder/'Volumes'/source)],cwd=tools)
                try:
                    window=wait_for(lambda:next((w for w in desk.windows(proc.pid) if source in w['title']),None),'source window')
                    hwnd=window['hwnd'];desk.u.ShowWindow(hwnd,9);desk.u.SetWindowPos(hwnd,W.HWND(-1),10,10,1000,900,0x40);desk.u.SetForegroundWindow(hwnd)
                    def rendered():
                        image,bounds=desk.capture(hwnd);origin=page_origin(image,2/3)
                        if origin:return image,bounds,origin
                        image.save(out/(name+'-waiting.png'))
                    image,bounds,origin=wait_for(rendered,'rendered source page');image.save(out/(name+'-source.png'))
                    source_pdf=PdfReader(folder/'Volumes'/source);height=float(source_pdf.pages[page-1].cropbox.top)
                    x0,y0,x1,y1=map(float,annotation['/Rect']);scale=2/3
                    desk.click(round(bounds.left+origin[0]+(x0+x1)/2*scale),round(bounds.top+origin[1]+(height-(y0+y1)/2)*scale))
                    opened=wait_for(lambda:next((w for w in desk.windows(proc.pid) if destination in w['title']),None),'clicked target')
                    time.sleep(.6);desk.capture(opened['hwnd'])[0].save(out/(name+'-target.png'))
                    for w in desk.windows(proc.pid):desk.u.PostMessageW(w['hwnd'],0x10,0,0) # graceful close writes actual current page
                    proc.wait(timeout=15)
                    settings=(profile/'SumatraPDF-settings.txt').read_text('utf-8-sig');states={};current=None
                    for line in settings.splitlines():
                        if re.match(r'\s*FilePath\s*=',line):current=line.split('=',1)[1].strip()
                        if current and re.match(r'\s*PageNo\s*=',line):states[current.casefold()]=int(line.split('=',1)[1].strip())
                    expected=str((folder/'Volumes'/destination).resolve()).casefold()
                    assert states.get(expected)==dest_page,(name,dest_page,states)
                    cases.append({'case':name,'file':destination,'actual_page':states[expected],'expected_page':dest_page,'passed':True})
                finally:
                    if proc.poll() is None:proc.terminate();proc.wait(timeout=15)
        for folder in (before,after):
            for name,digest in hashes.items():assert sha256((folder/name).read_bytes()).hexdigest()==digest
    finally:
        (out/'report.json').write_text(json.dumps({'viewer':'SumatraPDF 3.6.1','environment':'hosted Windows CI','input_zip_sha256':sha256(args.submission.read_bytes()).hexdigest(),'cases':cases,'passed':len(cases)==8},ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':main()
