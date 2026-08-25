# Marking up a CRO protocol or study plan

A CRO sends a draft study plan carrying its own comments — blanks for the sponsor to fill,
questions for the sponsor to answer, and internal notes to its own scientists. The sponsor
returns it with tracked changes and answers. This loop repeats for every study, so treat the
marked-up draft as an archived artifact: file it in the experiment's `protocol/` beside the
CRO original (never overwrite the original), and keep the edit list that produced it under
`analysis/` so a later revision can be re-marked rather than re-derived.

Use `scripts/docx_markup.py`. It writes tracked changes and threaded comment replies with the
stdlib alone; python-docx can do neither.

```bash
python3 .../scientist/scripts/docx_markup.py my_edits.py "CRO draft.docx" "marked up.docx" "Jane Sponsor"
```

`my_edits.py` exposes `apply(G)` and does the work — one block per document section, so the
next revision is a diff rather than a rewrite. Keep it beside the output with a short README
naming the input file and the command.

## Read the CRO's own comments first

The comments carry information the prose does not, and they reframe what look like defects:

- A field the CRO flags "please confirm" is a **question to you**, not a departure they made.
- A date or value flagged "to update" is a **placeholder**, not a claim.
- A comment can contradict the signed SOW. Query it; never silently "correct" it.

Extract them before reading the body — `word/comments.xml` plus each comment's anchor
paragraph — or you will mistake their questions for their decisions.

## Answering

**Reply inside the thread; never alongside it.** A separate top-level comment on the same span
of text is not a reply. Reserve new comments for points the CRO has not raised.

**Never resolve a comment you are answering.** Word greys out resolved threads, hiding the
answer from the person who asked. The asker closes it once satisfied. A tracked change alone
is not an answer either — it shows what changed, not why.

**Leave CRO-internal comments alone**: items awaiting their study director, individual
scientist or principal investigator, their site notes, and fields only they can fill.

**Write like correspondence.** No "Sponsor:" prefix, active voice, and the detail that earns
its place — lot numbers, concentrations, section references. Ask questions as questions.

**Mark up only what matters.** A cosmetic inconsistency is better raised as a comment asking
the CRO to make the document self-consistent than imposed as a tracked change, especially
where the change would move the plan against a signed SOW.

## Four traps

Each produces a file LibreOffice and pandoc open happily and Word rejects or renders wrong.
`docx_markup.py` handles all four and asserts against the first three.

1. **Namespaces.** Register every `xmlns:` prefix found *anywhere* in each part, not just on
   the root element — DrawingML (`a`, `a14`, `asvg`, `pic`) is declared on inner elements, and
   ElementTree re-emits any unregistered URI as `nsN:`. On write, *merge* the original root tag
   with the generated one: the original carries declarations ElementTree drops as unused but
   `mc:Ignorable` still names, and the generated one carries the DrawingML prefixes ElementTree
   hoisted up out of inner elements. Keeping only one set breaks the file either way.

2. **A comment lives in four parts, and all four must agree**: `comments.xml` (text),
   `commentsExtended.xml` (threading and resolved state), `commentsIds.xml` (durable identity —
   prefix `w16cid`, *not* `w15`), and `commentsExtensible.xml` (durableId → dateUtc). Write only
   some and Word shows the comment but will not thread it.

3. **Threading needs the anchor layout, not just `w15:paraIdParent`.** Parent and reply must
   open and close over exactly the same text, with the parent's `commentReference` first:

   ```
   <commentRangeStart parent/><commentRangeStart reply/>
     ...annotated text...
   <commentRangeEnd parent/><r><commentReference parent/></r>
   <commentRangeEnd reply/><r><commentReference reply/></r>
   ```

   Get it wrong and the reply still appears, just as a separate card sorted by wherever its
   range happened to end. Replies must also sit next to their parent, in order, in
   `comments.xml`. When inserting, look up each position fresh — an earlier insert shifts every
   later index.

4. **Element order inside `w:pPr`/`w:rPr` is schema-fixed.** A deleted paragraph mark is
   `w:pPr/w:rPr/w:del`, an inserted one `w:pPr/w:rPr/w:ins`, both first in `w:rPr`. Strip
   `w:highlight` from runs you insert to replace a highlighted placeholder, or the replacement
   still reads as a blank to be filled.

## Verify against Word, not against what is easy to check

LibreOffice and pandoc are permissive and will open files Word refuses; treating them as
validation is how all four traps above survived several rounds of "fixed". Assert the property
Word actually reads:

- parse **every** part with a strict parser, not just the zip container;
- no `nsN:` prefixes, no declaration lost from any root, every `mc:Ignorable` prefix declared;
- comment anchors balanced, and for each reply the layout in trap 3;
- all four comment parts holding the same number of entries, durable ids matching;
- **no part other than those you edited differs from the CRO original.**

If Word itself cannot be run, say the verification is indirect rather than reporting it as
confirmation.
