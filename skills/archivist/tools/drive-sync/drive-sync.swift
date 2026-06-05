// drive-sync — a tiny macOS menu-bar app that keeps a git working tree (typically
// one living inside a cloud-sync folder like Google Drive / iCloud / Dropbox) in
// sync with its upstream branch: fast-forward when it's cleanly on the tracked
// branch, otherwise PAUSE and alert — never discarding local work.
//
// It is config-driven; NOTHING here is host-specific. Configuration is read at
// launch from JSON at either $DRIVESYNC_CONFIG or
//   ~/Library/Application Support/<AppName>/config.json
// where <AppName> is the bundle's CFBundleName. Keys (only `repo` is required):
//   { "repo": "/abs/path/to/working/tree",
//     "remote": "origin", "branch": "main", "intervalSeconds": 900,
//     "git": "/usr/bin/git", "path": "/opt/homebrew/bin:/usr/bin:/bin" }
//
// Why a GUI menu-bar app (not a background launchd script): a background launchd
// agent is denied access to provider-backed folders (~/Library/CloudStorage, …)
// by macOS TCC, so it would need Full Disk Access. A normal Aqua-session app has
// that access natively — so installed as a login/menu-bar item this needs NO
// Full Disk Access grant.

import Cocoa
import UserNotifications

let APP_NAME = (Bundle.main.infoDictionary?["CFBundleName"] as? String) ?? "DriveSync"
let LOG    = NSHomeDirectory() + "/Library/Logs/\(APP_NAME).log"
let STATUS = NSHomeDirectory() + "/Library/Logs/\(APP_NAME).status"
let FDA_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

struct Config {
    var repo: String
    var remote: String
    var branch: String
    var interval: TimeInterval
    var git: String
    var path: String

