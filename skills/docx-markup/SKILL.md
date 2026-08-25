---
name: docx-markup
description: >-
  Return a Word document to whoever wrote it, marked up — tracked changes for what
  you changed, threaded comment replies for what they asked, and their own file
  otherwise untouched. Reads the author's existing comments first (a field flagged
  "please confirm" is a question to you, not their error), decides what belongs in a
  change versus a comment, replies inside each thread rather than alongside it, and
  never resolves a comment it is answering. Writes real OOXML tracked changes and
  threaded replies with the standard library, which python-docx cannot do, and
  handles the four traps that yield a file LibreOffice and pandoc open happily while
  Word rejects it or renders it wrong. Use this skill whenever the user wants to mark
  up, redline, review, or comment on a .docx and send it back — a CRO study plan or
  protocol, a contract or SOW, a manuscript, a report circulated for review — or to
  answer the comments already in one. Triggers include "mark this up", "redline this",
  "return it with tracked changes", "reply to the comments in this doc", "review this
  contract", "comment on this draft". For creating or reading Word documents rather
  than marking one up, use the docx skill instead.
---

# Marking up a Word document

Returning a `.docx` to whoever wrote it — tracked changes for what you changed, comments for
what they need to decide — with everything else in the file untouched. The document might be a
CRO study plan, a contract, an SOW, a manuscript, or a report someone circulated for review;
the procedure is the same.

Use `scripts/docx_markup.py`. It writes tracked changes and threaded comment replies with the
standard library alone; python-docx can do neither.

```bash
python3 scripts/docx_markup.py my_edits.py "their draft.docx" "marked up.docx" "Your Name"
```

`my_edits.py` exposes `apply(G)` and holds the edits themselves — organize it by document
section, one block per topic, so the next revision is a diff rather than a rewrite. Keep the
author's original file untouched alongside the output, and keep `my_edits.py` with a short
README naming the input and the command, so a later draft can be re-marked instead of
re-derived.

## Read their comments before you read the document

The comments in a circulated draft carry information the prose does not, and they routinely
reframe what look like defects:

- A field flagged *"please confirm"* or *"TBC"* is **a question to you**, not a decision the
  author made. Answer it; don't write it up as their error.
- A value flagged *"to update"* is **a placeholder**, not a claim. Don't treat a stale date or
  a dummy number as something they asserted.
- A comment can **contradict the governing document** — the signed contract, the SOW, the spec.
  Quote both and ask; never silently "correct" one to match the other.
- Some comments are **internal to the author's organization** — awaiting their own colleague,
  their site conventions, fields only they can fill. Leave those alone.

Extract every comment with its anchor paragraph before reading the body. Otherwise you will
mistake their open questions for their conclusions, and write a review of problems that aren't
there while missing the ones that are.

## Decide what is a change and what is a comment

**Tracked change** for facts you own and can state: your reference numbers, your contact
details, values from a document you hold, text they explicitly asked you to supply.

**Comment** for anything they must decide, anything you are asking about, and anything where
your preferred wording is not obviously right.

**Comment, not change, for cosmetic inconsistency.** If a document contradicts itself somewhere
that does not affect the outcome, ask the author to make it self-consistent rather than
imposing your choice. Marking it up spends their attention and yours on nothing.

**Flag loudly before changing anything that moves against a governing document.** If an edit
would put the draft out of step with a signed contract, say so in the comment and confirm the
change is wanted, rather than quietly making the document disagree with the thing it is
subordinate to.

## Answering

**Reply inside their thread, never alongside it.** A separate top-level comment on the same
span of text is not a reply — it just sits next to the question, and if the thread is later
collapsed your answer goes with it. Reserve new comments for points they have not raised.

**Never resolve a comment you are answering.** Word greys out resolved threads, hiding the
answer from the person who asked. Whoever raised a comment closes it once satisfied. A tracked
change alone is not an answer either — it shows what changed, not why, so pair it with a reply
saying so.

