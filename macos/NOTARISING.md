# Notarising Prism for macOS

Since macOS 15 an app that is not notarised is blocked when it arrives over the
internet, and the old right-click-Open bypass is gone. Users see a warning that
says the app is damaged or cannot be checked for malware, and the only route
through is System Settings. Most people stop there, and they are right to.

Notarisation fixes it permanently. Apple scans the app, issues a ticket, and
`stapler` writes that ticket into the bundle so it opens on a Mac that has never
seen it and is offline.

## What it costs

**An Apple Developer Program membership, 99 USD a year.** There is no free route:
a Developer ID certificate is only issued to paid members. Everything else here
is free and takes about twenty minutes once.

One membership covers every app and both platforms, so the same enrolment signs
Mac apps and ships iOS ones. Prism is currently signed by **Tradelynx Ltd**,
which is a deliberate choice rather than a permanent one: see "Moving Prism to
another company" at the end.

## One-time setup

**1. Enrol.** https://developer.apple.com/programs/ .

Individual or Organization is the one decision that is awkward to reverse, and
it is decided by whose name you want on things:

| | Team name in the signature | Needs |
|---|---|---|
| **Individual** | your own legal name | an Apple ID, minutes |
| **Organization** | the company name | a D-U-N-S number, about a week |

Organization is the one to pick if the App Store seller should read as a company
rather than a person. It requires a
[D-U-N-S number](https://developer.apple.com/help/account/membership/D-U-N-S/)
for the legal entity, and that is the slow step: **look the company up first**,
because a registered company often already has one. If it does not, D&B issue
one free in up to 5 business days, then Apple takes another 1 to 2 to verify it.
Start that lookup before anything else, because the rest of enrolment waits on
it and nothing else in this document does.

**2. Get a Developer ID Application certificate.** In Xcode: Settings, Accounts,
add the Apple ID, select the team, Manage Certificates, `+`,
**Developer ID Application**. It lands in the login keychain. Confirm:

    security find-identity -v -p codesigning

You want a line reading `Developer ID Application: <name> (TEAMID)`. Note that
team ID. **Back the certificate up** (Keychain Access, export as .p12, keep the
private key): Apple issues a limited number and losing the key means burning one.

**3. Make an app-specific password.** https://appleid.apple.com , Sign-In and
Security, App-Specific Passwords. This is not the Apple ID password, and
notarytool will not take the real one.

**4. Store the credentials in the keychain** so no secret ever sits in a script
or in shell history:

    xcrun notarytool store-credentials "prism-notary" \
      --apple-id "you@example.com" \
      --team-id "TEAMID" \
      --password "abcd-efgh-ijkl-mnop"

## Every release after that

    ./macos/build.sh                 # picks up the Developer ID automatically
    ./macos/notarise.sh              # submits, waits, staples, verifies

`release.sh` does both when a Developer ID is present, and falls back to an
ad-hoc build with a warning when it is not, so the repo still builds for anyone
who clones it without a membership.

## Checking it actually worked

The build machine is the worst place to test, because it trusts everything it
made itself. What a user's Mac will do:

    spctl -a -vvv -t install /path/to/Prism.app
    # source=Notarized Developer ID   <- what you want
    xcrun stapler validate /path/to/Prism.app

For a genuine end-to-end test, download the release zip on a Mac that has never
built Prism and open it. The quarantine flag only gets set by a real download,
so a file copied over the network on a USB stick does not prove anything.

## If Apple rejects it

`notarise.sh` prints the reasons. The usual ones for this app:

- **The hardened runtime is missing.** `build.sh` passes `--options runtime`.
  Rebuild rather than re-signing by hand.
- **No secure timestamp.** `--timestamp`, also in `build.sh`. A signature without
  one dies with the certificate rather than outliving it.
- **A nested binary is unsigned.** Everything inside the bundle has to be signed
  with the same identity, inside out.

## Moving Prism to another company later

This is deliberately cheap to do. The bundle identifier is
`uk.co.prism.optimiser`, which names Prism and not whoever signs it, so moving
the project changes the signature and nothing else:

1. Get a Developer ID from the new team.
2. `./macos/build.sh && ./macos/notarise.sh`, exactly as before.
3. Cut a release.

Copies already downloaded keep working. A stapled ticket stays valid after the
signing certificate expires, because it records that the app was notarised at a
point when the certificate was good. The one event that would break old copies
is Apple *revoking* the certificate, which is not what happens when a membership
lapses or a project changes hands.

Keep the bundle identifier out of it. If this were `uk.co.tradelynx.prism` a
move would change the app's identity on every user's machine, which is the sort
of thing that resets preferences and confuses Gatekeeper for no reason.

## The one thing notarisation does not fix

Prism's Mac build runs `/usr/bin/python3`, which on a Mac without Xcode or the
Command Line Tools is a stub that prompts to install them rather than a working
interpreter. Notarisation has nothing to say about that. Bundling the
interpreter with PyInstaller, the way the Windows and Linux builds already do,
is the fix, and it is a separate job.
