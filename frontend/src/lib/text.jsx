const STOP = new Set(
  "the and for are but not you all any can her was one our out has his how its may new now see who did get let put say she too use what when where which who why with from that this they them then than into over have been were will your about does would could should there their these those".split(
    " ",
  ),
)

export function queryTerms(question) {
  const words = question.toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}-]*/gu) || []
  return [...new Set(words)].filter((word) => word.length >= 3 && !STOP.has(word))
}

function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

// Wraps the question's words in <mark>. Embeddings match on meaning, so a passage
// can be relevant without containing any of them.
export function Highlight({ text, terms }) {
  if (!terms.length) return text
  const pattern = new RegExp(`(${terms.map(escapeRegex).join("|")})`, "gi")
  return text.split(pattern).map((part, index) => (index % 2 ? <mark key={index}>{part}</mark> : part))
}

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[\d+(?:\s*,\s*\d+)*\])/g
const LIST_ITEM = /^\s*(?:[-*•]|\d+[.)])\s+/

function inline(text, count, onCite, selected) {
  return text.split(INLINE).map((part, index) => {
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>
    const match = part.match(/^\[(\d+(?:\s*,\s*\d+)*)\]$/)
    if (!match) return part
    const numbers = match[1].split(",").map((value) => Number(value.trim()))
    if (!numbers.every((n) => n >= 1 && n <= count)) return part
    return (
      <span className="cites" key={index}>
        {numbers.map((n) => (
          <button
            type="button"
            key={n}
            className={`cite${selected === n ? " active" : ""}`}
            onClick={() => onCite(n)}
            aria-label={`Open source ${n}`}
          >
            {n}
          </button>
        ))}
      </span>
    )
  })
}

// A small renderer for what chat models usually write: paragraphs, lists, bold,
// code, and [n] citations that become buttons.
export function Answer({ text, count, onCite, selected }) {
  return text
    .trim()
    .split(/\n{2,}/)
    .map((block, index) => {
      const lines = block.split("\n")
      if (lines.every((line) => LIST_ITEM.test(line))) {
        const ordered = /^\s*\d/.test(lines[0])
        const items = lines.map((line, i) => <li key={i}>{inline(line.replace(LIST_ITEM, ""), count, onCite, selected)}</li>)
        return ordered ? <ol key={index}>{items}</ol> : <ul key={index}>{items}</ul>
      }
      return (
        <p key={index}>
          {lines.map((line, i) => (
            <span key={i}>
              {i ? <br /> : null}
              {inline(line, count, onCite, selected)}
            </span>
          ))}
        </p>
      )
    })
}
