"""Fetch current structured-note offerings from CIBC, National Bank, BMO and Scotiabank
and write one normalized JSON file.

Usage: python scripts/refresh.py OUT.json [PREVIOUS.json]

If a bank fails (network error, layout change, suspiciously few notes), its notes are
carried over from PREVIOUS.json and the bank is marked stale in the metadata.
Only counts are printed: the repository is public, so job logs must not contain note data.
"""
import json, re, sys, time, html
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import urllib.request, urllib.error

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')
TODAY = datetime.now(timezone(timedelta(hours=-5))).date()   # Toronto (EST)


def get(url, data=None, headers=None, tries=3, as_json=False):
    h = {'User-Agent': UA, 'Accept': 'application/json, text/html, */*'}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h, method='POST' if data is not None else 'GET')
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode('utf-8', 'replace')
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                break
            time.sleep((8 if e.code == 429 else 1.5) * (2 ** i))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f'GET failed: {url.split("?")[0]} ({last})')


def pmap(fn, items, workers=6):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def pnum(s):
    m = re.search(r'-?\d+(?:\.\d+)?', str(s or ''))
    return float(m.group()) if m else None


def iso(s):
    s = (s or '').strip()
    for fmt in ('%b %d, %Y', '%B %d, %Y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ''


def years(a, b, fallback):
    try:
        d = (date.fromisoformat(b) - date.fromisoformat(a)).days / 365.25
        return round(d * 2) / 2
    except Exception:  # noqa: BLE001
        return float(fallback or 0)


def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(str(s or ''))).strip()


def note(**k):
    base = dict(callThr=None, callNote='', cpnBarrier=None, opening='', issue='', status='',
                feats=[], fee=None, etc=None, freq='', callFreq='', rate=None, rateLabel='',
                barrier=None, mat='', closedFlag=False, docs=[], urlNote='')
    base.update(k)
    return base


# ------------------------------------------------------------------ CIBC
UND_ALIASES = {'Google': 'Alphabet Inc.', 'Dell': 'Dell Technologies Inc.', 'Oracle': 'Oracle Corporation',
               'Nvidia Corporation': 'NVIDIA Corporation', 'ServiceNow Inc': 'ServiceNow, Inc.',
               'Market Vectors Gold Miners': 'VanEck Gold Miners ETF (GDX)', 'Tesla Inc.': 'Tesla, Inc.',
               'Broadcom Inc': 'Broadcom Inc.', 'Meta': 'Meta Platforms, Inc.'}


