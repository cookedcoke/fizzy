// The markdown Gemini writes, drawn natively. The website renders the same text with marked (as it
// streams) and mistune (once saved): headings, lists, tables, fenced code, quotes and rules, with bold,
// italics, code, strikethrough and links inside. Apple's own parser only does the inline part, so the
// blocks are split here and each block's text goes through it.
import SwiftUI

enum MD {
    indirect enum Block {
        case paragraph(String)
        case heading(Int, String)
        case list([Item], ordered: Bool, start: Int)
        case code(String, lang: String)
        case quote([Block])
        case table(header: [String], align: [Align], rows: [[String]])
        case rule
    }

    struct Item {
        var text: String
        var checked: Bool?        // a task-list item: - [ ] / - [x]
        var children: [Block]
    }

    enum Align { case auto, left, center, right }

    // MARK: parsing

    static func parse(_ text: String) -> [Block] {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        return parse(lines[...])
    }

    private static func parse(_ lines: ArraySlice<String>) -> [Block] {
        var out: [Block] = []
        var para: [String] = []
        func flush() {
            if !para.isEmpty { out.append(.paragraph(joinParagraph(para))); para = [] }
        }
        var i = lines.startIndex
        while i < lines.endIndex {
            let line = lines[i]
            let t = line.trimmingCharacters(in: .whitespaces)
            if t.isEmpty { flush(); i += 1; continue }

            if let fence = fenceStart(t) {
                flush()
                var body: [String] = []
                i += 1
                while i < lines.endIndex, !isFenceEnd(lines[i], fence.marker) { body.append(lines[i]); i += 1 }
                if i < lines.endIndex { i += 1 }   // the closing fence (missing while the answer is still being written)
                out.append(.code(body.joined(separator: "\n"), lang: fence.lang))
                continue
            }
            if let h = heading(t) { flush(); out.append(.heading(h.level, h.text)); i += 1; continue }
            if isRule(t) { flush(); out.append(.rule); i += 1; continue }

            if t.hasPrefix(">") {
                flush()
                var q: [String] = []
                while i < lines.endIndex {
                    let s = lines[i].trimmingCharacters(in: .whitespaces)
                    if s.hasPrefix(">") { q.append(stripQuote(s)); i += 1 }
                    else if !s.isEmpty, !startsBlock(s), !(q.last ?? "").isEmpty { q.append(s); i += 1 }   // a wrapped line
                    else { break }
                }
                out.append(.quote(parse(q[...])))
                continue
            }

            if t.contains("|"), i + 1 < lines.endIndex, let align = tableSeparator(lines[i + 1]) {
                let header = cells(t)
                if header.count == align.count {
                    flush()
                    var rows: [[String]] = []
                    i += 2
                    while i < lines.endIndex {
                        let s = lines[i].trimmingCharacters(in: .whitespaces)
                        if s.isEmpty || !s.contains("|") { break }
                        var row = cells(s)
                        if row.count < header.count { row += Array(repeating: "", count: header.count - row.count) }
                        rows.append(Array(row.prefix(header.count)))
                        i += 1
                    }
                    out.append(.table(header: header, align: align, rows: rows))
                    continue
                }
            }

            if let first = listMarker(line) {
                flush()
                var items: [Item] = []
                while i < lines.endIndex, let mk = listMarker(lines[i]), mk.ordered == first.ordered, mk.indent < first.contentIndent {
                    i += 1
                    var body: [String] = []     // the item's further lines, with the item's indent taken off
                    while i < lines.endIndex {
                        let l = expandTabs(lines[i])
                        let s = l.trimmingCharacters(in: .whitespaces)
                        if s.isEmpty {
                            // A blank line stays in the item only if what follows is indented into it.
                            let next = lines[(i + 1)...].first { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
                            if let next, leadingSpaces(expandTabs(next)) >= mk.contentIndent { body.append(""); i += 1; continue }
                            break
                        }
                        let indent = leadingSpaces(l)
                        if indent >= mk.contentIndent { body.append(String(l.dropFirst(mk.contentIndent))) }
                        else if listMarker(l) != nil || startsBlock(s) { break }
                        else if !(body.last ?? " ").isEmpty { body.append(s) }   // a wrapped line of the item's text
                        else { break }
                        i += 1
                    }
                    var text = mk.text
                    var checked: Bool? = nil
                    if text.hasPrefix("[ ] ") { checked = false; text = String(text.dropFirst(4)) }
                    else if text.lowercased().hasPrefix("[x] ") { checked = true; text = String(text.dropFirst(4)) }
                    var children = parse(body[...])
                    // Lines wrapped straight under the marker belong to the item's own text.
                    if let f = body.first, !f.trimmingCharacters(in: .whitespaces).isEmpty, case .paragraph(let p)? = children.first {
                        text = text.isEmpty ? p : text + " " + p
                        children.removeFirst()
                    }
                    items.append(Item(text: text, checked: checked, children: children))
                }
                out.append(.list(items, ordered: first.ordered, start: first.number ?? 1))
                continue
            }

            para.append(line)
            i += 1
        }
        flush()
        return out
    }

    /// Lines of one paragraph. A line break is a space (the website renders with `breaks: false`),
    /// unless the line ends with two spaces or a backslash, which is a real break.
    private static func joinParagraph(_ lines: [String]) -> String {
        var s = ""
        for (n, raw) in lines.enumerated() {
            var line = raw.trimmingCharacters(in: .whitespaces)
            let hard = raw.hasSuffix("  ") || line.hasSuffix("\\")
            if line.hasSuffix("\\") { line.removeLast() }
            s += line
            if n < lines.count - 1 { s += hard ? "\n" : " " }
        }
        return s
    }

    private static func expandTabs(_ s: String) -> String { s.replacingOccurrences(of: "\t", with: "    ") }

    private static func leadingSpaces(_ s: String) -> Int { s.prefix { $0 == " " }.count }

    private static func fenceStart(_ t: String) -> (marker: String, lang: String)? {
        guard let c = t.first, c == "`" || c == "~" else { return nil }
        let run = t.prefix { $0 == c }
        guard run.count >= 3 else { return nil }
        let rest = t.dropFirst(run.count).trimmingCharacters(in: .whitespaces)
        if c == "`", rest.contains("`") { return nil }
        return (String(run), String(rest.split(separator: " ").first ?? ""))
    }

    private static func isFenceEnd(_ line: String, _ marker: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard let c = marker.first, t.hasPrefix(marker) else { return false }
        return t.allSatisfy { $0 == c }
    }

    private static func heading(_ t: String) -> (level: Int, text: String)? {
        let hashes = t.prefix { $0 == "#" }.count
        guard (1...6).contains(hashes), t.count == hashes || t[t.index(t.startIndex, offsetBy: hashes)] == " " else { return nil }
        var text = t.dropFirst(hashes).trimmingCharacters(in: .whitespaces)
        if text.hasSuffix("#") {   // closing hashes are allowed: "## Sales ##"
            let trimmed = text.reversed().drop { $0 == "#" }.reversed()
            if trimmed.last == " " || trimmed.isEmpty { text = String(trimmed).trimmingCharacters(in: .whitespaces) }
        }
        return (hashes, text)
    }

    private static func isRule(_ t: String) -> Bool {
        let chars = t.filter { $0 != " " }
        guard chars.count >= 3, let c = chars.first, "-*_".contains(c) else { return false }
        return chars.allSatisfy { $0 == c }
    }

    private static func stripQuote(_ s: String) -> String {
        var r = s.dropFirst()
        if r.first == " " { r = r.dropFirst() }
        return String(r)
    }

    private static func startsBlock(_ t: String) -> Bool {
        heading(t) != nil || isRule(t) || fenceStart(t) != nil || t.hasPrefix(">") || listMarker(t) != nil
    }

    private struct Marker {
        var indent: Int
        var contentIndent: Int
        var ordered: Bool
        var number: Int?
        var text: String
    }

    /// "- text", "* text", "+ text", "1. text" or "1) text", with up to 9 digits; the marker needs a space after it.
    private static func listMarker(_ line: String) -> Marker? {
        let l = expandTabs(line)
        let indent = leadingSpaces(l)
        let rest = l.dropFirst(indent)
        guard let c = rest.first else { return nil }
        var markerLength = 0
        var number: Int? = nil
        if "-*+".contains(c) {
            markerLength = 1
        } else if c.isASCII, c.isNumber {
            let digits = rest.prefix { $0.isASCII && $0.isNumber }
            guard digits.count <= 9, digits.count < rest.count else { return nil }
            let after = rest[rest.index(rest.startIndex, offsetBy: digits.count)]
            guard after == "." || after == ")" else { return nil }
            number = Int(digits)
            markerLength = digits.count + 1
        } else {
            return nil
        }
        let afterMarker = rest.dropFirst(markerLength)
        guard afterMarker.isEmpty || afterMarker.first == " " else { return nil }
        let spaces = afterMarker.prefix { $0 == " " }.count
        let gap = afterMarker.isEmpty ? 1 : (spaces > 4 ? 1 : spaces)   // 5+ spaces means indented code in the item: count one
        let text = afterMarker.dropFirst(spaces).trimmingCharacters(in: .whitespaces)
        return Marker(indent: indent, contentIndent: indent + markerLength + gap, ordered: number != nil, number: number, text: text)
    }

    /// `| --- | :---: | ---: |` under a header row. Returns one alignment per column, or nil if this isn't one.
    private static func tableSeparator(_ line: String) -> [Align]? {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard t.contains("-") else { return nil }
        let parts = cells(t)
        guard !parts.isEmpty, t.contains("|") || parts.count == 1 else { return nil }
        var align: [Align] = []
        for p in parts {
            let left = p.hasPrefix(":"), right = p.hasSuffix(":")
            var core = Substring(p)
            if left { core = core.dropFirst() }
            if right, !core.isEmpty { core = core.dropLast() }
            guard !core.isEmpty, core.allSatisfy({ $0 == "-" }) else { return nil }
            align.append(left && right ? .center : left ? .left : right ? .right : .auto)
        }
        return align
    }

    /// The cells of a table line, outer pipes dropped, `\|` kept as a pipe.
    private static func cells(_ line: String) -> [String] {
        var out: [String] = []
        var cur = ""
        var escaped = false
        for ch in line {
            if escaped { cur.append(ch == "|" ? "|" : "\\\(ch)"); escaped = false }
            else if ch == "\\" { escaped = true }
            else if ch == "|" { out.append(cur); cur = "" }
            else { cur.append(ch) }
        }
        if escaped { cur.append("\\") }
        out.append(cur)
        out = out.map { $0.trimmingCharacters(in: .whitespaces) }
        let t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|"), !out.isEmpty { out.removeFirst() }
        if t.hasSuffix("|"), !t.hasSuffix("\\|"), !out.isEmpty { out.removeLast() }
        return out
    }

    // MARK: inline

    /// Bold, italics, code, strikethrough and links. Only web links stay links, as on the website.
    static func inline(_ s: String) -> AttributedString {
        var a = (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace,
                                                                   failurePolicy: .returnPartiallyParsedIfPossible)))
            ?? AttributedString(s)
        for run in a.runs {
            if let url = run.link, !(url.scheme == "http" || url.scheme == "https") { a[run.range].link = nil }
        }
        return a
    }

    /// Like the website: a number (even a bold or dollar one) sits on the right of its column.
    static func isNumber(_ s: String) -> Bool {
        var t = Substring(s.filter { !"*_`".contains($0) }.trimmingCharacters(in: .whitespaces))
        if t.first == "-" || t.first == "+" { t = t.dropFirst() }
        if t.first == "$" { t = t.dropFirst() }
        if t.last == "%" { t = t.dropLast() }
        guard let f = t.first, f.isNumber else { return false }
        var dot = false
        for c in t {
            if c.isNumber { continue }
            if c == "," && !dot { continue }
            if c == "." && !dot { dot = true; continue }
            return false
        }
        return !t.hasSuffix(".")
    }

    /// Emojis are dropped, as the website does for saved answers (the staff site has none).
    static func stripEmoji(_ s: String) -> String {
        var out = String.UnicodeScalarView()
        for u in s.unicodeScalars {
            switch u.value {
            case 0x1F000...0x1FAFF, 0x2600...0x27BF, 0x2B00...0x2BFF, 0x2300...0x23FF, 0xFE0F: continue
            default: out.append(u)
            }
        }
        return String(out)
    }
}

