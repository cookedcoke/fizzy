// The JSON the server sends (fizzy/app.py, "for the iOS app"). Money arrives already formatted.
import Foundation

struct Me: Decodable {
    let staff: String
    let staff_names: [String]
}

struct SearchHit: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String
    let email: String?
    let points: Int
    let owing: String
    let status: String
    let status_key: String
}

struct Customer: Decodable {
    struct Tab: Decodable {
        let status: String
        let label: String
        let can_charge: Bool
        let available: String
        let limit: String
        let spent: String
        let unlock_spend: String
        let decision_note: String?
    }
    struct Charge: Decodable, Hashable {
        let at: String
        let amount: String
        let unpaid: String
        let due: String
        let overdue: Bool
    }
    struct Reward: Decodable, Hashable {
        let name: String
        let cost: Int
        let short: Int
        let can_redeem: Bool
        let limit_hit: Bool
    }
    struct Entry: Decodable, Hashable {
        let delta: Int
        let reason: String
        let note: String?
        let staff: String?
        let at: String
    }

    let id: Int
    let name: String
    let email: String?
    let kind: String?
    let archived: Bool
    let points: Int
    let tab: Tab
    let owing: String
    let owing_cents: Int
    let overdue: String?
    let charges: [Charge]
    let rewards: [Reward]
    let history: [Entry]
    let flash: String?
    let flash_kind: String
    let opened: String
}

struct Sale: Decodable, Identifiable, Hashable {
    let key: String
    let at: String
    let at_iso: String
    let items: String
    let paid: String?
    let tab: String?
    let repaid: String?
    let tab_warning: Bool
    var id: String { key }

    var amounts: String {
        [paid.map { "\($0) paid" }, tab.map { "\($0) on tab" }, repaid.map { "\($0) repaid" }]
            .compactMap { $0 }.joined(separator: " · ")
    }
}

struct Claimable: Decodable {
    let note: String?
    let square_on: Bool
    let sales: [Sale]
}

/// The words the staff pages use for points reasons.
func reasonLabel(_ reason: String) -> String {
    switch reason {
    case "purchase": "Purchase"
    case "refund": "Refund"
    case "manual": "Adjusted"
    case "redeem": "Reward"
    case "badge": "Badge"
    case "event": "Event bonus"
    case "tab_repaid": "Tab repaid"
    case "tab_refund": "Tab refund"
    default: reason.replacingOccurrences(of: "_", with: " ").capitalized
    }
}
