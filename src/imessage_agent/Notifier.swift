import AppKit
import UserNotifications

// A real app identity is required by UserNotifications when launched without Terminal.
final class Delegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let args = CommandLine.arguments
        let event = args.count >= 2 ? args[1] : ""
        if event == "authorize" {
            center.requestAuthorization(options: [.alert, .sound]) { _, _ in
                DispatchQueue.main.async { NSApp.terminate(nil) }
            }
            return
        }
        let messages = [
            "accepted": "Scheduled message accepted. Delivery is unverified.",
            "sent": "Scheduled message sent.",
            "delivered": "Scheduled message delivered.",
            "failed": "Scheduled message failed. Review local activity.",
            "partial": "Scheduled message requires attention. Do not retry automatically.",
            "unknown": "Scheduled outcome UNKNOWN. Do not retry.",
            "missed": "Scheduled message missed its authorized window.",
            "approval": "A message requires your approval.",
            "attention": "The background worker requires attention."
        ]
        guard let message = messages[event] else { NSApp.terminate(nil); return }
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized else {
                DispatchQueue.main.async { NSApp.terminate(nil) }; return
            }
            let content = UNMutableNotificationContent()
            content.title = "iMessage Agent"
            content.body = args.count == 3 ? args[2] + ": " + message : message
            center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)) { _ in
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) { NSApp.terminate(nil) }
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { NSApp.terminate(nil) }
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
