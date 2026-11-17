import SwiftUI

/// Same look as forbandelse.com: plain monospace, light grey on near-black.
private enum Look {
    static let bg = Color(red: 0x11 / 255, green: 0x11 / 255, blue: 0x11 / 255)
    static let text = Color(white: 0xAA / 255)
    static let dim = Color(white: 0x77 / 255)
    static let faint = Color(white: 0x3A / 255)
    static func mono(_ size: CGFloat) -> Font { .system(size: size, weight: .regular, design: .monospaced) }
}

struct ContentView: View {
    @ObservedObject var heart: HeartRate
    @ObservedObject var relay: Relay
    @Binding var pairMessage: String?

    var body: some View {
        ZStack {
            Look.bg.ignoresSafeArea()
            VStack(spacing: 0) {
                Spacer()
                bigNumber
                Text(heartLine).font(Look.mono(13)).foregroundColor(Look.dim).padding(.top, 18)
                Text(relayLine).font(Look.mono(13)).foregroundColor(Look.dim).padding(.top, 4)
                Spacer()
                controls
                Text("pulseSSS").font(Look.mono(11)).foregroundColor(Look.faint).padding(.top, 22)
            }
            .padding(.horizontal, 24)
            .padding(.bottom, 16)
        }
        .preferredColorScheme(.dark)
    }

    private var bigNumber: some View {
        HStack(alignment: .lastTextBaseline, spacing: 10) {
            if let bpm = heart.bpm {
                Text("\(bpm)").font(Look.mono(96)).foregroundColor(Look.text)
                Text("bpm").font(Look.mono(13)).foregroundColor(Look.dim)
            } else {
                Text("away").font(Look.mono(13)).foregroundColor(Look.dim)
            }
        }
        .frame(minHeight: 110)
    }

    private var heartLine: String {
        switch heart.state {
        case .starting: return "starting"
        case .released: return "air released (your pc can take it)"
        case .bluetoothOff: return "bluetooth is off"
        case .unauthorized: return "allow bluetooth in settings > pulsesss"
        case .scanning: return "looking for your air. share heart rate on?"
        case .connecting: return "connecting to \(name). can take 30 s"
        case .connected: return "connected to \(name)"
        }
    }

    private var name: String {
        (heart.deviceName ?? "the air").replacingOccurrences(of: "Google ", with: "").lowercased()
    }

    private var relayLine: String {
        if let pairMessage { return pairMessage.lowercased() }
        switch relay.health {
        case .unpaired: return "not paired. scan the qr in pulsesss > setup"
        case .idle: return "sending to \(relay.host)"
        case .ok: return relay.viewers > 0 ? "live on \(relay.host) · \(relay.viewers) watching" : "live on \(relay.host)"
        case .offline: return "can't reach \(relay.host), retrying"
        case .rejected: return "pairing no longer valid. pair again"
        }
    }

    private var controls: some View {
        VStack(spacing: 12) {
            Button(action: { heart.state == .released ? heart.hold() : heart.release() }) {
                Text(heart.state == .released ? "take the air" : "release the air")
                    .font(Look.mono(14))
                    .foregroundColor(Look.text)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Look.faint, lineWidth: 1))
            }
            if relay.paired {
                Button(action: { relay.unpair(); pairMessage = nil }) {
                    Text("unpair").font(Look.mono(12)).foregroundColor(Look.faint)
                }
            }
        }
    }
}
