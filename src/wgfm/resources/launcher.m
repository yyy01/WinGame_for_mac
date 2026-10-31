// SPDX-License-Identifier: MIT
#import <Cocoa/Cocoa.h>
#import <ImageIO/ImageIO.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/file.h>
static NSString *J(NSString *a, NSString *b) {
  return [a stringByAppendingPathComponent:b];
}
static NSDictionary *Read(NSString *path) {
  NSData *d = [NSData dataWithContentsOfFile:path];
  if (!d || d.length > 1024 * 1024)
    return nil;
  id v = [NSJSONSerialization JSONObjectWithData:d options:0 error:nil];
  return [v isKindOfClass:NSDictionary.class] ? v : nil;
}
static BOOL Relative(NSString *p) {
  if (![p isKindOfClass:NSString.class] || !p.length || p.isAbsolutePath ||
      [p containsString:@"\\"] || [p containsString:@":"])
    return NO;
  return ![p.pathComponents containsObject:@".."];
}
static NSString *Inside(NSString *root, NSString *rel) {
  if (!Relative(rel))
    return nil;
  NSString *p = [J(root, rel) stringByResolvingSymlinksInPath];
  NSString *r = root.stringByResolvingSymlinksInPath;
  return [p isEqual:r] || [p hasPrefix:[r stringByAppendingString:@"/"]] ? p
                                                                         : nil;
}
static BOOL ValidID(NSString *s) {
  if (![s isKindOfClass:NSString.class])
    return NO;
  NSRegularExpression *re = [NSRegularExpression
      regularExpressionWithPattern:@"^[a-z0-9][a-z0-9_-]{0,63}$"
                           options:0
                             error:nil];
  return [re numberOfMatchesInString:s
                             options:0
                               range:NSMakeRange(0, s.length)] == 1;
}
static BOOL File(NSString *p) {
  return [p isKindOfClass:NSString.class] &&
         [NSFileManager.defaultManager fileExistsAtPath:p];
}

// Downsample chosen artwork before decoding, then let iconutil encode ICNS.
static int MakeIcon(NSString *source, NSString *folder) {
  CGImageSourceRef input = CGImageSourceCreateWithURL(
      (__bridge CFURLRef)[NSURL fileURLWithPath:source], NULL);
  if (!input)
    return 65;
  NSDictionary *options = @{
    (id)kCGImageSourceCreateThumbnailFromImageAlways : @YES,
    (id)kCGImageSourceThumbnailMaxPixelSize : @1024,
    (id)kCGImageSourceCreateThumbnailWithTransform : @YES
  };
  CGImageRef thumb = CGImageSourceCreateThumbnailAtIndex(
      input, 0, (__bridge CFDictionaryRef)options);
  CFRelease(input);
  if (!thumb)
    return 65;
  NSImage *image = [[NSImage alloc] initWithCGImage:thumb size:NSZeroSize];
  CGImageRelease(thumb);
  [NSFileManager.defaultManager createDirectoryAtPath:folder
                          withIntermediateDirectories:YES
                                           attributes:nil
                                                error:nil];
  for (NSNumber *number in @[ @16, @32, @128, @256, @512 ]) {
    int size = number.intValue;
    for (int scale = 1; scale <= 2; scale++) {
      int pixels = size * scale;
      NSBitmapImageRep *rep = [[NSBitmapImageRep alloc]
          initWithBitmapDataPlanes:NULL
                        pixelsWide:pixels
                        pixelsHigh:pixels
                     bitsPerSample:8
                   samplesPerPixel:4
                          hasAlpha:YES
                          isPlanar:NO
                    colorSpaceName:NSCalibratedRGBColorSpace
                       bytesPerRow:0
                      bitsPerPixel:0];
      [NSGraphicsContext saveGraphicsState];
      [NSGraphicsContext
          setCurrentContext:[NSGraphicsContext
                                graphicsContextWithBitmapImageRep:rep]];
      NSGraphicsContext.currentContext.imageInterpolation =
          NSImageInterpolationHigh;
      [image drawInRect:NSMakeRect(0, 0, pixels, pixels)
               fromRect:NSZeroRect
              operation:NSCompositingOperationCopy
               fraction:1];
      [NSGraphicsContext restoreGraphicsState];
      NSString *name =
          [NSString stringWithFormat:@"icon_%dx%d%@.png", size, size,
                                     scale == 2 ? @"@2x" : @""];
      if (![[rep representationUsingType:NSBitmapImageFileTypePNG
                              properties:@{}] writeToFile:J(folder, name)
                                               atomically:YES])
        return 74;
    }
  }
  return 0;
}

