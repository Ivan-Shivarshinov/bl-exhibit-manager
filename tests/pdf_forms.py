"""Artificial PDF fields and a CMS signature made with an ephemeral test certificate."""
from io import BytesIO
from datetime import datetime, timedelta, timezone
import re

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject,
                          ByteStringObject, TextStringObject)
from reportlab.pdfgen import canvas


def form_pdf(kind='form'):
    out = BytesIO(); c = canvas.Canvas(out,pagesize=(595,842))
    c.drawString(40,720,'Artificial PDF - original page')
    c.showPage(); c.drawString(40,720,'Artificial PDF - form page')
    c.acroForm.textfield(name='test-field',value='Visible test field',x=40,y=500,width=300,height=45,
                        fillColor=__import__('reportlab.lib.colors',fromlist=['green']).green)
    c.showPage(); c.save()
    w = PdfWriter(clone_from=PdfReader(BytesIO(out.getvalue())))
    field = w._root_object['/AcroForm']['/Fields'][0].get_object()
    if kind == 'empty':
        for page in w.pages: page.pop('/Annots',None)
        w._root_object[NameObject('/AcroForm')] = DictionaryObject({NameObject('/Fields'):w._add_object(ArrayObject())})
    elif kind == 'widget':
        w._root_object.pop('/AcroForm',None)
    elif kind in ('unsigned','signed','perms'):
        field[NameObject('/FT')] = NameObject('/Sig'); field.pop('/V',None)
        if kind in ('signed','perms'):
            signature = w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Sig'),
                NameObject('/Filter'):NameObject('/Adobe.PPKLite'),NameObject('/SubFilter'):NameObject('/adbe.pkcs7.detached'),
                NameObject('/ByteRange'):ArrayObject([NumberObject(0),NumberObject(9999999999),NumberObject(9999999999),NumberObject(9999999999)]),
                NameObject('/Contents'):ByteStringObject(b'\0'*8192),NameObject('/Reason'):TextStringObject('Synthetic regression test')}))
            field[NameObject('/V')] = signature
            if kind == 'perms':
                for page in w.pages: page.pop('/Annots',None)
                w._root_object.pop('/AcroForm',None)
                w._root_object[NameObject('/Perms')] = DictionaryObject({NameObject('/DocMDP'):signature})
    out = BytesIO(); w.write(out); data = out.getvalue()
    if kind not in ('signed','perms'): return data
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs7
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Artificial PDF regression')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
            .sign(key,hashes.SHA256()))
    contents = re.search(rb'/Contents\s*(<0{16384}>)',data)
    start,end = contents.start(1),contents.end(1)
    byte_range = f'[0 {start:010d} {end:010d} {len(data)-end:010d}]'.encode()
    data = re.sub(rb'\[ 0 9999999999 9999999999 9999999999 \]',byte_range+b'  ',data)
    assert len(byte_range)+2 == len(b'[ 0 9999999999 9999999999 9999999999 ]')
    signature = (pkcs7.PKCS7SignatureBuilder().set_data(data[:start]+data[end:]).add_signer(cert,key,hashes.SHA256())
        .sign(serialization.Encoding.DER,[pkcs7.PKCS7Options.DetachedSignature,pkcs7.PKCS7Options.Binary]))
    encoded = signature.hex().encode().ljust(16384,b'0')
    return data[:start+1]+encoded+data[end-1:]