def cibc():
    base = 'https://notes.cibc.com'
    lst = []
    for cat in ('PAR', 'PPN'):
        lst += get(f'{base}/api/v2/noteList/offerings/{cat}', as_json=True).get('noteColumns') or []
    dets = pmap(lambda n: get(f'{base}/api/v2/noteDetails/{n["dealId"]}', as_json=True), lst)
    out = []
    for n, d in zip(lst, dets):
        body = ((d or {}).get('note') or {}).get('body') or {}
        ov = (body.get('noteDetailTab1') or {}).get('overview') or {}
        P = {}
        for p in (ov.get('paraList1') or []) + (ov.get('paraList2') or []):
            P[clean(p.get('label')).rstrip(':').lower()] = clean(p.get('text'))
        S = {x.get('label'): x.get('value') for x in
             ((body.get('noteDetailTab1') or {}).get('summaryDetails') or {}).get('lvPairList1') or []}
        tbl = (ov.get('tableList1') or [None])[0]
        heads = [h.lower() for h in (tbl or {}).get('header', {}).get('heading', [])]
        rows = [r['col'] for r in (tbl or {}).get('row', [])]

        nm = clean(n.get('noteName')).removeprefix('CIBC ')
        st = n.get('structure') or ''
        t = {'Autocall Coupon': 'Contingent income', 'Autocall': 'Autocallable'}.get(st, 'Growth')
        protected = n.get('noteCategory') == 'PPN'
        prot = P.get('contingent principal protection') or P.get('downside exposure') or ''
        bar = re.search(r'(-\d+(?:\.\d+)?)%', prot)
        cf = P.get('cash flow') or P.get('potential variable interest') or ''
        fq = re.search(r'\b(Monthly|Quarterly|Semi-Annual|Semi-annual|Annual)\b', cf, re.I)
        cb = re.search(r'(-\d+(?:\.\d+)?)%', cf)
        call = P.get('call feature') or ''
        ct = re.search(r'greater than or equal to (-?\d+(?:\.\d+)?)%', call)
        cs = re.search(r'starting (?:in|on) ([A-Z][a-z]+ \d{4})', call)
        call_thr, call_note = (float(ct.group(1)) if ct else None), ''
        if call_thr is None and any('threshold' in h for h in heads):
            i = next(i for i, h in enumerate(heads) if 'threshold' in h)
            vals = [pnum(r[i]) for r in rows if len(r) > i]
            if vals:
                call_thr = vals[-1]
                if vals[0] != vals[-1]:
                    call_note = f'{vals[0]:g}% at the first call date ({rows[0][0]}), {vals[-1]:g}% after'
        rate = pnum(n.get('autocallableAnnualizedCoupon'))
        rate_label = {'Contingent income': 'Coupon p.a.', 'Autocallable': 'Call return p.a.'}.get(t, '')
        if t == 'Growth':
            rate_label = 'Boost return' if 'Boost' in nm else ''
            if 'Boost' not in nm:
                rate = None
        feats = []
        if re.search('buffer', nm + prot, re.I): feats.append('Buffer: losses only beyond the buffer level')
        if 'Memory' in nm: feats.append('Memory coupon (missed coupons can be paid later)')
        if 'Enhanced' in nm and t == 'Autocallable': feats.append('Enhanced autocall')
        if rows and t == 'Autocallable': feats.append(f'Final call/maturity return {rows[-1][0]}: {rows[-1][-1]}')
        extra = P.get('outperformance potential') or ('' if st else (P.get('potential variable interest') or ''))
        if extra: feats.append(extra[:220])
        if t == 'Contingent income': cfreq = f'Monthly from {cs.group(1)}' if cs else 'Monthly'
        elif t == 'Autocallable': cfreq = 'Annual'
        else: cfreq = ''
        und = clean(P.get('reference portfolio'))
        mat = iso(n.get('maturityDate'))
        out.append(note(
            bank='CIBC', code=n.get('fundServ'), name='CIBC ' + nm,
            prot='Protected' if protected else 'At risk', type=t, und=UND_ALIASES.get(und, und),
            cur=n.get('currency'), f=n.get('fClass') == '1' or 'F-Class' in nm,
            term=years(iso(n.get('issueDate')), mat, n.get('term')), close=iso(n.get('availableUntil')), mat=mat,
            issue=iso(n.get('issueDate')), rate=rate, rateLabel=rate_label,
            freq=fq.group(1).title() if (fq and t == 'Contingent income') else '',
            barrier=None if protected else (float(bar.group(1)) if bar else None),
            cpnBarrier=float(cb.group(1)) if (cb and t == 'Contingent income') else None,
            callThr=call_thr, callNote=call_note or ('' if call_thr is not None or t not in ('Autocallable', 'Contingent income') else 'See pricing supplement'),
            callFreq=cfreq, fee=pnum(P.get('selling concession')), etc=pnum(S.get('Current ETC')),
            feats=feats, url=f'{base}/productdetail/{n["dealId"]}'))
    return out


# ------------------------------------------------------------------ National Bank
NBC_PAGE = 'https://cloud.messagebnc.nbc.ca/en-notes-current-offer?category=F'


def _js_str(block, key):
    m = re.search(key + r':\s*\{\s*(?:id:\s*"[^"]*",\s*)?en:\s*"((?:[^"\\]|\\.)*)"', block)
    if not m:
        return ''
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda x: chr(int(x.group(1), 16)), m.group(1))
    s = s.replace('\\n', ' ').replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')
    return clean(s)


