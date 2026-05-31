#import "PakonUSBTransport.h"

#import <IOUSBHost/IOUSBHost.h>
#import <IOKit/IOKitLib.h>

#define PAKON_VID      0x0F05
#define PAKON_WARM_PID 0xF135
#define PAKON_COLD_PID 0xF235

#define EP_CMD_OUT  0x01
#define EP_CMD_IN   0x81
#define EP_IMAGE_IN 0x86

#define CFG_VALUE  1  /* single configuration on the f135 */
#define IFC_NUMBER 0  /* single interface */

static NSString *const kPakonErrorDomain = @"PakonUSBError";

static NSError *pakonErr(NSInteger code, NSString *msg) {
    return [NSError errorWithDomain:kPakonErrorDomain
                               code:code
                           userInfo:@{NSLocalizedDescriptionKey: msg}];
}

static io_service_t findDevice(uint16_t pid) {
    CFMutableDictionaryRef dict =
        [IOUSBHostDevice createMatchingDictionaryWithVendorID:@(PAKON_VID)
                                                    productID:@(pid)
                                                    bcdDevice:nil
                                                  deviceClass:nil
                                               deviceSubclass:nil
                                               deviceProtocol:nil
                                                        speed:nil
                                               productIDArray:nil];
    return IOServiceGetMatchingService(kIOMainPortDefault, dict);
}

@implementation PakonUSBDevice {
    IOUSBHostDevice    *_device;
    IOUSBHostInterface *_interface;
    IOUSBHostPipe      *_cmdOut;
    IOUSBHostPipe      *_cmdIn;
    IOUSBHostPipe      *_imageIn;
}

// ---- Device detection ----

+ (BOOL)isScannerConnected {
    io_service_t svc = findDevice(PAKON_WARM_PID);
    if (!svc) return NO;
    IOObjectRelease(svc);
    return YES;
}

+ (BOOL)isColdScannerConnected {
    io_service_t svc = findDevice(PAKON_COLD_PID);
    if (!svc) return NO;
    IOObjectRelease(svc);
    return YES;
}

// ---- Open (warm) ----

+ (nullable instancetype)openWithError:(NSError **)outError {
    NSError *err = nil;

    io_service_t devSvc = findDevice(PAKON_WARM_PID);
    if (!devSvc) {
        if (outError) *outError = pakonErr(1, @"Scanner not found — is it connected and powered on?");
        return nil;
    }

    IOUSBHostDevice *device =
        [[IOUSBHostDevice alloc] initWithIOService:devSvc
                                           options:IOUSBHostObjectInitOptionsNone
                                             queue:nil
                                             error:&err
                                   interestHandler:nil];
    IOObjectRelease(devSvc);
    if (!device) {
        if (outError) *outError = err ?: pakonErr(2, @"Could not open scanner device");
        return nil;
    }

    if (![device configureWithValue:CFG_VALUE matchInterfaces:YES error:&err]) {
        [device destroy];
        if (outError) *outError = err ?: pakonErr(3, @"Could not configure scanner device");
        return nil;
    }

    io_service_t ifaceSvc = 0;
    for (int attempt = 0; attempt < 20 && !ifaceSvc; attempt++) {
        CFMutableDictionaryRef ifaceDict =
            [IOUSBHostInterface createMatchingDictionaryWithVendorID:@(PAKON_VID)
                                                          productID:@(PAKON_WARM_PID)
                                                          bcdDevice:nil
                                                    interfaceNumber:@(IFC_NUMBER)
                                                 configurationValue:@(CFG_VALUE)
                                                     interfaceClass:nil
                                                  interfaceSubclass:nil
                                                  interfaceProtocol:nil
                                                              speed:nil
                                                     productIDArray:nil];
        ifaceSvc = IOServiceGetMatchingService(kIOMainPortDefault, ifaceDict);
        if (!ifaceSvc) usleep(50000);
    }
    if (!ifaceSvc) {
        [device destroy];
        if (outError) *outError = pakonErr(4, @"Scanner interface not found after configure");
        return nil;
    }

    IOUSBHostInterface *iface =
        [[IOUSBHostInterface alloc] initWithIOService:ifaceSvc
                                              options:IOUSBHostObjectInitOptionsNone
                                                queue:nil
                                                error:&err
                                      interestHandler:nil];
    IOObjectRelease(ifaceSvc);
    if (!iface) {
        [device destroy];
        if (outError) *outError = err ?: pakonErr(5, @"Could not open scanner interface");
        return nil;
    }

    IOUSBHostPipe *cmdOut  = [iface copyPipeWithAddress:EP_CMD_OUT  error:&err];
    if (!cmdOut)  { [iface destroy]; [device destroy]; if (outError) *outError = err; return nil; }

    IOUSBHostPipe *cmdIn   = [iface copyPipeWithAddress:EP_CMD_IN   error:&err];
    if (!cmdIn)   { [iface destroy]; [device destroy]; if (outError) *outError = err; return nil; }

    IOUSBHostPipe *imageIn = [iface copyPipeWithAddress:EP_IMAGE_IN error:&err];
    if (!imageIn) { [iface destroy]; [device destroy]; if (outError) *outError = err; return nil; }

    PakonUSBDevice *obj = [[PakonUSBDevice alloc] init];
    obj->_device    = device;
    obj->_interface = iface;
    obj->_cmdOut    = cmdOut;
    obj->_cmdIn     = cmdIn;
    obj->_imageIn   = imageIn;
    return obj;
}

