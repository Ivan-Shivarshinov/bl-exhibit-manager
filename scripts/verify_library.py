"""Verify an already-running app with explicitly isolated synthetic data."""
import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from library_checks import verify,load_check

parser=argparse.ArgumentParser()
parser.add_argument('--url',required=True)
parser.add_argument('--data',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--incoming',type=Path)
parser.add_argument('--load',action='store_true')
args=parser.parse_args()

def request(path,body=None,raw=None):
    req=Request(args.url+path,data=json.dumps(body).encode() if body is not None else raw,headers={'X-Exhibit-Local':'1','Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=120) as r:
            content=r.read();return json.loads(content) if 'json' in r.headers.get('Content-Type','') else content
    except HTTPError as exc:raise AssertionError(path+': '+exc.read().decode(errors='replace')) from exc

print(json.dumps(verify(request,args.data,args.output,args.incoming),ensure_ascii=False))
if args.load:print(json.dumps(load_check(request,args.data,args.output),ensure_ascii=False))
