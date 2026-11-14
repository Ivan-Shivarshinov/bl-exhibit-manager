"""Declared synthetic profiles, measured in separate processes; no retained large PDFs."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
from io import BytesIO
from hashlib import sha256

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Declared before execution. Each item is an independent 10-page synthetic PDF.
PROFILES={'small':(3,2,30),'working':(50,10,180),'maximum':(1000,10,600)}
MEMORY_LIMIT=2_500_000_000


def peak_memory():
    if os.name=='nt':
        import ctypes as c
        class Counters(c.Structure):
            _fields_=[('cb',c.c_ulong),('faults',c.c_ulong)]+[(n,c.c_size_t) for n in ('peak','working','poolPeak','pool','nonPeak','non','page','peakPage')]
        kernel=c.WinDLL('kernel32');kernel.GetCurrentProcess.restype=c.c_void_p
        value=Counters();value.cb=c.sizeof(value)
        ps=c.WinDLL('psapi');ps.GetProcessMemoryInfo.argtypes=[c.c_void_p,c.c_void_p,c.c_ulong]
        assert ps.GetProcessMemoryInfo(kernel.GetCurrentProcess(),c.byref(value),value.cb)
        return value.peak
    import resource
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform=='darwin' else value*1024


def run(name):
    from exhibit import volumes,volume_settings
    from exhibit.samples import make_pdf
    count,pages,budget=PROFILES[name];start=time.monotonic();progress_count=0
    def stopped():
        if time.monotonic()-start>budget:raise AssertionError('Declared time budget exceeded')
        if peak_memory()>MEMORY_LIMIT:raise AssertionError('Declared memory budget exceeded')
        return False
    def progress(*args):
        nonlocal progress_count
        progress_count+=1
    items=[]
    for n in range(count):
        data=make_pdf('Synthetic load',[[f'Synthetic material {n}, page {p+1}'] for p in range(pages)])
        digest=sha256(data).hexdigest()
        items.append({'id':f'{n:032x}','title':f'Load {n}','identifier':f'Annex {n+1}',
            'path':f'Папка #/Annex {n+1}.pdf','data':data,'revision':digest,
            'source_map':[{'part':'original','source_sha256':digest,'source_page':p+1,'rect':None,'occurrence':1,'prepared_page':p+1} for p in range(pages)]})
    input_bytes=sum(len(i['data']) for i in items)
    files,mapping=volumes.build(items,{**volume_settings.DEFAULT,'enabled':True,'max_pages':600},stopped,progress)
    stopped();assert len(mapping['pages'])==count*pages
    return {'profile':name,'materials':count,'source_pages':count*pages,'seconds':round(time.monotonic()-start,3),
            'peak_rss_bytes':peak_memory(),'input_bytes':input_bytes,'output_bytes':sum(map(len,files.values())),
            'temporary_pdf_bytes':0,'volumes':len(files),'progress_events':progress_count,'passed':True,
            'budgets':{'seconds':budget,'peak_rss_bytes':MEMORY_LIMIT}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--profile',choices=PROFILES);a=parser.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    if a.profile:
        report=run(a.profile);(out/(a.profile+'.json')).write_text(json.dumps(report,indent=2),'utf-8');print(json.dumps(report));return
    for name,(_,_,seconds) in PROFILES.items():
        subprocess.run([sys.executable,'-B',__file__,'--profile',name,'--output',str(out)],check=True,timeout=seconds+60)


if __name__=='__main__':main()