def _js_date(block, key):
    m = re.search(key + r":\s*new Date\('(\d+)/(\d+)/(\d+)", block)
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat() if m else ''


def _js_val(block, key):
    m = re.search(key + r""":\s*['"]?([^'",\s}]+)""", block)
    return m.group(1) if m else ''


def nbc():
    page = get(NBC_PAGE)
    blocks = re.split(r'\bproductID:\s*', page)[1:]
    out, seen = [], set()
    for b in blocks:
        pid = re.match(r'(\d+)', b).group(1)
        b = b[:6000]
        if _js_val(b, 'category') != 'F':
            continue
        code = _js_val(b, 'fundservCode')
        if not code or code in seen:
            continue
        seen.add(code)
        name = _js_str(b, 'name')
        spec = _js_str(b, 'specSummary')
        upside = _js_str(b, 'upside').replace('▪', '').strip()
        ptype = _js_val(b, 'productType')
        stype = re.search(r'structureType:\s*\{\s*id:\s*"([^"]+)"', b)
        stype = stype.group(1) if stype else ''
        protected = ptype != 'NPPN'
        und = _js_str(b, 'underlying')
        start, end, issue = _js_date(b, 'startOfSale'), _js_date(b, 'endOfSale'), _js_date(b, 'issuanceDate')
        if end and date.fromisoformat(end) < TODAY:   # closed offerings linger in the page source
            continue
        term = float(_js_val(b, 'term') or 0)
        sold_out = _js_val(b, 'soldOut') == 'true'
        cthr_raw = _js_val(b, 'callThreshold')
        feats = []
        rate = None; rate_label = ''; freq = ''; cpnb = None; cthr = None; cnote = ''; cfreq = ''
        if stype == 'ACCI' or 'Contingent Income' in name:
            t = 'Contingent income'
            m = re.search(r'(\d+(?:\.\d+)?)% coupon', spec)
            rate = float(m.group(1)) if m else None; rate_label = 'Coupon p.a.'; freq = 'Monthly'
            m = re.search(r'(-\d+(?:\.\d+)?)% threshold', spec); cpnb = float(m.group(1)) if m else None
        elif 'Auto Callable' in name or stype == 'AC':
            t = 'Autocallable'
            rates = re.findall(r'(\d+(?:\.\d+)?)%', (re.search(r'callable at ([^,]+)', spec) or re.search('(.*)', upside)).group(1))
            rate = float(rates[0]) if rates else None; rate_label = 'Call return p.a.'
            if rates: feats.append(f'Final call/maturity return {rates[-1]}%')
            m = re.search(r'(\d+(?:\.\d+)?)% participation factor', spec)
            if m: feats.append(f'{m.group(1)}% participation above the call return; unlimited upside')
        elif ptype == 'CPR' or re.search(r'Coupon|Accrual', name):
            t = 'Fixed income'
            m = re.search(r'(\d+(?:\.\d+)?)% coupon', spec)
            rate = float(m.group(1)) if m else None; rate_label = 'Coupon p.a.'
            freq = 'Quarterly' if 'quarterly' in spec.lower() else 'Annual'
            if 'xtendible' in spec or 'Extendible' in name: cfreq = 'Extendible (issuer)'
            feats.append(spec.split(' | ')[0])
        else:
            t = 'Growth'
            m = re.search(r'\((\d+(?:\.\d+)?)% annualized\)', upside)
            if m: rate = float(m.group(1)); rate_label = 'Boost return p.a.'
            feats.append(spec.split(' | ')[0])
        if t in ('Autocallable', 'Contingent income'):
            m = re.search(r'(\d+(?:\.\d+)?)% call thresh\.(?: \(starting after (\d+) months\))?', spec)
            if m:
                cthr = float(m.group(1))
                cfreq = ('Monthly' if t == 'Contingent income' else 'Annual') + (f' after {m.group(2)} mo' if m.group(2) else '')
            elif cthr_raw not in ('', 'null'):
                cthr = round(float(cthr_raw) * 100, 2)
                cfreq = 'Monthly' if t == 'Contingent income' else 'Annual'
            else:
                cnote = 'See pricing supplement'
        bar = re.search(r'Barrier: (-\d+(?:\.\d+)?)%', spec)
        if start and date.fromisoformat(start) > TODAY: status = 'Opens ' + date.fromisoformat(start).strftime('%b %-d')
        elif end and date.fromisoformat(end) == TODAY: status = 'Closing today'
        else: status = ''
        if sold_out: status = 'Sold out'
        mat = ''
        if issue and term:
            d0 = date.fromisoformat(issue)
            mat = d0.replace(year=d0.year + int(term)).isoformat() if term == int(term) else ''
        out.append(note(
            bank='National Bank', code=code, name='NBC ' + name, prot='Protected' if protected else 'At risk',
            type=t, und=und or 'n/a', cur=_js_val(b, 'currency'), f=True, term=term, close=end, mat=mat,
            rate=rate, rateLabel=rate_label, freq=freq, barrier=None if protected else (float(bar.group(1)) if bar else None),
            cpnBarrier=cpnb, callThr=cthr, callNote=cnote, callFreq=cfreq, fee=pnum(_js_val(b, 'commission')),
            status=status, feats=feats, opening=start if start and date.fromisoformat(start) > TODAY else '',
            issue=issue, closedFlag=sold_out, url=NBC_PAGE, _pid=pid, docs=_nbc_docs(b)))

    def info(n):
        try:
            return get(f'https://app.nbcstructuredsolutions.ca/api/v1/public/products/{n["_pid"]}',
                       as_json=True, tries=2) or {}
        except Exception:  # noqa: BLE001
            return {}
    for n, j in zip(out, pmap(info, out)):
        if j.get('productId'):
            # published product page for this note; upcoming notes don't have one yet
            n['url'] = f'https://app.nbcstructuredsolutions.ca/products/{n["_pid"]}?lang=en'
            n['mat'] = j.get('maturityDate') or n['mat']
        else:
            n['urlNote'] = "National Bank's current offer list (this note's own page isn't published yet)"
            n['docs'] = []
        n.pop('_pid', None)
    return out


