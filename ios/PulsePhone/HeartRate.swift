import CoreBluetooth
import Foundation

/// Holds the Fitbit Air (or any standard Bluetooth heart-rate device) and reads its heart rate.
///
/// Runs in the background thanks to the `bluetooth-central` background mode: iOS keeps the connection
/// and wakes the app for each reading. State restoration lets iOS relaunch the app for Bluetooth
/// events if it was closed by the system (not if you swipe it away).
final class HeartRate: NSObject, ObservableObject {
    enum State: Equatable {
        case starting, released, bluetoothOff, unauthorized, scanning, connecting, connected
    }

    @Published private(set) var state: State = .starting
    @Published private(set) var bpm: Int?
    @Published private(set) var lastReading: Date?
    @Published private(set) var deviceName: String?

    /// Called on every reading (main queue).
    var onReading: ((Int) -> Void)?
    /// Called when the Air is released or lost, so the relay can show "away" right away.
    var onStopped: (() -> Void)?

    /// Whether you want the phone to hold the Air. Off = released, so the PC can take it.
    private(set) var wanted: Bool {
        get { UserDefaults.standard.object(forKey: "holdAir") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "holdAir") }
    }

    private static let hrService = CBUUID(string: "180D")
    private static let hrMeasurement = CBUUID(string: "2A37")
    private static let savedIDKey = "airIdentifier"

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil,
                                   options: [CBCentralManagerOptionRestoreIdentifierKey: "pulsesss.central"])
    }

    // MARK: - Your controls

    func hold() {
        wanted = true
        if central.state == .poweredOn { connectOrScan() }
    }

    func release() {
        wanted = false
        central.stopScan()
        if let p = peripheral { central.cancelPeripheralConnection(p) }
        state = .released
        bpm = nil
        onStopped?()
    }

    // MARK: - Connecting

    private func connectOrScan() {
        guard wanted else { state = .released; return }
        // A device the system already has connected (e.g. after a restore).
        if peripheral == nil, let p = central.retrieveConnectedPeripherals(withServices: [Self.hrService]).first {
            use(p)
        }
        // The Air we used last time: a pending connect waits (in the background too) until it shows up.
        if peripheral == nil, let raw = UserDefaults.standard.string(forKey: Self.savedIDKey),
           let id = UUID(uuidString: raw), let p = central.retrievePeripherals(withIdentifiers: [id]).first {
            use(p)
        }
        if let p = peripheral, p.state != .connected {
            central.connect(p, options: nil)
            state = .connecting
        }
        // Scan as well: the Air only advertises while "Share heart rate" is on, and may change address.
        // A service filter is required for scanning in the background.
        if peripheral?.state != .connected {
            central.scanForPeripherals(withServices: [Self.hrService], options: nil)
            if peripheral == nil { state = .scanning }
        }
    }

    private func use(_ p: CBPeripheral) {
        peripheral = p
        p.delegate = self
        deviceName = p.name
        UserDefaults.standard.set(p.identifier.uuidString, forKey: Self.savedIDKey)
    }

    private static func parse(_ data: Data) -> Int? {
        // Heart Rate Measurement: bit 0 of the flags says whether the value is 8 or 16 bits.
        guard let flags = data.first else { return nil }
        if flags & 0x01 != 0 {
            guard data.count >= 3 else { return nil }
            return Int(data[data.startIndex + 1]) | (Int(data[data.startIndex + 2]) << 8)
        }
        guard data.count >= 2 else { return nil }
        return Int(data[data.startIndex + 1])
    }
}

extension HeartRate: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn: connectOrScan()
        case .unauthorized: state = .unauthorized
        case .poweredOff: state = .bluetoothOff; bpm = nil
        default: break
        }
    }

    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        if let restored = (dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral])?.first {
            use(restored)
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        guard wanted else { return }
        if let current = peripheral, current.identifier != p.identifier, current.state == .connected { return }
        central.stopScan()
        if let current = peripheral, current.identifier != p.identifier { central.cancelPeripheralConnection(current) }
        use(p)
        state = .connecting
        central.connect(p, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didConnect p: CBPeripheral) {
        central.stopScan()
        deviceName = p.name
        state = .connecting  // until the first reading; the Air takes up to 30 s to "prepare to share"
        p.discoverServices([Self.hrService])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        guard wanted else { return }
        central.connect(p, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        bpm = nil
        onStopped?()
        guard wanted else { state = .released; return }
        // Try again; this waits in the background until the Air is back (or the PC lets go of it).
        state = .connecting
        central.connect(p, options: nil)
        central.scanForPeripherals(withServices: [Self.hrService], options: nil)
    }
}

extension HeartRate: CBPeripheralDelegate {
    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard let service = p.services?.first(where: { $0.uuid == Self.hrService }) else { return }
        p.discoverCharacteristics([Self.hrMeasurement], for: service)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard let ch = service.characteristics?.first(where: { $0.uuid == Self.hrMeasurement }) else { return }
        p.setNotifyValue(true, for: ch)
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        guard ch.uuid == Self.hrMeasurement, let data = ch.value, let value = Self.parse(data),
              value > 20, value < 260 else { return }
        bpm = value
        lastReading = Date()
        state = .connected
        onReading?(value)
    }
}