@interface Runner : NSObject <NSApplicationDelegate>
@property(strong) NSWindow *window;
@property(strong) NSTextField *label;
@property(strong) NSButton *cancelButton;
@property(strong) NSDictionary *profile;
@property(strong) NSDictionary *runtime;
@property(strong) NSString *home;
@property(strong) NSString *logPath;
@property(strong) NSFileHandle *log;
@property(strong) NSLock *logLock;
@property(atomic) BOOL cancelled;
@property(atomic) BOOL started;
@property(atomic) BOOL finishing;
@property int lockFD;
@end
@implementation Runner
- (void)text:(NSString *)s {
  dispatch_async(dispatch_get_main_queue(), ^{
    self.label.stringValue = s;
  });
}
- (void)finish {
  dispatch_async(dispatch_get_main_queue(), ^{
    self.finishing = YES;
    [NSApp terminate:nil];
  });
}
- (void)error:(NSString *)s {
  dispatch_async(dispatch_get_main_queue(), ^{
    [self.window close];
    NSAlert *a = [NSAlert new];
    a.messageText = @"游戏暂时无法启动";
    a.informativeText = s;
    [a runModal];
    self.finishing = YES;
    [NSApp terminate:nil];
  });
}
- (void)cancel:(id)sender {
  if (self.started)
    return;
  self.cancelled = YES;
  [self.window orderOut:nil];
}
- (NSDictionary *)environment {
  NSMutableDictionary *e = [NSMutableDictionary new];
  NSDictionary *host = NSProcessInfo.processInfo.environment;
  for (NSString *k in @[
         @"HOME", @"USER", @"LOGNAME", @"PATH", @"TMPDIR", @"LANG", @"LC_ALL"
       ])
    if (host[k])
      e[k] = host[k];
  NSString *mode = self.runtime[@"sync"];
  e[@"WINEPREFIX"] = self.profile[@"prefix"];
  e[@"WINELOADER"] = self.runtime[@"wine"];
  e[@"WINESERVER"] = self.runtime[@"wineserver"];
  e[@"WINEDEBUG"] = @"-all";
  e[@"MVK_CONFIG_LOG_LEVEL"] = @"0";
  e[@"WINEMSYNC"] = [mode isEqual:@"msync"] ? @"1" : @"0";
  e[@"WINEESYNC"] = [mode isEqual:@"esync"] ? @"1" : @"0";
  NSMutableArray *libs = [self.runtime[@"library_paths"] mutableCopy];
  [libs addObject:@"/usr/lib"];
  e[@"DYLD_FALLBACK_LIBRARY_PATH"] = [libs componentsJoinedByString:@":"];
  NSDictionary *m = self.profile[@"manifest"];
  NSDictionary *extra = m[@"environment"];
  NSSet *reserved = [NSSet setWithArray:@[
    @"WINEPREFIX", @"WINELOADER", @"WINESERVER", @"WINEMSYNC", @"WINEESYNC",
    @"DYLD_FALLBACK_LIBRARY_PATH", @"DYLD_INSERT_LIBRARIES", @"HOME", @"PATH"
  ]];
  NSRegularExpression *sensitive = [NSRegularExpression
      regularExpressionWithPattern:
          @"token|password|secret|cookie|credential|private.?key|session"
                           options:NSRegularExpressionCaseInsensitive
                             error:nil];
  for (NSString *k in extra) {
    if (![k isKindOfClass:NSString.class] ||
        [reserved containsObject:k.uppercaseString] ||
        [sensitive numberOfMatchesInString:k
                                   options:0
                                     range:NSMakeRange(0, k.length)] ||
        ![extra[k] isKindOfClass:NSString.class])
      return nil;
    e[k] = extra[k];
  }
  if (m[@"steam_app_id"]) {
    e[@"SteamAppId"] = m[@"steam_app_id"];
    e[@"SteamGameId"] = m[@"steam_app_id"];
  }
  return e;
}
- (void)append:(NSData *)data {
  if (!data.length)
    return;
  [self.logLock lock];
  @try {
    if (self.log.offsetInFile + data.length > 2 * 1024 * 1024) {
      [self.log truncateFileAtOffset:0];
      [self.log seekToFileOffset:0];
    }
    [self.log writeData:data];
  } @catch (NSException *e) {
  }
  [self.logLock unlock];
}
- (NSTask *)spawn:(NSArray *)args cwd:(NSString *)cwd env:(NSDictionary *)env {
  NSTask *t = [NSTask new];
  t.executableURL = [NSURL fileURLWithPath:self.runtime[@"wine"]];
  t.arguments = args;
  t.currentDirectoryURL = [NSURL fileURLWithPath:cwd];
  t.environment = env;
  NSPipe *pipe = [NSPipe pipe];
  t.standardOutput = pipe;
  t.standardError = pipe;
  __weak Runner *weak = self;
  pipe.fileHandleForReading.readabilityHandler = ^(NSFileHandle *f) {
    NSData *d = f.availableData;
    if (!d.length) {
      f.readabilityHandler = nil;
      return;
    }
    [weak append:d];
  };
  NSError *err = nil;
  if (![t launchAndReturnError:&err]) {
    pipe.fileHandleForReading.readabilityHandler = nil;
    return nil;
  }
  return t;
}
- (BOOL)ready:(NSDictionary *)env deadline:(NSTimeInterval)deadline {
  NSString *game = self.profile[@"game_path"];
  NSString *exe = Inside(game, self.profile[@"manifest"][@"executable"]);
  NSTask *t = [self spawn:@[ self.profile[@"steam_probe"] ]
                      cwd:exe.stringByDeletingLastPathComponent
                      env:env];
  if (!t)
    return NO;
  NSTimeInterval end =
      MIN(NSProcessInfo.processInfo.systemUptime + 8, deadline);
  while (t.running && NSProcessInfo.processInfo.systemUptime < end &&
         !self.cancelled)
    [NSThread sleepForTimeInterval:.1];
  if (t.running) {
    [t terminate];
    [NSThread sleepForTimeInterval:.2];
    if (t.running)
      kill(t.processIdentifier, SIGKILL);
    [t waitUntilExit];
    return NO;
  }
  return t.terminationStatus == 0;
}
- (void)work {
  @autoreleasepool {
    NSDictionary *m = self.profile[@"manifest"];
    NSString *game = self.profile[@"game_path"];
    NSString *exe = Inside(game, m[@"executable"]);
    NSString *cwd = Inside(game, m[@"working_directory"]);
    if (!File(exe) || !File(cwd) || !File(self.runtime[@"wine"]) ||
        !File(self.runtime[@"wineserver"])) {
      [self error:@"运行文件不完整。请运行 wgfm doctor 检查本机安装。"];
      return;
    }
    NSString *session = self.profile[@"session_id"];
    if (!ValidID(session)) {
      [self error:@"运行配置无效。"];
      return;
    }
    NSString *lockPath = J(J(self.home, @"sessions"),
                           [session stringByAppendingString:@".lock"]);
    [NSFileManager.defaultManager
              createDirectoryAtPath:lockPath.stringByDeletingLastPathComponent
        withIntermediateDirectories:YES
                         attributes:@{NSFilePosixPermissions : @0700}
                              error:nil];
    self.lockFD = open(lockPath.fileSystemRepresentation,
                       O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    if (self.lockFD < 0 || flock(self.lockFD, LOCK_EX | LOCK_NB)) {
      [self
          error:
              @"同一运行环境中的另一款游戏正在运行。先退出它，再启动本游戏。"];
      return;
    }
    NSDictionary *env = [self environment];
    if (!env) {
      [self error:@"环境配置无效。请从不含密码或访问令牌的清单重新导入。"];
      return;
    }
    [NSFileManager.defaultManager
              createDirectoryAtPath:self.profile[@"prefix"]
        withIntermediateDirectories:YES
                         attributes:@{NSFilePosixPermissions : @0700}
                              error:nil];
    self.logLock = [NSLock new];
    self.logPath =
        J(J(self.home, @"logs"), [m[@"id"] stringByAppendingString:@".log"]);
    [NSFileManager.defaultManager
              createDirectoryAtPath:self.logPath
                                        .stringByDeletingLastPathComponent
        withIntermediateDirectories:YES
                         attributes:@{NSFilePosixPermissions : @0700}
                              error:nil];
    int logfd = open(self.logPath.fileSystemRepresentation,
                     O_CREAT | O_WRONLY | O_TRUNC | O_CLOEXEC, 0600);
    if (logfd < 0) {
      [self error:@"无法写入本机启动日志。"];
      return;
    }
    self.log = [[NSFileHandle alloc] initWithFileDescriptor:logfd
                                             closeOnDealloc:YES];
    if (m[@"steam_app_id"]) {
      if (!File(self.profile[@"steam_executable"]) ||
          !File(self.profile[@"steam_probe"])) {
        [self error:@"请先为本游戏配置本机 Steam 与启动检查程序。"];
        return;
      }
      int seconds = [m[@"readiness_timeout_seconds"] intValue];
      if (seconds < 5 || seconds > 300)
        seconds = 120;
      NSTimeInterval deadline =
          NSProcessInfo.processInfo.systemUptime + seconds;
      [self text:@"正在连接 Steam…"];
      if (![self ready:env deadline:deadline]) {
        if (self.cancelled) {
          [self finish];
          return;
        }
        [self spawn:@[
          self.profile[@"steam_executable"], @"-silent", @"-cef-disable-gpu",
          @"-cef-disable-gpu-compositing"
        ]
                cwd:[self.profile[@"steam_executable"]
                        stringByDeletingLastPathComponent]
                env:env];
        BOOL ok = NO;
        while (!self.cancelled &&
               NSProcessInfo.processInfo.systemUptime < deadline) {
          if ([self ready:env deadline:deadline]) {
            ok = YES;
            break;
          }
          [self text:@"正在等待 Steam，若出现登录窗口请完成登录。"];
          // Probe failures back off; the deadline is elapsed time, not 90 ×
          // 8-second probes.
          for (int i = 0; i < 15 && !self.cancelled &&
                          NSProcessInfo.processInfo.systemUptime < deadline;
               i++)
            [NSThread sleepForTimeInterval:.1];
        }
        if (!ok) {
          if (self.cancelled)
            [self finish];
          else
            [self
                error:
                    @"Steam "
                    @"尚未就绪。请确认账号已登录并拥有本游戏，然后重新启动。"];
          return;
        }
      }
    }
    if (self.cancelled) {
      [self finish];
      return;
    }
    NSArray *arguments = m[@"arguments"];
    if (![arguments isKindOfClass:NSArray.class]) {
      [self error:@"游戏参数无效。"];
      return;
    }
    NSMutableArray *args = [NSMutableArray arrayWithObject:exe];
    for (id a in arguments) {
      if (![a isKindOfClass:NSString.class]) {
        [self error:@"游戏参数无效。"];
        return;
      }
      [args addObject:a];
    }
    [self text:@"正在启动游戏…"];
    self.started = YES;
    dispatch_async(dispatch_get_main_queue(), ^{
      self.cancelButton.enabled = NO;
    });
    NSTask *t = [self spawn:args cwd:cwd env:env];
    if (!t) {
      [self error:@"游戏进程无法启动，请查看本机日志或运行 wgfm doctor。"];
      return;
    }
    [NSThread sleepForTimeInterval:.7];
    dispatch_async(dispatch_get_main_queue(), ^{
      [self.window close];
    });
    [t waitUntilExit];
    if (t.terminationStatus != 0)
      [self
          error:@"游戏异常退出。详情保存在本机 WinGameForMac 的 logs 目录中。"];
    else
      [self finish];
  }
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
  NSDictionary *info = NSBundle.mainBundle.infoDictionary;
  NSString *gid = info[@"WGFMGameID"];
  self.home =
      info[@"WGFMHome"]
          ?: J(NSHomeDirectory(), @"Library/Application Support/WinGameForMac");
  if (!ValidID(gid)) {
    [self error:@"启动器缺少有效游戏编号。"];
    return;
  }
  self.profile = Read(
      J(J(self.home, @"profiles"), [gid stringByAppendingString:@".json"]));
  NSString *rid = self.profile[@"runtime_id"];
  if (!ValidID(rid)) {
    [self error:@"尚未导入本游戏。请先完成本机游戏配置。"];
    return;
  }
  self.runtime = Read(
      J(J(self.home, @"runtimes"), [rid stringByAppendingString:@".json"]));
  NSDictionary *m = self.profile[@"manifest"];
  if (!self.runtime || ![m isKindOfClass:NSDictionary.class] ||
      ![m[@"id"] isEqual:gid] || ![m[@"name"] isKindOfClass:NSString.class] ||
      ![m[@"environment"] isKindOfClass:NSDictionary.class] ||
      ![self.runtime[@"library_paths"] isKindOfClass:NSArray.class] ||
      ![@[ @"server", @"msync", @"esync" ]
          containsObject:self.runtime[@"sync"]]) {
    [self error:@"游戏或运行环境配置不完整。"];
    return;
  }
  for (NSString *k in @[ @"game_path", @"prefix", @"session_id" ]) {
    if (![self.profile[k] isKindOfClass:NSString.class]) {
      [self error:@"游戏运行路径无效。"];
      return;
    }
  }
  for (NSString *k in @[ @"wine", @"wineserver" ]) {
    if (![self.runtime[k] isKindOfClass:NSString.class]) {
      [self error:@"Wine 运行路径无效。"];
      return;
    }
  }
  for (id p in self.runtime[@"library_paths"]) {
    if (![p isKindOfClass:NSString.class]) {
      [self error:@"Wine 库目录无效。"];
      return;
    }
  }
  NSDictionary *session =
      Read(J(J(self.home, @"sessions"),
             [self.profile[@"session_id"] stringByAppendingString:@".json"]));
  if (![session[@"runtime_id"] isEqual:rid] ||
      ![session[@"prefix"] isEqual:self.profile[@"prefix"]] ||
      ![session[@"sync"] isEqual:self.runtime[@"sync"]]) {
    [self error:@"共享运行环境配置不一致，请重新注册游戏。"];
    return;
  }
  NSImage *cover = [[NSImage alloc]
      initWithContentsOfFile:J(NSBundle.mainBundle.resourcePath, @"cover.png")];
  if (!cover)
    cover = [[NSImage alloc]
        initWithContentsOfFile:J(NSBundle.mainBundle.resourcePath,
                                 @"cover.jpg")];
  CGFloat height = cover ? 350 : 130;
  self.window =
      [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 480, height)
                                  styleMask:NSWindowStyleMaskTitled
                                    backing:NSBackingStoreBuffered
                                      defer:NO];
  self.window.title = self.profile[@"manifest"][@"name"];
  self.window.releasedWhenClosed = NO;
  if (cover) {
    NSImageView *v =
        [[NSImageView alloc] initWithFrame:NSMakeRect(0, 112, 480, 238)];
    v.image = cover;
    v.imageScaling = NSImageScaleProportionallyUpOrDown;
    [self.window.contentView addSubview:v];
  }
  self.label = [NSTextField labelWithString:@"正在启动…"];
  self.label.frame = NSMakeRect(24, 72, 432, 24);
  [self.window.contentView addSubview:self.label];
  NSProgressIndicator *progress =
      [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(24, 52, 432, 14)];
  progress.indeterminate = YES;
  progress.style = NSProgressIndicatorStyleBar;
  [progress startAnimation:nil];
  [self.window.contentView addSubview:progress];
  self.cancelButton = [NSButton buttonWithTitle:@"取消"
                                         target:self
                                         action:@selector(cancel:)];
  self.cancelButton.frame = NSMakeRect(380, 12, 76, 30);
  [self.window.contentView addSubview:self.cancelButton];
  [self.window center];
  [self.window makeKeyAndOrderFront:nil];
  [NSApp activateIgnoringOtherApps:YES];
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
    [self work];
  });
}
- (NSApplicationTerminateReply)applicationShouldTerminate:
    (NSApplication *)sender {
  if (self.finishing)
    return NSTerminateNow;
  if (!self.started)
    [self cancel:nil];
  return NSTerminateCancel;
}
@end
// GoldSrc VGUI reads small TGA tiles referenced by its background layouts.
// ImageIO decodes; legacy 24-bit TGA output is uncompressed and bounded.
static int MakeBackground(NSString *source, NSString *folder, int width,
                          int height) {
  if (width < 256 || height < 256 || width > 4096 || height > 4096 ||
      width % 256 || height % 256)
    return 64;
  CGImageSourceRef input = CGImageSourceCreateWithURL(
      (__bridge CFURLRef)[NSURL fileURLWithPath:source], NULL);
  if (!input)
    return 65;
  NSDictionary *options = @{
    (id)kCGImageSourceCreateThumbnailFromImageAlways : @YES,
    (id)kCGImageSourceThumbnailMaxPixelSize : @4096,
    (id)kCGImageSourceCreateThumbnailWithTransform : @YES
  };
  CGImageRef image = CGImageSourceCreateThumbnailAtIndex(
      input, 0, (__bridge CFDictionaryRef)options);
  CFRelease(input);
  if (!image)
    return 65;
  CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
  CGContextRef ctx = CGBitmapContextCreate(
      NULL, width, height, 8, width * 4, space, kCGImageAlphaPremultipliedLast);
  CGColorSpaceRelease(space);
  if (!ctx) {
    CGImageRelease(image);
    return 70;
  }
  CGContextSetRGBFillColor(ctx, 0, 0, 0, 1);
  CGContextFillRect(ctx, CGRectMake(0, 0, width, height));
  double scale = MAX((double)width / CGImageGetWidth(image),
                     (double)height / CGImageGetHeight(image));
  double w = CGImageGetWidth(image) * scale,
         h = CGImageGetHeight(image) * scale;
  CGContextSetInterpolationQuality(ctx, kCGInterpolationHigh);
  CGContextDrawImage(ctx, CGRectMake((width - w) / 2, (height - h) / 2, w, h),
                     image);
  CGImageRelease(image);
  CGImageRef canvas = CGBitmapContextCreateImage(ctx);
  CGContextRelease(ctx);
  if (!canvas)
    return 70;
  [NSFileManager.defaultManager createDirectoryAtPath:folder
                          withIntermediateDirectories:YES
                                           attributes:nil
                                                error:nil];
  BOOL ok = YES;
  for (int y = 0; y < height / 256 && ok; y++)
    for (int x = 0; x < width / 256 && ok; x++) {
      CGImageRef tile = CGImageCreateWithImageInRect(
          canvas, CGRectMake(x * 256, y * 256, 256, 256));
      NSString *name = [NSString stringWithFormat:@"tile_%d_%d.tga", x, y];
      CGColorSpaceRef rgb = CGColorSpaceCreateDeviceRGB();
      CGContextRef pixels = CGBitmapContextCreate(
          NULL, 256, 256, 8, 256 * 4, rgb,
          kCGImageAlphaNoneSkipLast | kCGBitmapByteOrder32Big);
      CGColorSpaceRelease(rgb);
      if (!tile || !pixels) {
        if (tile)
          CGImageRelease(tile);
        if (pixels)
          CGContextRelease(pixels);
        ok = NO;
        break;
      }
      CGContextDrawImage(pixels, CGRectMake(0, 0, 256, 256), tile);
      const uint8_t *rgba = CGBitmapContextGetData(pixels);
      NSMutableData *tga = [NSMutableData dataWithLength:18 + 256 * 256 * 3];
      uint8_t *bytes = tga.mutableBytes;
      bytes[2] = 2;
      bytes[13] = 1;
      bytes[15] = 1;
      bytes[16] = 24;
      bytes[17] =
          0; // GoldSrc expects bottom-up TGA rows, including older loaders.
      for (int i = 0; i < 256 * 256; i++) {
        int source = ((255 - i / 256) * 256 + i % 256) * 4;
        bytes[18 + i * 3] = rgba[source + 2];
        bytes[19 + i * 3] = rgba[source + 1];
        bytes[20 + i * 3] = rgba[source];
      }
      ok = [tga writeToFile:J(folder, name) atomically:YES];
      CGContextRelease(pixels);
      CGImageRelease(tile);
    }
  CGImageRelease(canvas);
  return ok ? 0 : 74;
}

int main(int argc, const char **argv) {
  @autoreleasepool {
    if (argc == 4 && !strcmp(argv[1], "--make-icon"))
      return MakeIcon(@(argv[2]), @(argv[3]));
    if (argc == 6 && !strcmp(argv[1], "--make-goldsrc-background"))
      return MakeBackground(@(argv[2]), @(argv[3]), atoi(argv[4]),
                            atoi(argv[5]));
    NSApplication *app = NSApplication.sharedApplication;
    Runner *r = [Runner new];
    app.delegate = r;
    [app run];
    return 0;
  }
}