def _nbc_docs(block):
    docs = []
    for key, label in (('link', 'Pricing supplement'), ('clientSummary', 'Client summary')):
        m = re.search(key + r"\s*:\s*\{\s*en:\s*'(https://[^']+\.pdf)'", block)
        if m:
            docs.append([label, m.group(1)])
    return docs


# ------------------------------------------------------------------ BMO
def _bmo_detail(code):
    page = get(f'https://www.bmonotes.com/Note/{code}')
    P = {}
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)
        if len(cells) == 2:
            P[clean(re.sub('<[^>]+>', ' ', cells[0]))] = clean(re.sub('<[^>]+>', ' ', cells[1]))
    return P


def bmo():
    rows = get('https://www.bmonotes.com/TypeAjax/GetSelling/', data=b'',
               headers={'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded'},
               as_json=True)
    cands = []
    for r in rows:
        cat = ((r.get('productCategory') or {}).get('en') or '')
        code = r.get('cmdb') or ''
        if not code.startswith('JHN') or not re.search('Principal', cat):
            continue
        if r.get('noteStateEnum') != 1:      # 1 = currently selling (the feed holds ~19k historical notes)
            continue
        cands.append((code, clean((r.get('name') or {}).get('en')), cat))
    cands = list({c[0]: c for c in cands}.values())
    dets = pmap(lambda c: _bmo_detail(c[0]), cands)
    out = []
    for (code, nm, cat), P in zip(cands, dets):
        if P.get('Current Status') and 'Selling' not in P['Current Status']:
            continue
        if not P.get('Available Until'):
            continue
        close = iso(P.get('Available Until'))
        if close and date.fromisoformat(close) < TODAY:
            continue
        nm = nm.removeprefix('BMO ')
        protected = 'Protected' in cat
        ptype = P.get('Product Type', '')
        feats = []
        rate = None; rate_label = ''; freq = ''; cpnb = None; cthr = None; cfreq = ''
        lvl = pnum(P.get('AutoCall Level'))
        if 'AutoCall' in ptype or 'AutoCallable' in nm:
            t = 'Autocallable'
            cfq = P.get('Callable Frequency', '')
            rate = pnum(P.get('AutoCall Coupon (Next Call Date)')) if not protected else pnum(P.get('Contingent Coupon'))
            if rate is not None and cfq == 'Semi-Annual': rate *= 2
            if rate is not None and cfq == 'Quarterly': rate *= 4
            rate_label = 'Call return p.a.'
            cthr = lvl - 100 if lvl else (pnum(P.get('Coupon Knock-Out Level')) if protected else None)
            cfreq = cfq or 'Annual'
            ex = pnum(P.get('Excess Participation'))
            if ex and ex > 5: feats.append(f'{ex:g}% participation above the call level (unlimited upside)')
        elif ptype == 'Income' or 'Contingent Income' in nm:
            t = 'Contingent income'
            rate = pnum(P.get('Contingent Coupon')); rate_label = 'Coupon p.a.'; freq = P.get('Pay Frequency', '')
            cpnb = pnum(P.get('Coupon Knock-Out Level')); cthr = lvl - 100 if lvl else None
            if 'AR Version' in nm: feats.append('Adjusted-return (AR) version of the stock')
        else:
            t = 'Growth'
            if P.get('Upside Participation'): feats.append(f"{P['Upside Participation']} upside participation")
            if P.get('Boosted Return'):
                feats.append(f"Booster: {P['Boosted Return']} return if the index is between {P.get('Booster Range','')}; "
                             f"{P.get('Upside Participation (above Booster Range)','')} participation above")
            if P.get('Absolute Participation'): feats.append(f"Twin Win: {P['Absolute Participation']} absolute return participation within the barrier")
            if P.get('Minimum Payment'): feats.append(f"Minimum payment {P['Minimum Payment']} per note")
        bar = pnum(P.get('Barrier Protection'))
        if P.get('Buffer Protection'):
            bar = pnum(P['Buffer Protection'])
            dp = P.get('Downside Participation', '')
            feats.append(f"Buffer: no loss down to {bar:g}%" + (f"; losses beyond that at {dp} downside participation" if dp else ''))
        f = 'F-Class' in nm
        mat = iso(P.get('Maturity Date'))
        out.append(note(
            bank='BMO', code=code, name='BMO ' + nm, prot='Protected' if protected else 'At risk', type=t,
            und=P.get('Linked To', ''), cur=P.get('Currency', ''), f=f, term=pnum(P.get('Term')) or 0,
            close=close, mat=mat, issue=iso(P.get('Issue Date')), rate=round(rate, 2) if rate is not None else None,
            rateLabel=rate_label, freq=freq, barrier=None if protected else bar, cpnBarrier=cpnb,
            callThr=cthr, callFreq=cfreq, fee=0.0 if f else None, etc=pnum(P.get('Current ETC')), feats=feats,
            url=f'https://www.bmonotes.com/Note/{code}'))
    return out


