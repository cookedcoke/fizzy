import Foundation
import IOKit.pwr_mgt
import os

/// Stops the screens dimming, sleeping or starting the screen saver while Fizzy is open, so the
/// customer display stays on.
final class KeepAwake {
    private var assertion: IOPMAssertionID = 0
    private let log = Logger(subsystem: "co.cookedcoke.fizzy", category: "app")

    func start() {
        guard assertion == 0 else { return }
        let result = IOPMAssertionCreateWithName(
            kIOPMAssertionTypePreventUserIdleDisplaySleep as CFString,
            IOPMAssertionLevel(kIOPMAssertionLevelOn),
            "Fizzy customer display" as CFString,
            &assertion)
        if result != kIOReturnSuccess {
            assertion = 0
            log.error("Couldn't keep the Mac awake (error \(result))")
        }
    }

    func stop() {
        guard assertion != 0 else { return }
        IOPMAssertionRelease(assertion)
        assertion = 0
    }
}
