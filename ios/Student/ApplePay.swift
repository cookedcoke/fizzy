import PassKit
import SquareInAppPaymentsSDK
import SwiftUI

/// Paying off a tab with Apple Pay. Apple Pay gives a payment; Square's SDK turns it into a one-time card
/// nonce; Fizzy charges the nonce through Square and puts it against the tab (POST /me/tab/pay). The card
/// itself never reaches the app or Fizzy.
@MainActor
final class TabPayment: NSObject, PKPaymentAuthorizationControllerDelegate {
    /// Fizzy's settings for this (the tab's `apple_pay`): Square's application id, the merchant id, the currency.
    struct Config {
        let appID: String, merchantID: String, currency: String
        init?(_ j: J) {
            guard let app = j.app_id.optString, let merchant = j.merchant_id.optString else { return nil }
            appID = app; merchantID = merchant; currency = j.currency.optString ?? "AUD"
        }
    }

    /// This phone can pay with Apple Pay through Square (a supported card is in the Wallet).
    static func available(_ config: Config) -> Bool {
        SQIPInAppPaymentsSDK.squareApplicationID = config.appID   // must be set before anything else in the SDK
        return SQIPInAppPaymentsSDK.canUseApplePay
    }

    private let config: Config
    private let amountCents: Int
    private let key = UUID().uuidString   // one attempt: a retry of the same sheet is never charged twice
    private var message: String?
    private var failed = false
    private var done: CheckedContinuation<(ok: Bool, message: String)?, Never>?

    init(config: Config, amountCents: Int) {
        self.config = config
        self.amountCents = amountCents
    }

    /// Shows the Apple Pay sheet. nil if they cancelled; otherwise whether it worked and what to tell them.
    func run() async -> (ok: Bool, message: String)? {
        SQIPInAppPaymentsSDK.squareApplicationID = config.appID
        let request = PKPaymentRequest.squarePaymentRequest(merchantIdentifier: config.merchantID, countryCode: "AU",
                                                            currencyCode: config.currency)
        request.requiredBillingContactFields = []   // Square's default asks for an address; a tab doesn't need one
        request.requiredShippingContactFields = []
        request.paymentSummaryItems = [PKPaymentSummaryItem(label: "Cooked Coke Co tab",
                                                            amount: NSDecimalNumber(value: Double(amountCents) / 100))]
        let controller = PKPaymentAuthorizationController(paymentRequest: request)
        controller.delegate = self
        return await withCheckedContinuation { c in
            done = c
            controller.present { shown in
                if !shown { Task { @MainActor in self.finish(nil) } }
            }
        }
    }

    nonisolated func paymentAuthorizationController(_ controller: PKPaymentAuthorizationController, didAuthorizePayment payment: PKPayment,
                                                    handler completion: @escaping (PKPaymentAuthorizationResult) -> Void) {
        SQIPApplePayNonceRequest(payment: payment).perform { details, error in
            Task { @MainActor in
                guard let nonce = details?.nonce else {
                    self.failed = true
                    self.message = "Apple Pay didn't go through, and you weren't charged. Try again, or pay at the stall."
                    completion(PKPaymentAuthorizationResult(status: .failure, errors: error.map { [$0] }))
                    return
                }
                do {
                    self.message = try await StudentAPI.shared.payTab(nonce: nonce, amountCents: self.amountCents, key: self.key)
                    self.failed = false
                    completion(PKPaymentAuthorizationResult(status: .success, errors: nil))
                } catch {
                    self.failed = true
                    self.message = isOffline(error) ? "No signal, and you weren't charged. Try again when there is." : error.localizedDescription
                    completion(PKPaymentAuthorizationResult(status: .failure, errors: nil))
                }
            }
        }
    }

    nonisolated func paymentAuthorizationControllerDidFinish(_ controller: PKPaymentAuthorizationController) {
        controller.dismiss {
            Task { @MainActor in self.finish(self.message.map { (!self.failed, $0) }) }
        }
    }

    private func finish(_ result: (ok: Bool, message: String)?) {
        done?.resume(returning: result)
        done = nil
    }
}

/// "Pay $7.50 with Apple Pay" under the tab, when Fizzy has it switched on and this phone can use it.
struct PayTabButton: View {
    let tab: J
    @Environment(Student.self) private var model
    @State private var result: (ok: Bool, message: String)?
    @State private var paying = false

    var body: some View {
        if let config = TabPayment.Config(tab.apple_pay), tab.owing_cents.int > 0, TabPayment.available(config) {
            VStack(spacing: 6) {
                ApplePayButton { Task { await pay(config) } }
                    .frame(maxWidth: .infinity, minHeight: 48, maxHeight: 48)
                    .disabled(paying)
                Text("Pays off the \(Fmt.money(tab.owing_cents.int)) you owe. Or pay at the stall as usual.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .padding(.vertical, 4)
            .alert(result?.ok == true ? "Paid! 🎉" : "That didn't work",
                   isPresented: Binding(get: { result != nil }, set: { if !$0 { result = nil } })) {
                Button("OK", role: .cancel) {}
            } message: { Text(result?.message ?? "") }
        }
    }

    private func pay(_ config: TabPayment.Config) async {
        paying = true
        defer { paying = false }
        let payment = TabPayment(config: config, amountCents: tab.owing_cents.int)
        if let r = await payment.run() {
            result = r
            await model.load()   // the tab, the points and the activity, as they are now
        }
    }
}

/// Apple's own "Pay with Apple Pay" button (PKPaymentButton). SwiftUI's version can draw nothing inside a
/// list row, so this wraps the UIKit one, which always does.
private struct ApplePayButton: UIViewRepresentable {
    let action: () -> Void

    func makeUIView(context: Context) -> PKPaymentButton {
        let button = PKPaymentButton(paymentButtonType: .plain, paymentButtonStyle: .black)
        button.cornerRadius = 12
        button.addTarget(context.coordinator, action: #selector(Coordinator.tap), for: .touchUpInside)
        return button
    }

    func updateUIView(_ uiView: PKPaymentButton, context: Context) { context.coordinator.action = action }

    func makeCoordinator() -> Coordinator { Coordinator(action: action) }

    final class Coordinator: NSObject {
        var action: () -> Void
        init(action: @escaping () -> Void) { self.action = action }
        @objc func tap() { action() }
    }
}
