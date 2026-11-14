// Pages come back as the data their template is drawn from (the server's `X-Fizzy-Format: json`),
// so screens read from this instead of a struct per page. Missing keys read as null, not a crash.
import Foundation
import SwiftUI

@dynamicMemberLookup
enum J: Codable, Hashable {
    case null
    case bool(Bool)
    case num(Double)
    case str(String)
    case arr([J])
    case obj([String: J])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .num(v) }
        else if let v = try? c.decode(String.self) { self = .str(v) }
        else if let v = try? c.decode([J].self) { self = .arr(v) }
        else if let v = try? c.decode([String: J].self) { self = .obj(v) }
        else { self = .null }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let v): try c.encode(v)
        case .num(let v): try c.encode(v)
        case .str(let v): try c.encode(v)
        case .arr(let v): try c.encode(v)
        case .obj(let v): try c.encode(v)
        }
    }

    subscript(dynamicMember key: String) -> J { self[key] }
    subscript(key: String) -> J { if case .obj(let o) = self { return o[key] ?? .null } else { return .null } }
    subscript(i: Int) -> J { if case .arr(let a) = self, a.indices.contains(i) { return a[i] } else { return .null } }

    var isNull: Bool { if case .null = self { return true } else { return false } }
    var array: [J] { if case .arr(let a) = self { return a } else { return [] } }
    var dict: [String: J] { if case .obj(let o) = self { return o } else { return [:] } }
    var keys: [String] { Array(dict.keys) }
    var count: Int { array.count }
    var string: String {
        switch self {
        case .str(let s): s
        case .num(let n): n == n.rounded() ? String(Int(n)) : String(n)
        case .bool(let b): b ? "true" : "false"
        default: ""
        }
    }
    var optString: String? { if case .str(let s) = self, !s.isEmpty { return s } else { return nil } }
    var int: Int { if case .num(let n) = self { return Int(n) } else if case .bool(let b) = self { return b ? 1 : 0 } else { return Int(string) ?? 0 } }
    var double: Double { if case .num(let n) = self { return n } else { return Double(string) ?? 0 } }
    var bool: Bool {
        switch self {
        case .bool(let b): b
        case .num(let n): n != 0
        case .str(let s): !s.isEmpty
        case .arr(let a): !a.isEmpty
        case .obj(let o): !o.isEmpty
        case .null: false
        }
    }
    var strings: [String] { array.map(\.string) }
}

/// Something picked from a list, to show in a sheet.
struct Pick: Identifiable {
    let id = UUID()
    let value: J
}

// MARK: formatting

enum Fmt {
    /// The server's own time zone, so "08:35" matches what the website shows.
    static var timeZone = TimeZone(identifier: "Australia/Sydney") ?? .current

    /// Cents -> "$1,234.56" (or "-$1.00").
    static func money(_ cents: Int) -> String {
        let n = NumberFormatter()
        n.locale = Locale(identifier: "en_AU")   // as the server writes it, whatever region the phone is set to
        n.numberStyle = .decimal
        n.minimumFractionDigits = 2
        n.maximumFractionDigits = 2
        let body = n.string(from: NSNumber(value: Double(abs(cents)) / 100)) ?? "0.00"
        return (cents < 0 ? "-$" : "$") + body
    }

    static func number(_ n: Int) -> String {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_AU")
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? String(n)
    }

    /// Dollars typed by hand: "$12.50", "12,50" (a comma before one or two final digits is a decimal
    /// point, as on some regions' decimal pads), "1,200". nil when it isn't an amount, or is absurd.
    static func amount(_ s: String) -> Double? {
        var v = s.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "$", with: "")
        if let r = v.range(of: #",\d{1,2}$"#, options: .regularExpression) { v.replaceSubrange(r, with: "." + v[r].dropFirst()) }
        v = v.replacingOccurrences(of: ",", with: "")
        guard let d = Double(v), d.isFinite, abs(d) < 10_000_000 else { return nil }
        return d
    }

