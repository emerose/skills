--[[
layout.lua — fit a report's exhibits to the Tufte page (used by `sci report`, ROADMAP §5,
alongside references.lua). Structural AST tweaks; pandoc's writers still typeset.

  * Tables — equal column widths summing to the full measure, so the LaTeX writer emits
    `p{...}` columns that fill `\linewidth` (a bare pipe table otherwise renders narrow).
    Harmless on HTML (relative column widths).

  * Figures (PDF/LaTeX only) — replace the floating figure with an in-place, full-text-width
    block (so it stays where written and centred, rather than floating off to its own page),
    keeping `\captionof{figure}` numbering. The caption inlines are written back to LaTeX so
    their formatting / footnote markers survive.
--]]

function Table(t)
  local n = #t.colspecs
  if n > 0 then
    for i = 1, n do
      t.colspecs[i] = { t.colspecs[i][1], 1.0 / n }
    end
  end
  return t
end

function Figure(fig)
  if not FORMAT:match("latex") then
    return nil                                    -- HTML/docx: leave the figure as-is
  end
  local src
  pandoc.walk_block(fig, { Image = function(im) src = src or im.src end })
  if not src then return nil end
  local caption = pandoc.write(pandoc.Pandoc(fig.caption.long or {}), "latex")
  local tex = table.concat({
    "\\par\\medskip\\begin{center}",
    "\\includegraphics[width=\\linewidth,keepaspectratio]{" .. src .. "}",
    "\\end{center}",
    "\\captionof{figure}{" .. caption .. "}",
    "\\par\\medskip",
  }, "\n")
  return pandoc.RawBlock("latex", tex)
end
