# GitHub release setup

This project publishes `AtoZ-Voice-Studio-Setup.exe` when a version tag is pushed.

1. Commit and push the source to `crowsley/atoz-voice-studio`.
2. Ensure the version in `version.json` and `installer.iss` matches the tag.
3. Push the release tag:

       git tag v0.7.0
       git push origin v0.7.0

GitHub Actions builds the standalone app, creates the installer and attaches it to the release.

For optional Authenticode signing, add `WINDOWS_CERTIFICATE_BASE64` and `WINDOWS_CERTIFICATE_PASSWORD` as repository secrets. Unsigned installers work, but Windows may display a SmartScreen warning.
