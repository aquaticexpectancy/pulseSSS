"""Write altstore/source.json for a new pulseSSS Phone build (run by the GitHub workflow).

AltStore checks the listed permissions against the .ipa and refuses to install on a mismatch, so the
privacy text below must match NSBluetoothAlwaysUsageDescription in ios/project.yml.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source.json"
BUNDLE_ID = "com.forbandelse.pulsesss"
BLUETOOTH_TEXT = ("pulseSSS connects to your Fitbit Air to read your heart rate and send it to your site and PC.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--ipa", required=True)
    ap.add_argument("--repo", required=True, help="owner/name on GitHub")
    ap.add_argument("--tag", required=True)
    a = ap.parse_args()

    raw = f"https://raw.githubusercontent.com/{a.repo}/main"
    old = json.loads(SOURCE.read_text(encoding="utf-8")) if SOURCE.exists() else {}
    old_versions = old.get("apps", [{}])[0].get("versions", []) if old.get("apps") else []

    version = {
        "version": a.version,
        "buildVersion": a.build,
        "date": dt.date.today().isoformat(),
        "localizedDescription": f"Build {a.build}.",
        "downloadURL": f"https://github.com/{a.repo}/releases/download/{a.tag}/pulseSSS-Phone.ipa",
        "size": Path(a.ipa).stat().st_size,
        "minOSVersion": "16.0",
    }
    source = {
        "name": "pulseSSS",
        "subtitle": "Live heart rate from your Fitbit Air",
        "description": "The pulseSSS Phone app: holds your Fitbit Air and sends your heart rate to your own relay.",
        "iconURL": f"{raw}/ios/icon-180.png",
        "website": f"https://github.com/{a.repo}",
        "tintColor": "#AAAAAA",
        "apps": [{
            "name": "pulseSSS Phone",
            "bundleIdentifier": BUNDLE_ID,
            "developerName": "forbandelse",
            "subtitle": "Heart rate to your site and PC, in the background.",
            "localizedDescription": ("Connects to your Fitbit Air over Bluetooth and sends your heart rate to your "
                                     "pulseSSS relay, which shows it on your site and in pulseSSS on your PC. "
                                     "Keeps working in the background. Pair it with the QR code on the pulseSSS "
                                     "Setup page."),
            "iconURL": f"{raw}/ios/icon-180.png",
            "tintColor": "#AAAAAA",
            "category": "utilities",
            "versions": [version] + [v for v in old_versions if v.get("version") != a.version][:9],
            "appPermissions": {
                "entitlements": [],
                "privacy": {"NSBluetoothAlwaysUsageDescription": BLUETOOTH_TEXT},
            },
        }],
        "news": [],
    }
    SOURCE.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print(f"source.json -> {a.version} ({version['size']} bytes)")


if __name__ == "__main__":
    main()
