import SwiftUI
import Combine

@MainActor
final class ProcessingRouter: ObservableObject {
    @Published var pendingURL: URL?
}

@main
struct MacPakonApp: App {
    @StateObject private var router = ProcessingRouter()

    var body: some Scene {
        WindowGroup("Pakon Scanner") {
            ContentView()
                .environmentObject(router)
        }
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

        WindowGroup("Process Scan", id: "processing") {
            ProcessingView()
                .environmentObject(router)
        }
        .defaultSize(width: 1000, height: 700)
    }
}