    static func load() -> Config? {
        let path = ProcessInfo.processInfo.environment["DRIVESYNC_CONFIG"]
            ?? NSHomeDirectory() + "/Library/Application Support/\(APP_NAME)/config.json"
        guard let data = FileManager.default.contents(atPath: path),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let repo = obj["repo"] as? String, !repo.isEmpty else { return nil }
        func s(_ k: String, _ d: String) -> String { (obj[k] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? d }
        let interval = (obj["intervalSeconds"] as? NSNumber)?.doubleValue ?? 900
        return Config(repo: repo,
                      remote: s("remote", "origin"),
                      branch: s("branch", "main"),
                      interval: interval,
                      git: s("git", "/usr/bin/git"),
                      path: s("path", "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"))
    }
}

var cfg: Config?

enum SyncState: Equatable {
    case upToDate, synced(Int)
    case pausedDirty(Int), pausedBranch(String), pausedAhead(Int)
    case blocked, offline, notConfigured, error(String)
}

@discardableResult
func git(_ args: [String]) -> (code: Int32, out: String, err: String) {
    guard let c = cfg else { return (-1, "", "no config") }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: c.git)
    p.arguments = ["-C", c.repo] + args
    var env = ProcessInfo.processInfo.environment
    env["PATH"] = c.path
    env["HOME"] = NSHomeDirectory()
    p.environment = env
    let o = Pipe(), e = Pipe()
    p.standardOutput = o; p.standardError = e
    do { try p.run() } catch { return (-1, "", "spawn failed: \(error)") }
    let od = o.fileHandleForReading.readDataToEndOfFile()
    let ed = e.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    let trim: (Data) -> String = { (String(data: $0, encoding: .utf8) ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
    return (p.terminationStatus, trim(od), trim(ed))
}

func performSync() -> SyncState {
    guard let c = cfg else { return .notConfigured }
    if !FileManager.default.fileExists(atPath: c.repo) { return .offline }
    let probe = git(["rev-parse", "--git-dir"])
    if probe.code != 0 {
        let e = probe.err.lowercased()
        if e.contains("operation not permitted") || e.contains("permission denied") { return .blocked }
        return .offline
    }
    if git(["fetch", "-q", c.remote]).code != 0 { return .offline }
    let upstream = "\(c.remote)/\(c.branch)"
    let branch = git(["symbolic-ref", "--short", "-q", "HEAD"]).out
    let dirty  = git(["status", "--porcelain"]).out
    let ahead  = Int(git(["rev-list", "--count", "\(upstream)..HEAD"]).out) ?? 0
    let behind = Int(git(["rev-list", "--count", "HEAD..\(upstream)"]).out) ?? 0
    if branch != c.branch { return .pausedBranch(branch.isEmpty ? "detached HEAD" : branch) }
    if !dirty.isEmpty     { return .pausedDirty(dirty.split(separator: "\n").count) }
    if ahead > 0          { return .pausedAhead(ahead) }
    if behind > 0 {
        let m = git(["merge", "--ff-only", upstream])
        return m.code == 0 ? .synced(behind) : .error(m.err.isEmpty ? "fast-forward failed" : m.err)
    }
    return .upToDate
}

func logLine(_ msg: String) {
    let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    guard let data = "\(f.string(from: Date()))  \(msg)\n".data(using: .utf8) else { return }
    if let h = FileHandle(forWritingAtPath: LOG) { h.seekToEndOfFile(); h.write(data); try? h.close() }
    else { try? data.write(to: URL(fileURLWithPath: LOG)) }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    let statusLine = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
    var fdaItem: NSMenuItem!
    var timer: Timer?
    var busy = false
    var lastAlertKey = ""

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.accessory)
        cfg = Config.load()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        let menu = NSMenu()
        statusLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(.separator())
        menu.addItem(item("Sync Now", #selector(syncNow), "r"))
        fdaItem = item("Grant Full Disk Access…", #selector(openFDA), "")
        fdaItem.isHidden = true
        menu.addItem(fdaItem)
        menu.addItem(.separator())
        menu.addItem(item("Open Log", #selector(openLog), "l"))
        menu.addItem(item("Open Folder", #selector(openFolder), ""))
        menu.addItem(.separator())
        menu.addItem(item("Quit \(APP_NAME)", #selector(quit), "q"))
        statusItem.menu = menu
        setIcon("arrow.triangle.2.circlepath", "\(APP_NAME) — starting…")

        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }

        runSync()
        let interval = cfg?.interval ?? 900
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in self?.runSync() }
    }

    func item(_ title: String, _ sel: Selector, _ key: String) -> NSMenuItem {
        let i = NSMenuItem(title: title, action: sel, keyEquivalent: key); i.target = self; return i
    }

    func setIcon(_ symbol: String, _ tip: String) {
        guard let b = statusItem.button else { return }
        let img = NSImage(systemSymbolName: symbol, accessibilityDescription: tip)
        img?.isTemplate = true
        b.image = img
        b.toolTip = tip
    }

    @objc func syncNow() { runSync() }

    func runSync() {
        if busy { return }
        busy = true
        setIcon("arrow.triangle.2.circlepath", "\(APP_NAME) — syncing…")
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let state = performSync()
            DispatchQueue.main.async { self?.apply(state); self?.busy = false }
        }
    }

    func apply(_ state: SyncState) {
        let now = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .short)
        var icon = "checkmark.circle", status = "", log = "", alert: String? = nil, key = "", showFDA = false
        switch state {
        case .upToDate:           icon = "checkmark.circle";        status = "In sync · \(now)"
        case .synced(let n):      icon = "checkmark.circle";        status = "Synced \(n) commit\(n==1 ? "" : "s") · \(now)"; log = "OK: fast-forwarded \(n) commit(s)"
        case .pausedDirty(let n): icon = "exclamationmark.triangle"; status = "Paused: \(n) local change\(n==1 ? "" : "s") · \(now)"; log = "PAUSE: \(n) local change(s)"; key = "dirty"
                                  alert = "\(n) uncommitted change\(n==1 ? "" : "s") in the folder — auto-sync paused so nothing is lost. Commit or stash them."
        case .pausedBranch(let b):icon = "exclamationmark.triangle"; status = "Paused: on \(b) · \(now)"; log = "PAUSE: on \(b)"; key = "branch:\(b)"
                                  alert = "Folder is on '\(b)', not \(cfg?.branch ?? "the tracked branch") — auto-sync paused."
        case .pausedAhead(let n): icon = "exclamationmark.triangle"; status = "Paused: \(n) unpushed · \(now)"; log = "PAUSE: \(n) ahead"; key = "ahead"
                                  alert = "Local branch has \(n) unpushed commit\(n==1 ? "" : "s") — auto-sync paused. Push or open a PR."
        case .blocked:            icon = "lock";                    status = "Needs Full Disk Access · \(now)"; log = "BLOCKED: no access to folder"; key = "blocked"; showFDA = true
                                  alert = "\(APP_NAME) can't read the folder. If it's a background process, grant Full Disk Access; a menu-bar app usually needs none."
        case .offline:            icon = "bolt.horizontal.circle";  status = "Folder unavailable · \(now)"
        case .notConfigured:      icon = "gearshape";               status = "Not configured · \(now)"; log = "NOT CONFIGURED: missing config.json"; key = "noconfig"
                                  alert = "\(APP_NAME) has no config — set repo in ~/Library/Application Support/\(APP_NAME)/config.json"
        case .error(let m):       icon = "xmark.octagon";           status = "Error · \(now)"; log = "ERROR: \(m)"; key = "error"; alert = "\(APP_NAME) error: \(m)"
        }
        setIcon(icon, "\(APP_NAME) — \(status)")
        statusLine.title = status
        fdaItem.isHidden = !showFDA
        if !log.isEmpty { logLine(log) }
        if let msg = alert {
            if key != lastAlertKey { notify(msg); lastAlertKey = key }
        } else {
            lastAlertKey = ""
        }
        try? "\(icon)\t\(status)\n".write(toFile: STATUS, atomically: true, encoding: .utf8)
    }

    func notify(_ body: String) {
        let c = UNMutableNotificationContent(); c.title = APP_NAME; c.body = body
        let r = UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil)
        UNUserNotificationCenter.current().add(r)
    }

    @objc func openLog()    { if !FileManager.default.fileExists(atPath: LOG) { FileManager.default.createFile(atPath: LOG, contents: nil) }; NSWorkspace.shared.open(URL(fileURLWithPath: LOG)) }
    @objc func openFolder() { if let c = cfg { NSWorkspace.shared.open(URL(fileURLWithPath: c.repo)) } }
    @objc func openFDA()    { if let u = URL(string: FDA_URL) { NSWorkspace.shared.open(u) } }
    @objc func quit()       { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