## Voice

You are writing to a person you work with, not filing a formal response. Draft in that voice
from the start rather than writing stiffly and softening afterward.

- No standing prefix or label on each comment — just say the thing.
- Relaxed, normal register. Precise, but not officious.
- Active voice, standard US English, and the spelling the document already uses.
- Only detail that earns its place: identifiers, values, section references stay; throat-clearing
  goes.
- Ask questions as questions, not as "please advise: (a)… (b)… (c)…".

```
✗  Sponsor: yes - please provide the biodistribution results to the Study Director
   for inclusion in the report.
✓  Yes — please give the biodistribution results to the Study Director for the report.

✗  Sponsor: please advise: (a) do you scale divalent supplementation with ASO
   concentration across a dose ladder, or hold it constant; (b) what do you
   recommend here; and (c) what should the vehicle group receive.
✓  What do you do here: scale divalent supplementation with ASO across a ladder, or
   hold it constant? What would you recommend? And what should the vehicle group get,
   so we can rule magnesium out as a cause of anything we see at the high dose?
```

The stiff register reads as distancing, and the padding buries the questions that need
answers — which is the whole reason the document is going back.

## Four traps in the file format

Each produces a file that LibreOffice and pandoc open happily and Word rejects or renders
wrong. `docx_markup.py` handles all four and asserts against the first three.

1. **Namespaces.** Register every `xmlns:` prefix found *anywhere* in each part, not just on the
   root element — DrawingML (`a`, `a14`, `asvg`, `pic`) is declared on inner elements, and
   ElementTree re-emits any unregistered URI as `nsN:`. On write, *merge* the original root tag
   with the generated one: the original carries declarations ElementTree drops as unused but
   `mc:Ignorable` still names, and the generated one carries the DrawingML prefixes ElementTree
   hoisted up. Keep only one set and the file breaks either way.

2. **A comment lives in four parts, and all four must agree**: `comments.xml` (text),
   `commentsExtended.xml` (threading and resolved state), `commentsIds.xml` (durable identity —
   prefix `w16cid`, *not* `w15`), and `commentsExtensible.xml` (durableId → dateUtc). Write only
   some and Word shows the comment but will not thread it.

3. **Threading needs the anchor layout, not just `w15:paraIdParent`.** Parent and reply must open
   and close over exactly the same text, with the parent's `commentReference` first:

   ```
   <commentRangeStart parent/><commentRangeStart reply/>
     ...annotated text...
   <commentRangeEnd parent/><r><commentReference parent/></r>
   <commentRangeEnd reply/><r><commentReference reply/></r>
   ```

   Get it wrong and the reply still appears, just as a separate card sorted by wherever its range
   happened to end. Replies must also sit next to their parent, in order, in `comments.xml`. When
   inserting, look up each position fresh — an earlier insert shifts every later index.

4. **Element order inside `w:pPr`/`w:rPr` is schema-fixed.** A deleted paragraph mark is
   `w:pPr/w:rPr/w:del`, an inserted one `w:pPr/w:rPr/w:ins`, both first in `w:rPr`. Strip
   `w:highlight` from runs you insert to replace a highlighted placeholder, or the replacement
   still reads as a blank waiting to be filled.

## Verify against Word, not against what is easy to check

LibreOffice and pandoc are permissive and open files Word refuses. Treating them as validation is
how every trap above survives several rounds of "fixed". Assert the properties Word actually
reads:

- parse **every** part with a strict parser, not just the zip container;
- no `nsN:` prefixes, no declaration lost from any root, every `mc:Ignorable` prefix declared;
- comment anchors balanced, and for each reply the layout in trap 3;
- all four comment parts holding the same number of entries, with durable ids matching;
- **no part other than those you edited differs from the original.**

If Word itself cannot be run, say the verification is indirect rather than reporting it as
confirmation.