# ------------------------------------------------------------------ Scotiabank
SC_API = 'https://api.scotiabank.com/investorsolutions/issue/v1.0'


def scotia():
    items = []
    for ty in ('PAR', 'PPN', 'MLGIC', 'FIN'):
        try:
            lst = get(f'{SC_API}/issue-list?issueStatus=Current&language=English&productType={ty}', as_json=True) or []
        except RuntimeError as e:
            if '404' in str(e):       # no current issues of this type
                continue
            raise
        items += [(x.get('issueId'), ty) for x in lst if x.get('issueId')]

    def det(it):
        time.sleep(1.0)
        return (get(f'{SC_API}?issueId={it[0]}&language=English&productType={it[1]}', as_json=True, tries=5) or [{}])[0]
    dets = [det(it) for it in items]     # one at a time: this API rate-limits
    out = []
    for (iid, ty), o in zip(items, dets):
        if not o or not o.get('fundServCode'):
            continue
        close = o.get('availableUntil') or ''
        if close and date.fromisoformat(close) < TODAY:
            continue
        protected = ty in ('PPN', 'MLGIC')
        osc, cat = o.get('oscNoteType') or '', o.get('category') or ''
        if cat.startswith('Callable Contingent') or (osc == 'Income' and o.get('paymentBarrier')):
            t = 'Contingent income'
        elif osc == 'Autocallable' or 'Autocallable' in cat:
            t = 'Autocallable'
        elif osc == 'Income':
            t = 'Fixed income'
        else:
            t = 'Growth'
        ev = o.get('eventSchedule') or []
        calls = [e for e in ev if e.get('callBarrier') is not None]
        feats = []
        basket = o.get('basketAssetNames')
        if basket: feats.append(f'Basket: {clean(basket)}')
        rate = None; rate_label = ''
        cf = (o.get('callFrequency') or '').replace('Annually', 'Annual').replace('Semi-Annually', 'Semi-annual')
        per_year = {'Annual': 1, 'Semi-annual': 2, 'Quarterly': 4, 'Monthly': 12}.get(cf, 1)
        part = o.get('participationRate') or 0
        if t == 'Contingent income':
            rate = o.get('paymentAmount'); rate_label = 'Coupon p.a.'
        elif t == 'Autocallable':
            if calls:
                rate = round(calls[0].get('paymentAmount', 0) * per_year, 2); rate_label = 'Call return p.a.'
                feats.append(f"Final call/maturity return {calls[-1].get('paymentAmount'):g}%")
            if part: feats.append(f'{part:g}% participation above the call return')
        elif t == 'Fixed income':
            rate = o.get('paymentAmount'); rate_label = 'Coupon p.a.'
            if part: feats.append(f"Fixed {rate:g}% coupon plus {part:g}% participation in index growth")
        else:
            if o.get('booster'): feats.append(f"Booster {o['booster']:g}% (booster start at {o.get('boosterStart', 0):g}% of initial level)")
            if part: feats.append(f'{part:g}% participation in growth' + (f", capped at {o['maximumReturn']:g}% total return" if o.get('maximumReturn') else ''))
        fc = calls[0]['valuationDate'] if calls else ''
        fc_txt = datetime.strptime(fc, '%Y-%m-%d').strftime('%b %Y') if fc else ''
        lvl = o.get('autocallLevel') or 0
        bar = o.get('barrierLevel') or 0
        buf = o.get('bufferLevel') or 0
        if buf: feats.append(f'Buffer at {buf - 100:g}%')
        pay_bar = o.get('paymentBarrier') or 0
        out.append(note(
            bank='Scotiabank', code=o['fundServCode'], name=clean(o.get('noteName')),
            prot='Protected' if protected else 'At risk', type=t, und=clean(o.get('underlyingAsset')),
            cur=o.get('currency'), f=(o.get('classType') or '').startswith('F'), term=float(o.get('term') or 0),
            close=close, mat=o.get('maturityDate') or '', issue=o.get('issueDate') or '',
            rate=rate if rate else None, rateLabel=rate_label if rate else '',
            freq=(o.get('paymentFrequency') or '') if t in ('Contingent income', 'Fixed income') else '',
            barrier=None if protected else ((bar or buf) - 100 if (bar or buf) else None),
            cpnBarrier=(pay_bar - 100) if (t == 'Contingent income' and pay_bar) else None,
            callThr=(lvl - 100) if lvl else None, callFreq=(f'{cf} from {fc_txt}' if fc_txt else cf) if lvl else '',
            fee=o.get('sellingConcession'), etc=o.get('currentETC') or None, feats=feats,
            url=f'https://www.gbm.scotiabank.com/en/services/investor-solutions/current-issues/detail.en.{iid}.{ty}.html'))
    return out


