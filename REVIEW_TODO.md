# Review pass: still to do

Done and pushed: security fixes (odd IDs, archived namesakes, stacked double points, long extensions, payouts as big changes, removed staff signed out, exports locked), student sign-in fixes (school Wi-Fi lockout, double taps, student numbers without "rns"), student contrast and tap feedback, student error wording, down page reloads, success/error flash colours, header search folds to an icon under 1600px, many staff CSS fixes.

Still to do:
- Staff: dashboard "Needs attention" as cards on phone; give date cells class="when"; tab score panel says "Their tab's on" for overdue customers (customer.html ~210-212); tool_result.html numeric cells class="n" and nicer dates; Square table hide Tender on tablet and data-clip="25"; Money/Rewards column balance; Redeem button widths; tool "Run" button not sm; passcode placeholder; en dashes in verdicts; Claim page shouldn't show the env var name.
- Staff: decide whether "Wrong passcode. 2 more tries." should drop the count.
- PIN guessing: longer-window cap / 6+ digits; rolling per-staff hand-change total.
- Student: points card "+N since last time"; badges summary "Show fewer"; passcode show/hide; leaderboard toggle.
- cookedcoke.co: Sydney time zone and holidays in the open/closed pill; "Tab buys earn points when paid off"; promos strip layout shift (cache + preconnect); phone pill text; safe-area insets; lang en-AU, canonical, manifest colours; Tally loads only on tap.
- Unfinished reviews (stopped early): money/points logic, updater/deploy dry runs, input fuzzing, code quality, staff interaction feel.
