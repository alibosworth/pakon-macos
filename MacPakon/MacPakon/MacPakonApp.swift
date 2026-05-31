import SwiftUI

@main
struct MacPakonApp: App {
    var body: some Scene {
        WindowGroup("Pakon Scanner") {
            ContentView()
        }
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

        WindowGroup("Process Scan", id: "processing") {
            ProcessingView()
        }
        .defaultSize(width: 1000, height: 700)
    }
}