# ------------------------------------------------------------------ derived fields
def group(u):
    ul = u.lower()
    if re.search(r'bank', ul): return 'Banks'
    if re.search(r'nuclear|energy|pipeline|xeg', ul): return 'Energy'
    if 'utilit' in ul or 'xlu' in ul: return 'Utilities'
    if re.search(r'insurance|asset manager|financials', ul): return 'Financials (other)'
    if 'gold' in ul or 'gdx' in ul: return 'Gold miners'
    if 'consumer staples' in ul or 'xlp' in ul: return 'Consumer staples'
    if 'healthcare' in ul: return 'Healthcare'
    if 'reit' in ul: return 'Real estate'
    if 'industrial' in ul: return 'Industrials'
    if 'index basket' in ul: return 'Multi-sector basket'
    if re.search(r'large cap|large capitalizations|tsx 60|s&p 500|xsp|xiu|stoxx 50|eurozone 50|diversified|500|qqq|top 30|top 50|blue chip|dia\b', ul): return 'Broad market'
    if 'corra' in ul or ul in ('n/a', ''): return 'Rates / fixed'
    if 'brazil' in ul: return 'Emerging markets'
    return 'Single stock'


US = (r'\bus\b|u\.s\.|united states|s&p 500|xsp|xlu|xlp|spy|dia\b|qqq|broadcom|dell|marvell|meta|micron|nvidia|oracle|'
      r'tesla|dollar tree|intuit|seagate|space exploration|asset manager|alphabet|crowdstrike|servicenow|netflix|intel|'
      r'ge vernova|gold miners etf|nuclear|morningstar us|shopify')


