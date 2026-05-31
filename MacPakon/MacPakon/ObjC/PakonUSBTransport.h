#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Synchronous IOUSBHost transport for the Pakon F-135 scanner.
///
/// Manages the IOUSBHostDevice + IOUSBHostInterface session lifecycle.
/// All I/O methods block the calling thread; run them on a background queue.
@interface PakonUSBDevice : NSObject

// ---- Device detection ----

/// VID 0x0F05, PID 0xF135 = operational ("warm") firmware.
+ (BOOL)isScannerConnected;

/// VID 0x0F05, PID 0xF235 = bootstrap ("cold") firmware.
+ (BOOL)isColdScannerConnected;

// ---- Open ----

/// Open the warm Pakon scanner (full interface + bulk pipes).
+ (nullable instancetype)openWithError:(NSError **)error;

/// Open the cold Pakon scanner for firmware loading (EP0 only, no interface).
+ (nullable instancetype)openColdWithError:(NSError **)error;

// ---- Bulk I/O (warm device only) ----

/// Synchronous bulk write to EP 0x01 (command OUT).
- (BOOL)sendCommand:(NSData *)data
            timeout:(NSTimeInterval)timeout
              error:(NSError **)error;

/// Synchronous bulk read from EP 0x81 (command IN).
- (nullable NSData *)receiveCommandMaxLength:(NSUInteger)maxLength
                                     timeout:(NSTimeInterval)timeout
                                       error:(NSError **)error;

/// Synchronous bulk read from EP 0x86 (image IN).
/// Always returns Data (never nil); returns empty Data on timeout/no-data.
/// Does not throw in Swift — timeout is not an error here.
- (NSData *)receiveImageMaxLength:(NSUInteger)maxLength
                          timeout:(NSTimeInterval)timeout;

// ---- EP0 control transfer ----

/// Synchronous EP0 control transfer via the open interface (warm device).
- (nullable NSData *)controlTransferType:(uint8_t)bmRequestType
                                 request:(uint8_t)bRequest
                                   value:(uint16_t)wValue
                                   index:(uint16_t)wIndex
                                  length:(uint16_t)wLength
                                sendData:(nullable NSData *)sendData
                                 timeout:(NSTimeInterval)timeout
                                   error:(NSError **)error;

/// Synchronous EP0 control transfer directly on the device (cold firmware load).
/// wLength is taken from the script and is authoritative; data may be nil for IN
/// or zero-length transfers.
- (BOOL)sendDeviceControlTransferType:(uint8_t)bmRequestType
                               request:(uint8_t)bRequest
                                 value:(uint16_t)wValue
                                 index:(uint16_t)wIndex
                                length:(uint16_t)wLength
                                  data:(nullable NSData *)data
                               timeout:(NSTimeInterval)timeout
                                 error:(NSError **)error;

// ---- Lifecycle ----

/// Release all IOUSBHost objects.
- (void)close;

@end

NS_ASSUME_NONNULL_END
