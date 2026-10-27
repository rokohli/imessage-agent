import AppKit
import Contacts

// Only an explicit authorize action may prompt. Export never writes to Contacts.
let app = NSApplication.shared
let args = CommandLine.arguments
func finish(_ value: [String: Any]) -> Never {
    if args.count == 3, let data = try? JSONSerialization.data(withJSONObject: value) {
        FileManager.default.createFile(atPath: args[2], contents: data,
                                      attributes: [.posixPermissions: 0o600])
    }
    exit(0)
}
func readContacts() {
    let status = CNContactStore.authorizationStatus(for: .contacts)
    let allowed = status == .authorized
    guard allowed else {
        finish(["status": status == .notDetermined ? "not_determined" : "denied", "contacts": []])
    }
    if args[1] != "export" { finish(["status": "authorized", "contacts": []]) }
    let keys = [CNContactIdentifierKey, CNContactGivenNameKey, CNContactFamilyNameKey,
                CNContactOrganizationNameKey, CNContactPhoneNumbersKey, CNContactEmailAddressesKey]
    let request = CNContactFetchRequest(keysToFetch: keys as [CNKeyDescriptor])
    var rows = [[String: Any]]()
    do {
        try CNContactStore().enumerateContacts(with: request) { contact, _ in
            let personal = [contact.givenName, contact.familyName].filter { !$0.isEmpty }.joined(separator: " ")
            rows.append(["id": contact.identifier,
                         "name": personal.isEmpty ? contact.organizationName : personal,
                         "handles": contact.phoneNumbers.map { $0.value.stringValue }
                             + contact.emailAddresses.map { String($0.value) }])
        }
        finish(["status": "authorized", "contacts": rows])
    } catch { finish(["status": "unavailable", "contacts": []]) }
}
guard args.count == 3, ["status", "authorize", "export"].contains(args[1]) else { exit(1) }
if args[1] == "authorize" && CNContactStore.authorizationStatus(for: .contacts) == .notDetermined {
    CNContactStore().requestAccess(for: .contacts) { _, _ in
        DispatchQueue.main.async { readContacts() }
    }
    app.run()
} else {
    readContacts()
}
