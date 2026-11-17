import SwiftUI
import UIKit

/// Owns the Bluetooth side and the relay, wired together once at launch. iOS may relaunch the app in
/// the background for Bluetooth events (state restoration) without showing any screen, so this must not
/// depend on the UI appearing.
final class AppModel {
    static let shared = AppModel()
    let heart = HeartRate()
    let relay = Relay()

    private init() {
        heart.onReading = { [relay] bpm in relay.reading(bpm) }
        heart.onStopped = { [relay] in relay.stopped() }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        _ = AppModel.shared  // creates the Bluetooth manager right away, as restoration requires
        return true
    }
}

@main
struct PulsePhoneApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var pairMessage: String?

    var body: some Scene {
        WindowGroup {
            ContentView(heart: AppModel.shared.heart, relay: AppModel.shared.relay, pairMessage: $pairMessage)
                .onOpenURL { url in
                    pairMessage = AppModel.shared.relay.pair(with: url)
                        ? "Paired with \(AppModel.shared.relay.host)."
                        : "That link isn't a pulseSSS pairing code."
                }
        }
    }
}
