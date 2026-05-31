// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PakonApp",
    platforms: [.macOS(.v13)],
    targets: [
        // ObjC-only target: IOUSBHost transport layer
        .target(
            name: "PakonUSBObjC",
            path: "ObjC",
            publicHeadersPath: "include",
            linkerSettings: [
                .linkedFramework("IOUSBHost"),
                .linkedFramework("IOKit"),
                .linkedFramework("Foundation"),
            ]
        ),
        // Swift SwiftUI app
        .executableTarget(
            name: "PakonApp",
            dependencies: ["PakonUSBObjC"],
            path: "Sources/PakonApp",
            resources: [
                .copy("Resources/36frames.pakscan"),
                .copy("Resources/advance.pakscan"),
            ]
        ),
    ]
)
