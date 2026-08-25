"""Author Word tracked changes and threaded comment replies in a .docx.

Usage:  python3 docx_markup.py <edits_module> <source.docx> <out.docx> "<Author Name>"

`<edits_module>` is a Python file exposing `apply(G)`, where G carries the helpers this
module builds: paras/ptext/find_para, edit_run/edit_run_must/edit_run_deep, sub-style
helpers, append_ins/insert_para_after/delete_para, and add_comment/reply/resolve.

Always rebuilds from the source, so it is safe to re-run after editing the edit list.
Stdlib only: python-docx cannot write tracked changes or comments.

See references/docx-markup.md for the four traps this handles, all of which yield a
file that LibreOffice and pandoc open happily and Word rejects or renders wrong.
"""
import copy, re, shutil, zipfile, os, sys
import xml.etree.ElementTree as ET

EDITS_MODULE = sys.argv[1]; SRC = sys.argv[2]; OUT = sys.argv[3]
WORK = os.path.join(os.path.dirname(OUT), "_work")
AUTHOR = sys.argv[4] if len(sys.argv) > 4 else "Sponsor"
DATE = os.environ.get("DOCX_MARKUP_DATE", "2026-01-01T12:00:00Z")

W  = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W14= 'http://schemas.microsoft.com/office/word/2010/wordml'
W15= 'http://schemas.microsoft.com/office/word/2012/wordprocessingDrawing'
W15C='http://schemas.microsoft.com/office/word/2012/wordml'
W16CID='http://schemas.microsoft.com/office/word/2016/wordml/cid'
W16CEX='http://schemas.microsoft.com/office/word/2018/wordml/cex'
def q(t, ns=W): return '{%s}%s' % (ns, t)

shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK)
with zipfile.ZipFile(SRC) as z:
    names = z.namelist(); z.extractall(WORK)

# --- namespace preservation -------------------------------------------------
# ElementTree only re-declares namespaces it sees in use, and rewrites any
# unregistered URI as ns0:.  Word rejects both: a dropped declaration leaves
# mc:Ignorable pointing at an undeclared prefix.  So register every prefix
# from each root start tag up front, and splice the original root tag back on
# write so all declarations survive verbatim.
TOUCHED = ['word/document.xml', 'word/comments.xml', 'word/commentsExtended.xml',
           'word/commentsIds.xml', 'word/commentsExtensible.xml']

def root_tag(path):
    raw = open(path, encoding='utf-8').read()
    m = re.match(r'\s*<\?xml[^>]*\?>\s*', raw)
    start = m.end() if m else 0
    j = raw.index('>', start)
    while raw.count('"', start, j) % 2 == 1:
        j = raw.index('>', j + 1)
    return raw[start:j + 1]

