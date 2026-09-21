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

## One-time setup

**1. Join.** https://developer.apple.com/programs/ . Individual is fine.

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

## The one thing notarisation does not fix

Prism's Mac build runs `/usr/bin/python3`, which on a Mac without Xcode or the
Command Line Tools is a stub that prompts to install them rather than a working
interpreter. Notarisation has nothing to say about that. Bundling the
interpreter with PyInstaller, the way the Windows and Linux builds already do,
is the fix, and it is a separate job.
