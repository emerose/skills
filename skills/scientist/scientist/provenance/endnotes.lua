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

-- Replace each footnote with a superscript marker that links to its endnote; stash the
-- note's content (a list of Blocks) for the end section.
local function Note(el)
  local n = #notes + 1
  local note_id = "en-" .. n          -- anchor on the endnote entry
  local back_id = "en-ref-" .. n      -- anchor on the in-text marker (note links back)
  notes[n] = { num = n, id = note_id, back = back_id, content = el.content }
  local marker = pandoc.Link({ pandoc.Str(tostring(n)) }, "#" .. note_id, "",
                             pandoc.Attr(back_id))
  return pandoc.Superscript({ marker })
end

-- After the walk, append the "Grounding notes" section.
local function Pandoc(doc)
  if #notes == 0 then return doc end
  local blocks = doc.blocks
  blocks:insert(pandoc.Header(1, { pandoc.Str("Notes") }, pandoc.Attr("notes")))
  for _, note in ipairs(notes) do
    -- a back-linked number "N." leading the note
    local lead = { pandoc.Link({ pandoc.Str(note.num .. ".") }, "#" .. note.back),
                   pandoc.Space() }
    local content = note.content
    local body
    if #content > 0 and (content[1].t == "Para" or content[1].t == "Plain") then
      -- prepend the number to the first paragraph so it flows inline
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
