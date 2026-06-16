--[[
endnotes.lua — a pandoc filter that turns footnotes into endnotes.

Used by `sci report`'s renderer (ROADMAP §5). The report's `[claim:<id>]` citations are
assembled as native pandoc footnotes (hyperlinked, auto-numbered); this filter relocates
them — at the *AST* level, so it is format-agnostic and does no typesetting itself — into a
single "Grounding notes" section at the end of the document. Each in-text marker links to
its note and each note links back to its marker.

Why a filter and not the LaTeX `endnotes` package: endnotes are *position-independent*
(they all go to the end regardless of page layout), so the move is a pure structural
transform expressible before any writer runs — it then works uniformly for PDF (LaTeX),
HTML, and docx, with no LaTeX-package dependency. (True bottom-of-page footnotes could NOT
be done this way: they need the typesetter's page knowledge. Endnotes can.)

The collected notes keep their order of first appearance in the text.
--]]

local notes = {}
local seen = {}   -- note content (stringified) -> existing index, so a claim cited more
                  -- than once shares ONE numbered note (proper citation reuse) rather than
                  -- producing a duplicate endnote per occurrence.

-- Replace each footnote with a superscript marker that links to its endnote; stash the
-- note's content (a list of Blocks) for the end section.
local function Note(el)
  local key = pandoc.utils.stringify(pandoc.Div(el.content))
  local idx = seen[key]
  if not idx then
    idx = #notes + 1
    seen[key] = idx
    notes[idx] = { num = idx, id = "en-" .. idx, back = "en-ref-" .. idx,
                   content = el.content }
  end
  local note = notes[idx]
  -- only the first occurrence carries the back-anchor (the note links back to it);
  -- later markers just link forward, so ids/labels stay unique.
  local attr = pandoc.Attr("")
  if not note.back_emitted then
    note.back_emitted = true
    attr = pandoc.Attr(note.back)
  end
  local marker = pandoc.Link({ pandoc.Str(tostring(note.num)) }, "#" .. note.id, "", attr)
  return pandoc.Superscript({ marker })
end

-- After the walk, append the "Grounding notes" section.
local function Pandoc(doc)
  if #notes == 0 then return doc end
  local blocks = doc.blocks
  blocks:insert(pandoc.Header(1, { pandoc.Str("Notes") }, pandoc.Attr("notes")))

  if FORMAT:match("latex") then
    -- a tight, hanging-indent endnotes list (small; the back-linked number hangs in the
    -- margin, wrapped lines align under the text) — the clean endnotes look, single-pass,
    -- with no external .ent file or LaTeX package.
    local out = {
      "\\begingroup\\small\\setlength{\\parindent}{0pt}",
      "\\begin{list}{}{%",
      "  \\setlength{\\leftmargin}{1.9em}\\setlength{\\labelwidth}{1.5em}",
      "  \\setlength{\\labelsep}{0.4em}\\setlength{\\itemindent}{0pt}",
      "  \\setlength{\\listparindent}{0pt}\\setlength{\\itemsep}{2.5pt}",
      "  \\setlength{\\parsep}{0pt}\\setlength{\\topsep}{4pt}",
      "  \\renewcommand{\\makelabel}[1]{\\hss#1}}",   -- right-align the number (hanging)
    }
    for _, note in ipairs(notes) do
      local body = pandoc.write(pandoc.Pandoc(note.content), "latex"):gsub("%s+$", "")
      -- brace-wrap the label: the `]` inside \hyperref[...] would otherwise close \item[
      out[#out + 1] = "\\item[{\\hyperref[" .. note.back .. "]{" .. note.num .. ".}}]" ..
                      "\\phantomsection\\label{" .. note.id .. "}" .. body
    end
    out[#out + 1] = "\\end{list}\\endgroup"
    blocks:insert(pandoc.RawBlock("latex", table.concat(out, "\n")))
    return doc
  end

  -- HTML / docx: one Div per note (a back-linked number leads each).
  for _, note in ipairs(notes) do
    local lead = { pandoc.Link({ pandoc.Str(note.num .. ".") }, "#" .. note.back),
                   pandoc.Space() }
    local content = note.content
    local body
    if #content > 0 and (content[1].t == "Para" or content[1].t == "Plain") then
      local inlines = {}
      for _, x in ipairs(lead) do inlines[#inlines + 1] = x end
      for _, x in ipairs(content[1].content) do inlines[#inlines + 1] = x end
      local rest = {}
      for i = 2, #content do rest[#rest + 1] = content[i] end
      body = { pandoc.Para(inlines) }
      for _, b in ipairs(rest) do body[#body + 1] = b end
    else
      body = { pandoc.Para(lead) }
      for _, b in ipairs(content) do body[#body + 1] = b end
    end
    blocks:insert(pandoc.Div(body, pandoc.Attr(note.id)))
  end
  return doc
end

-- element functions run during the walk; Pandoc runs last on the whole document.
return { { Note = Note, Pandoc = Pandoc } }
