"""Configure a locally trusted certificate; does not change the OS trust store."""
import argparse
import json
from pathlib import Path
import ssl
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from exhibit.project import ROOT


def configure(root, cert, key, port):
    cert, key = Path(cert).resolve(), Path(key).resolve()
    if not 1024 <= port <= 65535 or port == 8765: raise ValueError('Выберите отдельный HTTPS-порт, например 8769.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    root.mkdir(parents=True, exist_ok=True)
    config = {'cert': str(cert), 'key': str(key), 'port': port}
    (root/'word-connection.json').write_text(json.dumps(config, indent=2), 'utf-8')
    origin = f'https://localhost:{port}'
    manifest = f'''<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="TaskPaneApp">
  <Id>43a6f2bd-d10f-4c7a-91b5-f3938c499ac4</Id><Version>0.2.0.0</Version><ProviderName>Buzko Legal</ProviderName><DefaultLocale>ru-RU</DefaultLocale>
  <DisplayName DefaultValue="BL Exhibit Manager"/><Description DefaultValue="Сноски и приложения локальной подачи"/>
  <Hosts><Host Name="Document"/></Hosts>
  <Requirements><Sets DefaultMinVersion="1.5"><Set Name="WordApi" MinVersion="1.5"/></Sets></Requirements>
  <DefaultSettings><SourceLocation DefaultValue="{escape(origin)}/word/index.html"/></DefaultSettings><Permissions>ReadWriteDocument</Permissions>
</OfficeApp>'''
    path = root/'BLExhibitManager.Word.xml'; path.write_text(manifest, 'utf-8')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cert', type=Path, default=Path.home()/'.office-addin-dev-certs/localhost.crt')
    parser.add_argument('--key', type=Path, default=Path.home()/'.office-addin-dev-certs/localhost.key')
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--data-dir', type=Path, default=ROOT)
    args = parser.parse_args()
    print(configure(args.data_dir, args.cert, args.key, args.port))