    /// The same, written as the server reads it ("12.50"); what was typed if it isn't an amount, so the server can say so.
    static func normalised(_ s: String) -> String { amount(s).map { String(format: "%.2f", $0) } ?? s }

    /// Typed dollars as cents, 0 if it isn't an amount.
    static func cents(_ s: String) -> Int { amount(s).map { Int(($0 * 100).rounded()) } ?? 0 }

    static func points(_ n: Int) -> String { (n > 0 ? "+" : "") + number(n) }

    private static let parsers: [ISO8601DateFormatter] = {
        let a = ISO8601DateFormatter()
        let b = ISO8601DateFormatter()
        b.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return [a, b]
    }()

    static func date(_ iso: String) -> Date? {
        for p in parsers { if let d = p.date(from: iso) { return d } }
        // "+0000" style offsets from the database
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        for pattern in ["yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd'T'HH:mm:ss.SSSSSSZ", "yyyy-MM-dd"] {
            f.dateFormat = pattern
            f.timeZone = pattern == "yyyy-MM-dd" ? timeZone : TimeZone(identifier: "UTC")
            if let d = f.date(from: iso) { return d }
        }
        return nil
    }

    /// An ISO timestamp (or a plain yyyy-MM-dd day) in the stall's time, e.g. "EEE d MMM HH:mm".
    static func when(_ iso: String, _ pattern: String = "EEE d MMM HH:mm") -> String {
        guard !iso.isEmpty, let d = date(iso) else { return iso }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_AU")
        f.timeZone = timeZone
        f.dateFormat = pattern
        return f.string(from: d)
    }

    static func day(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = timeZone
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }

    static func parseDay(_ s: String) -> Date {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = timeZone
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: s) ?? Date()
    }

    /// 1 / 2.5 -> "1", "2.5" (the website's %g).
    static func g(_ d: Double) -> String { d == d.rounded() ? String(Int(d)) : String(d) }
}

// MARK: big screens

/// On a wide screen (the iPhone Duo open, or landscape) lists and forms stay a readable width in the middle
/// instead of stretching edge to edge. contentMargins reaches every scroll view inside, so this goes once at
/// the root.
struct ReadableWidth: ViewModifier {
    var limit: CGFloat = 720
    func body(content: Content) -> some View {
        GeometryReader { geo in
            // Narrow (a phone, the Duo closed): untouched, so lists keep their own margins, which also leave room
            // for the Duo's side rail. Wide: a column in the middle.
            if geo.size.width > limit + 80 {
                content.contentMargins(.horizontal, (geo.size.width - limit) / 2, for: .scrollContent)
            } else {
                content
            }
        }
    }
}

extension View {
    func readableWidth(_ limit: CGFloat = 720) -> some View { modifier(ReadableWidth(limit: limit)) }
}

// MARK: the iPhone Duo's fold

/// Two columns on a wide screen, using the system's split view, so iOS itself puts the split on the iPhone Duo's
/// hinge when it's half folded (with the gap the system uses) - the same as Settings and Mail. Narrower, the caller
/// shows its usual single page instead. The left column has no sidebar button: both columns always show.
struct SplitColumns<Left: View, Right: View>: View {
    @ViewBuilder let left: () -> Left
    @ViewBuilder let right: () -> Right
    @State private var visibility: NavigationSplitViewVisibility = .all

    var body: some View {
        NavigationSplitView(columnVisibility: $visibility) {
            left()
                .listStyle(.insetGrouped)   // the system's sidebar style turns cards into plain rows; keep them cards
                .navigationSplitViewColumnWidth(min: 320, ideal: 460, max: 560)
                .toolbar(removing: .sidebarToggle)
        } detail: {
            NavigationStack { right() }
        }
        .navigationSplitViewStyle(.balanced)
    }
}