// ---- Open (cold, EP0 only) ----

+ (nullable instancetype)openColdWithError:(NSError **)outError {
    NSError *err = nil;

    io_service_t devSvc = findDevice(PAKON_COLD_PID);
    if (!devSvc) {
        if (outError) *outError = pakonErr(1, @"Cold scanner not found");
        return nil;
    }

    IOUSBHostDevice *device =
        [[IOUSBHostDevice alloc] initWithIOService:devSvc
                                           options:IOUSBHostObjectInitOptionsNone
                                             queue:nil
                                             error:&err
                                   interestHandler:nil];
    IOObjectRelease(devSvc);
    if (!device) {
        if (outError) *outError = err ?: pakonErr(2, @"Could not open cold scanner device");
        return nil;
    }

    PakonUSBDevice *obj = [[PakonUSBDevice alloc] init];
    obj->_device = device;
    return obj;
}

// ---- Bulk I/O ----

- (BOOL)sendCommand:(NSData *)data timeout:(NSTimeInterval)timeout error:(NSError **)error {
    NSMutableData *buf = [NSMutableData dataWithData:data];
    NSUInteger transferred = 0;
    return [_cmdOut sendIORequestWithData:buf
                         bytesTransferred:&transferred
                        completionTimeout:timeout
                                    error:error];
}

- (nullable NSData *)receiveCommandMaxLength:(NSUInteger)maxLength
                                     timeout:(NSTimeInterval)timeout
                                       error:(NSError **)error {
    NSMutableData *buf = [NSMutableData dataWithLength:maxLength];
    NSUInteger received = 0;
    BOOL ok = [_cmdIn sendIORequestWithData:buf
                           bytesTransferred:&received
                          completionTimeout:timeout
                                      error:error];
    if (!ok) return nil;
    return [buf subdataWithRange:NSMakeRange(0, received)];
}

- (NSData *)receiveImageMaxLength:(NSUInteger)maxLength timeout:(NSTimeInterval)timeout {
    NSMutableData *buf = [NSMutableData dataWithLength:maxLength];
    NSUInteger received = 0;
    NSError *err = nil;
    BOOL ok = [_imageIn sendIORequestWithData:buf
                             bytesTransferred:&received
                            completionTimeout:timeout
                                        error:&err];
    if (!ok) {
        [_imageIn clearStallWithError:nil];
        return [NSData data];
    }
    return [buf subdataWithRange:NSMakeRange(0, received)];
}

// ---- EP0 control transfer (warm, via interface) ----

- (nullable NSData *)controlTransferType:(uint8_t)bmRequestType
                                 request:(uint8_t)bRequest
                                   value:(uint16_t)wValue
                                   index:(uint16_t)wIndex
                                  length:(uint16_t)wLength
                                sendData:(nullable NSData *)sendData
                                 timeout:(NSTimeInterval)timeout
                                   error:(NSError **)error {
    IOUSBDeviceRequest req = {
        .bmRequestType = bmRequestType,
        .bRequest      = bRequest,
        .wValue        = wValue,
        .wIndex        = wIndex,
        .wLength       = wLength,
    };
    NSMutableData *buf = sendData ? [NSMutableData dataWithData:sendData]
                                  : [NSMutableData dataWithLength:wLength];
    NSUInteger transferred = 0;
    BOOL ok = [_interface sendDeviceRequest:req
                                       data:buf
                           bytesTransferred:&transferred
                          completionTimeout:timeout
                                      error:error];
    if (!ok) return nil;
    return [buf subdataWithRange:NSMakeRange(0, transferred)];
}

// ---- EP0 control transfer (cold, directly on device) ----

- (BOOL)sendDeviceControlTransferType:(uint8_t)bmRequestType
                               request:(uint8_t)bRequest
                                 value:(uint16_t)wValue
                                 index:(uint16_t)wIndex
                                length:(uint16_t)wLength
                                  data:(nullable NSData *)data
                               timeout:(NSTimeInterval)timeout
                                 error:(NSError **)error {
    IOUSBDeviceRequest req = {
        .bmRequestType = bmRequestType,
        .bRequest      = bRequest,
        .wValue        = wValue,
        .wIndex        = wIndex,
        .wLength       = wLength,
    };
    NSMutableData *buf = data ? [NSMutableData dataWithData:data]
                              : [NSMutableData dataWithLength:wLength];
    NSUInteger transferred = 0;
    return [_device sendDeviceRequest:req
                                 data:buf
                     bytesTransferred:&transferred
                    completionTimeout:timeout
                                error:error];
}

// ---- Lifecycle ----

- (void)close {
    _cmdOut   = nil;
    _cmdIn    = nil;
    _imageIn  = nil;
    [_interface destroy]; _interface = nil;
    [_device   destroy]; _device    = nil;
}

- (void)dealloc {
    [self close];
}

@end