// MARK: views

/// Markdown text as native views, re-parsed whenever the text changes (cheap at chat sizes), so it
/// works for an answer that's still arriving.
struct MarkdownView: View {
    let blocks: [MD.Block]

    init(_ text: String) { blocks = MD.parse(text) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in MDBlockView(block: block) }
        }
        .textSelection(.enabled)
    }
}

struct MDBlockView: View {
    let block: MD.Block

    var body: some View {
        switch block {
        case .paragraph(let s):
            Text(MD.inline(s))
        case .heading(let level, let s):
            Text(MD.inline(s))
                .font(level == 1 ? .title3.bold() : level == 2 ? .headline : .subheadline.bold())
                .padding(.top, 2)
        case .list(let items, let ordered, let start):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { n, item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        marker(item, ordered: ordered, number: start + n)
                        VStack(alignment: .leading, spacing: 4) {
                            if !item.text.isEmpty { Text(MD.inline(item.text)) }
                            ForEach(Array(item.children.enumerated()), id: \.offset) { _, child in MDBlockView(block: child) }
                        }
                    }
                }
            }
        case .code(let code, _):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(.footnote, design: .monospaced))
                    .padding(10)
            }
            .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color(.separator)))
        case .quote(let blocks):
            HStack(alignment: .top, spacing: 8) {
                RoundedRectangle(cornerRadius: 1.5).fill(Color(.separator)).frame(width: 3)
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(blocks.enumerated()), id: \.offset) { _, child in MDBlockView(block: child) }
                }
                .foregroundStyle(.secondary)
            }
        case .table(let header, let align, let rows):
            MDTable(header: header, align: align, rows: rows)
        case .rule:
            Divider().padding(.vertical, 2)
        }
    }

    @ViewBuilder private func marker(_ item: MD.Item, ordered: Bool, number: Int) -> some View {
        if let checked = item.checked {
            Image(systemName: checked ? "checkmark.square.fill" : "square")
                .foregroundStyle(checked ? Color.accentColor : Color.secondary)
                .frame(minWidth: 18, alignment: .trailing)
        } else if ordered {
            Text("\(number).").monospacedDigit().frame(minWidth: 18, alignment: .trailing)
        } else {
            Text("•").frame(minWidth: 18, alignment: .trailing)
        }
    }
}