def region(n):
    u = (n['und'] + ' ' + n['name']).lower()
    if re.search(r'euro|stoxx|merqube', u): return 'Europe'
    if 'brazil' in u: return 'Emerging'
    if 'shopify' in u or 'brookfield' in u: return 'Canada'
    if re.search(US, u): return 'US'
    return 'Canada'


MIN_EXPECTED = {'CIBC': 20, 'National Bank': 10, 'BMO': 20, 'Scotiabank': 10}


def main():
    out_path = sys.argv[1]
    prev = []
    if len(sys.argv) > 2:
        try:
            prev = json.load(open(sys.argv[2], encoding='utf-8')).get('notes', [])
        except Exception:  # noqa: BLE001
            prev = []
    meta = {'asof': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'banks': {}}
    notes = []
    for bank, fn in (('CIBC', cibc), ('National Bank', nbc), ('BMO', bmo), ('Scotiabank', scotia)):
        try:
            got = fn()
            if len(got) < MIN_EXPECTED[bank]:
                raise RuntimeError(f'only {len(got)} notes found')
            notes += got
            meta['banks'][bank] = {'ok': True, 'count': len(got), 'asof': meta['asof']}
            print(f'{bank}: {len(got)} notes')
        except Exception as e:  # noqa: BLE001
            old = [n for n in prev if n.get('bank') == bank]
            notes += old
            meta['banks'][bank] = {'ok': False, 'count': len(old), 'error': str(e)[:160]}
            print(f'{bank}: FAILED ({str(e)[:160]}); kept {len(old)} notes from the previous refresh')
    for n in notes:
        n['sector'] = group(n['und'])
        n['region'] = region(n)
    json.dump({'meta': meta, 'notes': notes}, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    # coverage summary (counts only)
    miss_call = sum(1 for n in notes if n['type'] in ('Autocallable', 'Contingent income') and n['callThr'] is None)
    miss_cpn = sum(1 for n in notes if n['type'] == 'Contingent income' and n['cpnBarrier'] is None)
    print(f'total {len(notes)} | missing call threshold {miss_call} | missing coupon threshold {miss_cpn}')
    if not any(b['ok'] for b in meta['banks'].values()):
        sys.exit('All banks failed; not publishing.')


if __name__ == '__main__':
    main()
