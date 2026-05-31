import SwiftUI

@main
struct PakonApp: App {
    var body: some Scene {
        WindowGroup("Pakon Scanner") {
            ContentView()
        }
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }
    }
}
