// SPDX-License-Identifier: GPL-2.0-only
// Bounded host-only decoder using the already pinned Zig standard library.
const std = @import("std");
pub fn main() !void {
    const allocator = std.heap.page_allocator;
    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);
    if (args.len != 3) return error.InvalidArguments;
    const source = try std.fs.cwd().openFile(args[1], .{});
    defer source.close();
    const output = try std.fs.cwd().createFile(args[2], .{ .exclusive = true });
    defer output.close();
    const window = try allocator.alloc(u8, 128 * 1024 * 1024);
    defer allocator.free(window);
    var buffered = std.io.bufferedReader(source.reader());
    var decoder = std.compress.zstd.decompressor(buffered.reader(), .{ .window_buffer = window });
    var buffer: [65536]u8 = undefined;
    var total: usize = 0;
    while (true) {
        const count = try decoder.reader().read(&buffer);
        if (count == 0) break;
        total += count;
        if (total > 2 * 1024 * 1024 * 1024) return error.OutputTooLarge;
        try output.writeAll(buffer[0..count]);
    }
    try output.sync();
}
