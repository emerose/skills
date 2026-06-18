--[[
references.lua — render the report's References list unnumbered.

`sci report`'s renderer emits per-page footnotes for `[claim:]`/`[lit:]` citations,
auto-numbered by the typesetter. A *numbered* References/bibliography list collides
visually with those footnote numbers, and nothing in the prose cross-references a reference
by its number (inline citations are `[lit:]`/`[claim:]` footnotes; background refs are
author-year). So convert the ordered list under a "References" (or "Bibliography" / "Works
cited") heading into an UNNUMBERED list — done at render time, so existing reports need no
edits.

Structural AST transform: format-agnostic (PDF / HTML / docx), pandoc's writers still
typeset. The scope is reset at the next heading, so only the References section is touched.
--]]

local function is_ref_heading(h)
  local t = pandoc.utils.stringify(h):lower():gsub("^%s+", ""):gsub("%s+$", "")
  return t == "references" or t == "bibliography" or t == "works cited"
end

function Pandoc(doc)
  local in_refs = false
  local out = {}
  for _, blk in ipairs(doc.blocks) do
    if blk.t == "Header" then
      in_refs = is_ref_heading(blk)
      out[#out + 1] = blk
    elseif in_refs and blk.t == "OrderedList" then
      -- drop the numbering: an OrderedList's items are a list of {Block}; a BulletList
      -- takes the same items, so the entries survive verbatim, just unnumbered.
      out[#out + 1] = pandoc.BulletList(blk.content)
    else
      out[#out + 1] = blk
    end
  end
  doc.blocks = out
  return doc
end

return { { Pandoc = Pandoc } }
