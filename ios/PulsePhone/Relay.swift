import Foundation
import Security
import UIKit

/// Posts readings to your relay (forbandelse.com/push). Paired by the QR code on the pulseSSS Setup page,
/// which holds `pulsesss://pair?relay=<https url>&key=<phone key>`. The key lives in the Keychain.
///
/// In the background iOS only gives the app a few seconds per Bluetooth wake-up, so each reading becomes
/// one short HTTPS request instead of an open connection. The relay answers how often to send:
/// often while someone is watching, every 15 s otherwise (keeps Cloudflare's free plan comfortable).
final class Relay: ObservableObject {
    enum Health: Equatable { case unpaired, idle, ok, offline, rejected }

    @Published private(set) var relayURL: URL?
    @Published private(set) var health: Health = .unpaired
    @Published private(set) var viewers = 0
    @Published private(set) var lastSent: Date?

    private var every: TimeInterval = 2
    private var sending = false
    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 8
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    init() {
        if let raw = UserDefaults.standard.string(forKey: "relayURL"), let url = URL(string: raw), Keychain.read() != nil {
            relayURL = url
            health = .idle
        }
    }

    var paired: Bool { relayURL != nil }
    var host: String { relayURL?.host ?? "" }

    /// Handles a pairing link. Returns false if it isn't one.
    @discardableResult
    func pair(with link: URL) -> Bool {
        guard link.scheme == "pulsesss", link.host == "pair",
              let items = URLComponents(url: link, resolvingAgainstBaseURL: false)?.queryItems,
              let relayText = items.first(where: { $0.name == "relay" })?.value,
              let relay = URL(string: relayText), relay.scheme == "https" || relay.host == "127.0.0.1",
              let key = items.first(where: { $0.name == "key" })?.value, key.count >= 16 else { return false }
        Keychain.save(key)
        UserDefaults.standard.set(relay.absoluteString, forKey: "relayURL")
        relayURL = relay
        health = .idle
        every = 2
        return true
    }

    func unpair() {
        Keychain.delete()
        UserDefaults.standard.removeObject(forKey: "relayURL")
        relayURL = nil
        health = .unpaired
    }

    /// A new reading. Sent if enough time has passed since the last post.
    func reading(_ bpm: Int) {
        if let last = lastSent, Date().timeIntervalSince(last) < every - 0.2 { return }
        post(bpm: bpm)
    }

    /// The Air was released or lost: tell the relay so the site switches to "away" immediately.
    func stopped() {
        post(bpm: nil)
    }

    private func post(bpm: Int?) {
        guard let base = relayURL, let key = Keychain.read(), !sending || bpm == nil else { return }
        var request = URLRequest(url: base.appendingPathComponent("push"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["bpm": bpm.map { $0 as Any } ?? NSNull()]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)

        sending = true
        lastSent = Date()
        // Finish the request even if iOS is about to suspend the app.
        var task: UIBackgroundTaskIdentifier = .invalid
        task = UIApplication.shared.beginBackgroundTask(withName: "pulse-push") {
            UIApplication.shared.endBackgroundTask(task)
            task = .invalid
        }
        session.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                defer {
                    if task != .invalid { UIApplication.shared.endBackgroundTask(task) }
                }
                guard let self else { return }
                self.sending = false
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                if status == 401 { self.health = .rejected; return }
                guard error == nil, status == 200, let data,
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    self.health = .offline
                    return
                }
                self.health = .ok
                self.viewers = json["viewers"] as? Int ?? 0
                if let every = json["every"] as? Double { self.every = max(1, min(every, 60)) }
            }
        }.resume()
    }
}

/// The phone key, kept in the Keychain (stays on this device, survives app updates).
enum Keychain {
    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "com.forbandelse.pulsesss",
        kSecAttrAccount as String: "phoneKey",
    ]

    static func save(_ value: String) {
        delete()
        var item = query
        item[kSecValueData as String] = Data(value.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock  // readable in the background
        SecItemAdd(item as CFDictionary, nil)
    }

    static func read() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete() {
        SecItemDelete(query as CFDictionary)
    }
}
