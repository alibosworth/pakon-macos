#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Synchronous IOUSBHost transport for the Pakon F-135 scanner.
///
/// Manages the IOUSBHostDevice + IOUSBHostInterface session lifecycle.
/// All I/O methods block the calling thread; run them on a background queue.
@interface PakonUSBDevice : NSObject

/// VID 0x0F05, PID 0xF135 = operational ("warm") firmware.
+ (BOOL)isScannerConnected;

/// Open the warm Pakon scanner. Returns nil + error on failure.
+ (nullable instancetype)openWithError:(NSError **)error;

// ---- Bulk I/O ----

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

/// Synchronous EP0 vendor control transfer.
/// For IN transfers the returned Data holds the received bytes;
/// for OUT transfers the returned Data is empty but non-nil on success.
- (nullable NSData *)controlTransferType:(uint8_t)bmRequestType
                                 request:(uint8_t)bRequest
                                   value:(uint16_t)wValue
                                   index:(uint16_t)wIndex
                                  length:(uint16_t)wLength
                                sendData:(nullable NSData *)sendData
                                 timeout:(NSTimeInterval)timeout
                                   error:(NSError **)error;

// ---- Lifecycle ----

/// Release all IOUSBHost objects (device + interface + pipes).
- (void)close;

@end

NS_ASSUME_NONNULL_END
