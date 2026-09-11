"""Load/approve/export 100 small fictional PDFs. Reports observed timings."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from zipfile import ZipFile
import json
from exhibit.project import Store
from exhibit.samples import make_pdf

with TemporaryDirectory() as temp:
    start=perf_counter()
    store=Store(temp);p=store.create("Нагрузка 100 учебных документов")
    for i in range(1,101):
        data=make_pdf(f"Учебный документ {i}", [[f"Вымышленный текст {i}."]])
        store.upload(p,f"LOAD-{i}.pdf",data)
        d=p["documents"][-1]
        store.update(p,d["id"],{"prefix":"LOAD","number":i,"folder":"LOAD"})
        store.approve(p,d["id"],"document")
    prepared=perf_counter()
    package=store.export(p)
    done=perf_counter()
    with ZipFile(BytesIO(package)) as z:
        assert len(z.namelist())==100
        assert all(z.read(name).startswith(b"%PDF") for name in z.namelist())
    print(json.dumps({"documents":100,"prepare_seconds":round(prepared-start,2),"export_seconds":round(done-prepared,2),"zip_bytes":len(package)}))
