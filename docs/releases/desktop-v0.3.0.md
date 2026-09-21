# macOS desktop companion v0.3.0 — early access

Apple Silicon, macOS 14+. Ad-hoc signed, not notarized. Windows remains pinned
to desktop-v0.2.0; Linux native capture is deferred.

Update your Showrun checkout/install, then run `showrun prepare-desktop` and
`showrun desktop-status`. No compiler is required. The installer verifies the
pinned SHA-256 and signature before replacing the app.

Editable combo boxes now support fill and native Confirm. Text areas without
AXValue writes support exact, immediate single-line input only after their focused
contents are verified empty. Focus recovery uses hit-tested mouse clicks, and
rich-text readback uses the accessibility text-range API. Input does not expose
clipboard access, arbitrary shortcuts or Return; commit with an observed button.

Verified with Excel: recorded Name Box selection, formula-bar entry and commit
created Drink/Count, Water/7, Juice/9, Sum/16. Independent saved-XLSX inspection
confirmed numeric inputs and SUM(B2:B3), cached as 16. Existing failed takes were
preserved. Populated-editor replacement, paced Mac typing and general grid support
are not claimed. Saving can change a window title and end a bound take.

Ad-hoc updates may invalidate macOS permissions even while Settings switches show
on. Remove and re-add Showrun Desktop in Accessibility and Screen & System Audio
Recording, then enable it and recheck status. Pause typing during native takes.

Asset: `showrun-desktop-macos-arm64.zip`

SHA-256: `1380c6ad07910120f5ce6ff31772b5f43df2612261f7180ed899168fd2f4179b`