/// A table that scrolls sideways when it's wider than the bubble. Columns of numbers line up on the right.
struct MDTable: View {
    let header: [String]
    let align: [MD.Align]
    let rows: [[String]]

    private func alignment(_ col: Int) -> HorizontalAlignment {
        switch col < align.count ? align[col] : .auto {
        case .left: return .leading
        case .center: return .center
        case .right: return .trailing
        case .auto:
            let values = rows.compactMap { col < $0.count && !$0[col].isEmpty ? $0[col] : nil }
            return !values.isEmpty && values.allSatisfy(MD.isNumber) ? .trailing : .leading
        }
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 0) {
                GridRow {
                    ForEach(header.indices, id: \.self) { c in
                        Text(MD.inline(header[c]))
                            .font(.footnote.weight(.semibold))
                            .padding(.vertical, 7)
                            .gridColumnAlignment(alignment(c))
                    }
                }
                Divider().gridCellUnsizedAxes(.horizontal)
                ForEach(Array(rows.enumerated()), id: \.offset) { r, row in
                    GridRow {
                        ForEach(header.indices, id: \.self) { c in
                            Text(MD.inline(c < row.count ? row[c] : ""))
                                .font(.footnote)
                                .padding(.vertical, 6)
                        }
                    }
                    if r < rows.count - 1 { Divider().gridCellUnsizedAxes(.horizontal) }
                }
            }
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
            .padding(.horizontal, 10)
        }
        .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color(.separator)))
    }
}
