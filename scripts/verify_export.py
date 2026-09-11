"""Create a reviewed fictional export and unpack it at a different location for Word QA."""
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
from zipfile import ZipFile
from exhibit.project import Store
from exhibit.samples import demo_project, files

with TemporaryDirectory() as temp:
    store=Store(temp)
    p=demo_project(store)
    data=files()
    store.upload(p,"RLA-99.pdf",data["RLA-99.pdf"])
    store.update(p,p["documents"][-1]["id"],{"prefix":"RLA","number":99,"folder":"RLA"})
    for d in p["documents"]:
        if d["translation"]:store.approve(p,d["id"],"translation")
        store.approve(p,d["id"],"document")
    store.scan(p);store.confirm_links(p)
    package=store.export(p)
    target=Path("output/verified");target.mkdir(parents=True,exist_ok=True)
    (target/"Submission.zip").write_bytes(package)
    relocated=Path("output/relocated");relocated.mkdir(parents=True,exist_ok=True)
    with ZipFile(BytesIO(package)) as z:
        z.extractall(relocated)  # generated, validated archive; never arbitrary user ZIP input
    print((relocated/"Submission/Main document.docx").resolve())
