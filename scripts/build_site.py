"""Encrypted page builder for the Structured Notes Shelf.

  python scripts/build_site.py extract index.html prev.json   # decrypt the live page's data
  python scripts/build_site.py build data.json index.html     # render + encrypt a new page

Environment: NOTES_PASSWORD (required), REFRESH_TOKEN (optional; enables the Refresh button).
The password never ships. The token, when set, is placed only inside the encrypted page.
"""
import base64, json, os, re, sys
from datetime import datetime, timezone
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ITER = 600_000
REPO = os.environ.get('GITHUB_REPOSITORY', 'Jordan-Hum/notes-shelf')


def key(pw, salt):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pw.encode())


def extract(index_path, out_path, pw):
    h = open(index_path, encoding='utf-8').read()
    P = json.loads(re.search(r'const P = (\{.*?\});', h).group(1))
    inner = AESGCM(key(pw, base64.b64decode(P['s']))).decrypt(
        base64.b64decode(P['i']), base64.b64decode(P['c']), None).decode('utf-8')
    dec = json.JSONDecoder()
    m = re.search(r'const PAYLOAD = ', inner)
    if m:
        data, _ = dec.raw_decode(inner, m.end())
    else:  # first-generation page: a bare array
        m = re.search(r'const DATA = ', inner)
        arr, _ = dec.raw_decode(inner, m.end())
        data = {'meta': {}, 'notes': arr}
    json.dump(data, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'extracted {len(data["notes"])} notes from the live page')


def build(data_path, out_path, pw, token):
    payload = json.load(open(data_path, encoding='utf-8'))
    build_id = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    tpl = open(os.path.join(ROOT, 'site-src', 'template.html'), encoding='utf-8').read()
    gh = {'token': token or '', 'repo': REPO, 'workflow': 'refresh.yml', 'build': build_id}
    inner = (tpl.replace('__DATA__', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
                .replace('__GH__', json.dumps(gh)))
    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
           '<style>html,body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>'
           '</head><body>' + inner + '</body></html>')
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(key(pw, salt)).encrypt(iv, doc.encode('utf-8'), None)
    b = lambda x: base64.b64encode(x).decode()
    enc = json.dumps({'s': b(salt), 'i': b(iv), 'c': b(ct), 'n': ITER})
    gate = open(os.path.join(ROOT, 'site-src', 'gate.html'), encoding='utf-8').read()
    gate = gate.replace('__PAYLOAD__', enc).replace('__BUILD__', build_id)
    open(out_path, 'w', encoding='utf-8').write(gate)
    print(f'built {out_path} (build {build_id}, {len(payload["notes"])} notes, refresh button '
          f'{"on" if token else "off"})')


if __name__ == '__main__':
    pw = os.environ.get('NOTES_PASSWORD', '').strip()
    if not pw:
        sys.exit('NOTES_PASSWORD is not set')
    mode = sys.argv[1]
    if mode == 'extract':
        extract(sys.argv[2], sys.argv[3], pw)
    elif mode == 'build':
        build(sys.argv[2], sys.argv[3], pw, os.environ.get('REFRESH_TOKEN', '').strip())
    else:
        sys.exit('mode must be extract or build')
