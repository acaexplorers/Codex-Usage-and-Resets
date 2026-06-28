import AppKit
import Foundation

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var summary: [String: Any]?
    private var errorMessage: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.isVisible = true
        configureStatusButton(image: menuBarIcon(), tintColor: NSColor.systemBlue, tooltip: "Codex Usage: loading")

        rebuildMenu()
        refreshNow()

        timer = Timer.scheduledTimer(
            timeInterval: 300,
            target: self,
            selector: #selector(refreshNow),
            userInfo: nil,
            repeats: true
        )
    }

    @objc private func refreshNow() {
        configureStatusButton(image: menuBarIcon(), tintColor: NSColor.systemBlue, tooltip: "Codex Usage: refreshing")

        DispatchQueue.global(qos: .utility).async {
            do {
                let data = try self.runHelper(arguments: ["--summary-json"])
                let parsed = try JSONSerialization.jsonObject(with: data, options: [])
                let dict = parsed as? [String: Any]

                DispatchQueue.main.async {
                    self.summary = dict
                    self.errorMessage = nil
                    self.configureStatusButton(
                        image: self.menuBarIcon(),
                        tintColor: NSColor.systemBlue,
                        tooltip: self.titleText()
                    )
                    self.rebuildMenu()
                }
            } catch {
                DispatchQueue.main.async {
                    self.summary = nil
                    self.errorMessage = error.localizedDescription
                    self.configureStatusButton(
                        image: self.menuBarErrorIcon(),
                        tintColor: NSColor.systemRed,
                        tooltip: "Codex Usage: unavailable"
                    )
                    self.rebuildMenu()
                }
            }
        }
    }

    private func configureStatusButton(image: NSImage, tintColor: NSColor, tooltip: String) {
        guard let button = statusItem.button else { return }
        button.title = ""
        button.image = image
        button.imagePosition = .imageOnly
        button.imageScaling = .scaleProportionallyDown
        button.contentTintColor = tintColor
        button.toolTip = tooltip
        button.setAccessibilityLabel("Codex Usage")
        button.setAccessibilityTitle("Codex Usage")
        button.setAccessibilityHelp(tooltip)
    }

    private func titleText() -> String {
        guard let summary else { return "Codex Usage" }
        let usage = summary["usage"] as? [[String: Any]] ?? []
        let resets = summary["resets"] as? [String: Any] ?? [:]
        var parts: [String] = []

        for window in usage {
            let label = window["label"] as? String ?? ""
            let remaining = window["remainingPercent"] as? Int ?? 0
            if label == "5 hour" {
                parts.append("5h \(remaining)%")
            } else if label == "Weekly" {
                parts.append("Weekly \(remaining)%")
            }
        }

        let available = resets["availableCount"] as? Int ?? 0
        parts.append("\(available) resets")

        return "Codex Usage: " + parts.joined(separator: ", ")
    }

    private func menuBarIcon() -> NSImage {
        return symbolIcon(named: "c.circle.fill", fallbackColor: NSColor.systemBlue, fallbackLabel: "C")
    }

    private func menuBarErrorIcon() -> NSImage {
        return symbolIcon(named: "exclamationmark.circle.fill", fallbackColor: NSColor.systemRed, fallbackLabel: "!")
    }

    private func symbolIcon(named name: String, fallbackColor: NSColor, fallbackLabel: String) -> NSImage {
        if let symbol = NSImage(systemSymbolName: name, accessibilityDescription: "Codex Usage") {
            let config = NSImage.SymbolConfiguration(pointSize: 18, weight: .semibold)
            let image = symbol.withSymbolConfiguration(config) ?? symbol
            image.isTemplate = true
            return image
        }
        return badgeIcon(color: fallbackColor, label: fallbackLabel)
    }

    private func badgeIcon(color: NSColor, label: String) -> NSImage {
        let size = NSSize(width: 18, height: 18)
        let image = NSImage(size: size)
        image.lockFocus()

        NSColor.clear.setFill()
        NSRect(origin: .zero, size: size).fill()

        let rect = NSRect(x: 1.5, y: 1.5, width: 15, height: 15)
        let path = NSBezierPath(ovalIn: rect)
        color.setFill()
        path.fill()

        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: label == "C" ? 10 : 11, weight: .bold),
            .foregroundColor: NSColor.white,
        ]
        let textSize = label.size(withAttributes: attrs)
        let textRect = NSRect(
            x: (size.width - textSize.width) / 2,
            y: (size.height - textSize.height) / 2 - 0.5,
            width: textSize.width,
            height: textSize.height
        )
        label.draw(in: textRect, withAttributes: attrs)
        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    private func rebuildMenu() {
        let menu = NSMenu()

        if let errorMessage {
            addInfo("Codex usage unavailable", to: menu, weight: .semibold, color: NSColor.systemRed)
            addInfo(errorMessage, to: menu, color: NSColor.secondaryLabelColor)
        } else if let summary {
            let usage = summary["usage"] as? [[String: Any]] ?? []
            let resets = summary["resets"] as? [String: Any] ?? [:]

            addInfo("Codex Usage", to: menu, weight: .semibold)
            menu.addItem(NSMenuItem.separator())

            for window in usage {
                let label = window["label"] as? String ?? "Usage"
                let remaining = window["remainingPercent"] as? Int ?? 0
                let resetsAt = window["resetsAt"] as? String ?? "unknown"
                addInfo(
                    "\(label): \(remaining)% left",
                    to: menu,
                    weight: .semibold,
                    color: usageColor(for: remaining)
                )
                addInfo("Resets \(resetsAt)", to: menu, color: NSColor.secondaryLabelColor)
            }

            menu.addItem(NSMenuItem.separator())
            let available = resets["availableCount"] as? Int ?? 0
            let resetWord = available == 1 ? "reset" : "resets"
            addInfo("\(available) available \(resetWord)", to: menu, weight: .semibold)

            let resetItems = resets["items"] as? [[String: Any]] ?? []
            if resetItems.isEmpty {
                if let expiry = resets["nextExpiry"] as? String {
                    addInfo("Expires \(expiry)", to: menu)
                }
                if let timeLeft = resets["nextTimeLeft"] as? String {
                    addInfo("Time left \(timeLeft)", to: menu, color: NSColor.secondaryLabelColor)
                }
            } else {
                for item in resetItems {
                    let number = item["number"] as? Int ?? 0
                    let title = item["title"] as? String ?? "Codex reset"
                    let expiry = item["expires"] as? String ?? "unknown"
                    let timeLeft = item["timeLeft"] as? String ?? "unknown"
                    addInfo("Reset \(number): \(title)", to: menu, weight: .semibold)
                    addInfo("Expires \(expiry)", to: menu)
                    addInfo("Time left \(timeLeft)", to: menu, color: NSColor.secondaryLabelColor)
                }
            }

            menu.addItem(NSMenuItem.separator())
            addInfo("Checked \(summary["checkedAt"] as? String ?? "unknown")", to: menu, color: NSColor.secondaryLabelColor)
        } else {
            addInfo("Loading Codex usage...", to: menu, color: NSColor.secondaryLabelColor)
        }

        menu.addItem(NSMenuItem.separator())
        addAction("Refresh Now", selector: #selector(refreshNow), key: "r", to: menu)
        addAction("Open Dashboard Snapshot", selector: #selector(openDashboardSnapshot), key: "o", to: menu)

        menu.addItem(NSMenuItem.separator())
        addAction("Quit", selector: #selector(quit), key: "q", to: menu)

        statusItem.menu = menu
    }

    private func usageColor(for remaining: Int) -> NSColor {
        if remaining <= 10 {
            return NSColor.systemRed
        }
        if remaining <= 25 {
            return NSColor.systemOrange
        }
        return NSColor.labelColor
    }

    private func addInfo(
        _ title: String,
        to menu: NSMenu,
        weight: NSFont.Weight = .regular,
        color: NSColor = NSColor.labelColor
    ) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .font: NSFont.systemFont(ofSize: 14, weight: weight),
                .foregroundColor: color,
            ]
        )
        item.isEnabled = false
        menu.addItem(item)
    }

    private func addAction(_ title: String, selector: Selector, key: String, to menu: NSMenu) {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        item.target = self
        menu.addItem(item)
    }

    @objc private func openDashboardSnapshot() {
        DispatchQueue.global(qos: .utility).async {
            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent("codex-reset-expiry-dashboard.html")
            _ = try? self.runHelper(arguments: ["--html", destination.path, "--quiet"])
            DispatchQueue.main.async {
                NSWorkspace.shared.open(destination)
            }
        }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func runHelper(arguments: [String]) throws -> Data {
        guard let helperURL = Bundle.main.resourceURL?.appendingPathComponent("codex-reset-expiry.py") else {
            throw NSError(
                domain: "CodexUsageMenuBar",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Missing bundled helper"]
            )
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [helperURL.path] + arguments

        let output = Pipe()
        let errorOutput = Pipe()
        process.standardOutput = output
        process.standardError = errorOutput
        try process.run()
        process.waitUntilExit()

        let data = output.fileHandleForReading.readDataToEndOfFile()
        if process.terminationStatus != 0 {
            let errorData = errorOutput.fileHandleForReading.readDataToEndOfFile()
            let message = String(data: errorData, encoding: .utf8) ?? "Helper failed"
            throw NSError(
                domain: "CodexUsageMenuBar",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: message]
            )
        }
        return data
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