ROOTTAG = {}
for rel in TOUCHED:
    path = os.path.join(WORK, rel)
    ROOTTAG[rel] = root_tag(path)
    # scan the WHOLE file: DrawingML (a, a14, asvg, pic) is declared on inner
    # elements, not the root, and an unregistered URI is re-emitted as nsN:.
    whole = open(path, encoding='utf-8').read()
    for pfx, uri in re.findall(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"', whole):
        ET.register_namespace(pfx, uri)

doc = ET.parse(os.path.join(WORK,'word/document.xml'))
droot = doc.getroot()
body = droot.find(q('body'))
paras = list(body.iter(q('p')))
def ptext(p): return "".join(n.text or "" for n in p.iter(q('t')))

_parent_of_comment = {}
_rev = [1000]
def rid():
    _rev[0] += 1; return str(_rev[0])
def revattrs(el):
    el.set(q('id'), rid()); el.set(q('author'), AUTHOR); el.set(q('date'), DATE)

def mkrun(text, template=None):
    r = ET.Element(q('r'))
    if template is not None:
        rpr = template.find(q('rPr'))
        if rpr is not None:
            rpr = copy.deepcopy(rpr)
            for h in rpr.findall(q('highlight')): rpr.remove(h)
            r.append(rpr)
    t = ET.SubElement(r, q('t')); t.text = text
    t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    return r

def runs_of(p): return [c for c in list(p) if c.tag == q('r')]

def find_para(pred, start=0):
    for i in range(start, len(paras)):
        if pred(ptext(paras[i])): return i
    raise SystemExit("paragraph not found: %r" % pred)

def edit_run(p, old, new):
    """Track-change a run whose text == old (or contains it) -> new. new=None deletes."""
    for idx, r in enumerate(list(p)):
        if r.tag != q('r'): continue
        txt = "".join(n.text or "" for n in r.iter(q('t')))
        if txt != old: continue
        pos = list(p).index(r)
        p.remove(r)
        dele = ET.Element(q('del')); revattrs(dele)
        dr = copy.deepcopy(r)
        for t in dr.iter(q('t')):
            t.tag = q('delText'); t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
        dele.append(dr)
        p.insert(pos, dele)
        if new is not None:
            ins = ET.Element(q('ins')); revattrs(ins)
            ins.append(mkrun(new, r))
            p.insert(pos+1, ins)
        return True
    return False

def edit_run_deep(p, old, new):
    """Like edit_run but finds runs nested inside sdt/content controls."""
    for holder in [p] + [el for el in p.iter() if el.tag != q('r')]:
        for r in list(holder):
            if r.tag != q('r'): continue
            txt = "".join(n.text or "" for n in r.iter(q('t')))
            if txt != old: continue
            pos = list(holder).index(r); holder.remove(r)
            d = ET.Element(q('del')); revattrs(d)
            dr = copy.deepcopy(r)
            for t in dr.iter(q('t')):
                t.tag = q('delText'); t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
            d.append(dr); holder.insert(pos, d)
            if new is not None:
                i = ET.Element(q('ins')); revattrs(i); i.append(mkrun(new, r)); holder.insert(pos+1, i)
            return True
    return False

def edit_run_must(p, old, new, label=""):
    if not edit_run(p, old, new):
        raise SystemExit("run not found %r in %r  [%s]" % (old, ptext(p)[:90], label))

def append_ins(p, text):
    """Append inserted text to end of paragraph, styled like its last run."""
    rs = [c for c in list(p) if c.tag == q('r')]
    tmpl = rs[-1] if rs else None
    ins = ET.Element(q('ins')); revattrs(ins)
    ins.append(mkrun(text, tmpl))
    p.append(ins)

def insert_para_after(p, text):
    """Insert a wholly new paragraph (marked inserted) after p."""
    parent = parent_of(p)
    new = ET.Element(q('p'))
    ppr_src = p.find(q('pPr'))
    ppr = copy.deepcopy(ppr_src) if ppr_src is not None else ET.Element(q('pPr'))
    if ppr.tag != q('pPr'): ppr = ET.Element(q('pPr'))
    rpr = ppr.find(q('rPr'))
    if rpr is None:
        rpr = ET.SubElement(ppr, q('rPr'))
    ins_mark = ET.Element(q('ins')); revattrs(ins_mark)
    rpr.insert(0, ins_mark)
    new.append(ppr)
    rs = [c for c in list(p) if c.tag == q('r')]
    ins = ET.SubElement(new, q('ins')); revattrs(ins)
    ins.append(mkrun(text, rs[-1] if rs else None))
    parent.insert(list(parent).index(p)+1, new)
    return new

def delete_para(p):
    """Track-delete an entire paragraph, including its paragraph mark."""
    for r in list(p):
        if r.tag != q('r'): continue
        txt = "".join(n.text or "" for n in r.iter(q('t')))
        if txt.strip(): edit_run(p, txt, None)
    ppr = p.find(q('pPr'))
    if ppr is None:
        ppr = ET.Element(q('pPr')); p.insert(0, ppr)
    rpr = ppr.find(q('rPr'))
    if rpr is None: rpr = ET.SubElement(ppr, q('rPr'))
    d = ET.Element(q('del')); revattrs(d); rpr.insert(0, d)

_parentmap = {c: par for par in droot.iter() for c in par}
def parent_of(el): return _parentmap[el]

# ---------------- comments ----------------
ctree = ET.parse(os.path.join(WORK,'word/comments.xml')); croot = ctree.getroot()
extree = ET.parse(os.path.join(WORK,'word/commentsExtended.xml')); exroot = extree.getroot()
idstree = ET.parse(os.path.join(WORK,'word/commentsIds.xml')); idsroot = idstree.getroot()
cextree = ET.parse(os.path.join(WORK,'word/commentsExtensible.xml')); cexroot = cextree.getroot()

existing_ids = [int(c.get(q('id'))) for c in croot]
_cid = [max(existing_ids)]
_pid = [0x50000000]
def new_cid():
    _cid[0] += 1; return str(_cid[0])
def new_paraid():
    _pid[0] += 7; return "%08X" % _pid[0]

# map comment id -> paraId (for resolving)
cid2para = {}
for c in croot:
    ps = list(c.iter(q('p')))
    if ps:
        pid = ps[-1].get(q('paraId', W14))
        if pid: cid2para[c.get(q('id'))] = pid

def _mkcomment(text, after_cid=None):
    """Create the comments.xml entry; returns (cid, paraId).

    A thread's comments must sit together and in order in comments.xml, so a
    reply is placed after its parent (and after any replies already there)
    rather than appended at the end of the file.
    """
    cid = new_cid(); pid = new_paraid()
    c = ET.Element(q('comment'))
    if after_cid is None:
        croot.append(c)
    else:
        kids = list(croot)
        at = max(i for i, k in enumerate(kids)
                 if k.get(q('id')) == after_cid or _parent_of_comment.get(k.get(q('id'))) == after_cid)
        croot.insert(at + 1, c)
    c.set(q('id'), cid); c.set(q('author'), AUTHOR)
    c.set(q('date'), DATE); c.set(q('initials'), 'SQ')
    cp = ET.SubElement(c, q('p')); cp.set(q('paraId', W14), pid)
    cppr = ET.SubElement(cp, q('pPr')); ET.SubElement(cppr, q('pStyle')).set(q('val'),'CommentText')
    r = ET.SubElement(cp, q('r'))
    rpr = ET.SubElement(r, q('rPr')); ET.SubElement(rpr, q('rStyle')).set(q('val'),'CommentReference')
    ET.SubElement(r, q('annotationRef'))
    r2 = ET.SubElement(cp, q('r')); t = ET.SubElement(r2, q('t')); t.text = text
    t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    durable = "%08X" % (0x60000000 + int(cid))
    # commentsIds is w16cid, not w15 -- a wrong prefix here silently breaks the
    # comment's identity, and with it Word's threading.
    ci = ET.SubElement(idsroot, q('commentId', W16CID))
    ci.set(q('paraId', W16CID), pid); ci.set(q('durableId', W16CID), durable)
    # every comment also needs a commentsExtensible entry keyed on the same
    # durableId; omit it and Word treats the comment as unrecognised.
    cx = ET.SubElement(cexroot, q('commentExtensible', W16CEX))
    cx.set(q('durableId', W16CEX), durable); cx.set(q('dateUtc', W16CEX), DATE)
    return cid, pid

def _refrun(cid):
    rr = ET.Element(q('r'))
    rpr = ET.SubElement(rr, q('rPr')); ET.SubElement(rpr, q('rStyle')).set(q('val'),'CommentReference')
    ET.SubElement(rr, q('commentReference')).set(q('id'), cid)
    return rr

def reply(parent_cid, text, done=False):
    """Post a threaded reply inside an existing comment's thread.

    Word threads on w15:paraIdParent, but it only renders the reply inside the
    parent when the two share an anchor range exactly and the parent's
    commentReference comes first. Word's own layout is:

        <commentRangeStart parent/><commentRangeStart reply/>
        ...the annotated text...
        <commentRangeEnd parent/><r><commentReference parent/></r>
        <commentRangeEnd reply/><r><commentReference reply/></r>
    """
    parent_cid = str(parent_cid)
    parent_pid = cid2para.get(parent_cid)
    if not parent_pid:
        raise SystemExit("unknown parent comment %s" % parent_cid)
    cid, pid = _mkcomment(text, after_cid=parent_cid)
    _parent_of_comment[cid] = parent_cid
    cid2para[cid] = pid
    ex = ET.SubElement(exroot, q('commentEx', W15C))
    ex.set(q('paraId', W15C), pid)
    ex.set(q('paraIdParent', W15C), parent_pid)
    ex.set(q('done', W15C), '1' if done else '0')
    if done:
        resolve(parent_cid)

    def locate(pred):
        for par in droot.iter():
            for i, el in enumerate(list(par)):
                if pred(el):
                    return par, i
        return None, None

    # our range start goes immediately after the parent's, so both open together
    par, i = locate(lambda el: el.tag == q('commentRangeStart')
                    and el.get(q('id')) == parent_cid)
    if par is None:
        raise SystemExit("no commentRangeStart for %s" % parent_cid)
    e = ET.Element(q('commentRangeStart')); e.set(q('id'), cid)
    par.insert(i + 1, e)

    # our range end and reference go after the parent's reference run, so both
    # close over the same text and the parent's card sorts first
    par, i = locate(lambda el: el.tag == q('r')
                    and el.find(q('commentReference')) is not None
                    and el.find(q('commentReference')).get(q('id')) == parent_cid)
    if par is None:
        raise SystemExit("no commentReference run for %s" % parent_cid)
    end = ET.Element(q('commentRangeEnd')); end.set(q('id'), cid)
    par.insert(i + 1, end)
    par.insert(i + 2, _refrun(cid))
    return cid


def resolve(cid):
    pid = cid2para.get(str(cid))
    if not pid: raise SystemExit("no paraId for comment %s" % cid)
    for ex in exroot:
        if ex.get(q('paraId', W15C)) == pid:
            ex.set(q('done', W15C), '1'); return
    raise SystemExit("commentEx not found for %s" % cid)

def add_comment(p, text):
    """Anchor a brand-new comment (not a reply) across paragraph p."""
    cid, pid = _mkcomment(text)
    ex = ET.SubElement(exroot, q('commentEx', W15C))
    ex.set(q('paraId', W15C), pid); ex.set(q('done', W15C), '0')
    s = ET.Element(q('commentRangeStart')); s.set(q('id'), cid)
    e = ET.Element(q('commentRangeEnd')); e.set(q('id'), cid)
    rr = _refrun(cid)
    kids = list(p)
    ins_at = 1 if (kids and kids[0].tag == q('pPr')) else 0
    p.insert(ins_at, s); p.append(e); p.append(rr)
    return cid

import edits  # noqa  (the actual edit list, imported for readability)
edits.apply(globals())
EDITLOG = globals().get('LOG', [])
EDITLOG=globals().get('LOG',[])

DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
def save(tree, rel):
    body = ET.tostring(tree.getroot(), encoding='unicode')
    j = body.index('>')
    while body.count('"', 0, j) % 2 == 1:
        j = body.index('>', j + 1)
    # Merge, don't replace: the original root carries declarations ET drops
    # (mc:Ignorable references them), while ET's root carries the DrawingML
    # prefixes it hoisted out of inner elements. Both sets are needed.
    gen = body[:j + 1]
    orig = ROOTTAG[rel]
    have = set(re.findall(r'xmlns:([A-Za-z0-9_.-]+)=', orig))
    extra = [m for m in re.findall(r'xmlns:[A-Za-z0-9_.-]+="[^"]*"', gen)
             if re.match(r'xmlns:([A-Za-z0-9_.-]+)=', m).group(1) not in have]
    merged = orig[:-1].rstrip() + (' ' + ' '.join(extra) if extra else '') + '>'
    body = merged + body[j + 1:]
    leaked = sorted(set(re.findall(r'<(ns\d+):', body)))
    assert not leaked, "unregistered namespace(s) %s leaked into %s" % (leaked, rel)
    with open(os.path.join(WORK, rel), 'w', encoding='utf-8') as f:
        f.write(DECL + body)
save(doc,'word/document.xml'); save(ctree,'word/comments.xml')
save(extree,'word/commentsExtended.xml'); save(idstree,'word/commentsIds.xml')
save(cextree,'word/commentsExtensible.xml')
print("\n".join(EDITLOG))

with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED) as z:
    for n in names:
        z.write(os.path.join(WORK,n), n)
print("wrote", OUT)
