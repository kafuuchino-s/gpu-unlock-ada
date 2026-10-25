/* SPDX-License-Identifier: GPL-2.0-only */
/* Exercise the actual context collector with read-only internal file handles. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#undef NULL
#include "uefi/context.c"

static EFI_BOOT_SERVICES services;
static EFI_SYSTEM_TABLE system_table;
static EFI_LOADED_IMAGE_PROTOCOL loaded;
static EFI_SIMPLE_FILE_SYSTEM_PROTOCOL filesystem;
static EFI_PARTITION_INFO_PROTOCOL partition_info;
static EFI_FILE_PROTOCOL internal_root, boot_file;
static UINT8 usb_path[] = {3, 5, 6, 0, 1, 0, 0x7f, 0xff, 4, 0};
static UINT8 gpu_path[] = {1, 1, 6, 0, 0, 1, 0x7f, 0xff, 4, 0};
static UINT8 disk_path[52], boot_data[512];
static char captured_json[65536];
static UINTN active, read_offset, opened, closed, saved_boot, saved_context;
static BOOLEAN no_boot, short_read, oversize, malformed, storage_error, json_error, is_usb_disk;
static BOOLEAN pathless_fs, volume_error, root_close_error, boot_close_error, open_error;

static EFI_HANDLE handle(UINTN value) { return (EFI_HANDLE)value; }
static VOID *allocate(UINTN bytes)
{
	VOID *result = malloc(bytes);
	assert(result);
	active++;
	return result;
}
static EFI_STATUS EFIAPI allocate_pool(EFI_MEMORY_TYPE type, UINTN bytes, VOID **out)
{
	assert(type == EfiLoaderData);
	*out = allocate(bytes);
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI free_pool(VOID *pointer)
{
	assert(pointer && active);
	active--;
	free(pointer);
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI locate(EFI_LOCATE_SEARCH_TYPE search, EFI_GUID *guid,
	VOID *key, UINTN *count, EFI_HANDLE **out)
{
	(void)key;
	if (search == ByProtocol) {
		assert(BytesEqual(guid, &context_fs_guid, sizeof(*guid)));
		*count = pathless_fs ? 3 : 2;
	} else {
		assert(search == AllHandles && !guid);
		*count = 3;
	}
	*out = allocate(*count * sizeof(**out));
	for (UINTN i = 0; i < *count; i++)
		(*out)[i] = handle(i + 1);
	if (search == ByProtocol && pathless_fs)
		(*out)[2] = handle(5);
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI protocol(EFI_HANDLE source, EFI_GUID *guid, VOID **out)
{
	*out = NULL;
	if (source == handle(5))
		return EFI_UNSUPPORTED;
	if (BytesEqual(guid, &context_path_guid, sizeof(*guid)))
		*out = source == handle(1) ? usb_path : source == handle(2) ?
			(is_usb_disk ? usb_path : disk_path) : gpu_path;
	else if (BytesEqual(guid, &context_loaded_guid, sizeof(*guid)) && source == handle(4))
		*out = &loaded;
	else if (BytesEqual(guid, &context_fs_guid, sizeof(*guid)) && source == handle(2))
		*out = &filesystem;
	else if (BytesEqual(guid, &context_partition_guid, sizeof(*guid)) && source == handle(2))
		*out = &partition_info;
	return *out ? EFI_SUCCESS : EFI_UNSUPPORTED;
}
static EFI_STATUS EFIAPI protocols(EFI_HANDLE source, EFI_GUID ***out, UINTN *count)
{
	(void)source;
	*count = 1;
	*out = allocate(sizeof(**out));
	(*out)[0] = &context_path_guid;
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI opens(EFI_HANDLE source, EFI_GUID *guid,
	EFI_OPEN_PROTOCOL_INFORMATION_ENTRY **out, UINTN *count)
{
	(void)source; (void)guid;
	*out = NULL; *count = 0;
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI open_volume(EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *self, EFI_FILE_PROTOCOL **out)
{
	assert(self == &filesystem);
	if (volume_error)
		return EFI_DEVICE_ERROR;
	*out = &internal_root;
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI open_file(EFI_FILE_PROTOCOL *self, EFI_FILE_PROTOCOL **out,
	CHAR16 *name, UINT64 mode, UINT64 attributes)
{
	const CHAR16 expected[] = L"EFI\\Microsoft\\Boot\\bootmgfw.efi";
	assert(self == &internal_root && mode == EFI_FILE_MODE_READ && !attributes);
	assert(!memcmp(name, expected, sizeof(expected)));
	if (no_boot)
		return EFI_NOT_FOUND;
	if (open_error)
		return EFI_ACCESS_DENIED;
	*out = &boot_file;
	read_offset = 0; opened++;
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI close_file(EFI_FILE_PROTOCOL *self)
{
	assert(self == &internal_root || self == &boot_file);
	if (self == &boot_file)
		closed++;
	return (self == &internal_root && root_close_error) ||
		(self == &boot_file && boot_close_error) ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}
static EFI_STATUS EFIAPI file_info(EFI_FILE_PROTOCOL *self, EFI_GUID *guid, UINTN *bytes, VOID *out)
{
	EFI_FILE_INFO *info = out;
	assert(self == &boot_file && BytesEqual(guid, &context_info_guid, sizeof(*guid)));
	assert(*bytes >= sizeof(*info));
	memset(out, 0, sizeof(*info));
	info->Size = *bytes = sizeof(*info);
	info->FileSize = oversize ? BOOT_FILE_MAX + 1 : sizeof(boot_data);
	return EFI_SUCCESS;
}
static EFI_STATUS EFIAPI read_file(EFI_FILE_PROTOCOL *self, UINTN *bytes, VOID *out)
{
	assert(self == &boot_file);
	if (read_offset) {
		*bytes = 0;
		return EFI_SUCCESS;
	}
	assert(*bytes == sizeof(boot_data));
	if (short_read)
		(*bytes)--;
	memcpy(out, boot_data, *bytes);
	read_offset += *bytes;
	return EFI_SUCCESS;
}
static EFI_STATUS save_usb(CHAR16 *name, const VOID *data, UINTN bytes)
{
	if (name[0] == 'B') {
		assert(name[8] == '0' && name[9] == '0' && bytes == sizeof(boot_data));
		assert(!memcmp(data, boot_data, bytes));
		saved_boot++;
		return storage_error ? EFI_DEVICE_ERROR : EFI_SUCCESS;
	}
	assert(name[0] == 'C' && bytes < sizeof(captured_json));
	memcpy(captured_json, data, bytes);
	captured_json[bytes] = 0;
	saved_context++;
	return json_error ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}
static VOID put32(UINT8 *data, UINTN offset, UINT32 value)
{
	for (UINTN i = 0; i < 4; i++)
		data[offset + i] = (UINT8)(value >> (8 * i));
}
static VOID reset(VOID)
{
	assert(!active && opened == closed);
	memset(&services, 0, sizeof(services));
	memset(&system_table, 0, sizeof(system_table));
	memset(&filesystem, 0, sizeof(filesystem));
	memset(&partition_info, 0, sizeof(partition_info));
	memset(&internal_root, 0, sizeof(internal_root));
	memset(&boot_file, 0, sizeof(boot_file));
	memset(disk_path, 0, sizeof(disk_path));
	memcpy(disk_path, (UINT8[]){1, 1, 6, 0, 0, 0, 4, 1, 42, 0}, 10);
	put32(disk_path, 10, 2); put32(disk_path, 14, 2048); put32(disk_path, 22, 204800);
	disk_path[46] = disk_path[47] = 2;
	memcpy(disk_path + 48, (UINT8[]){0x7f, 0xff, 4, 0}, 4);
	memset(boot_data, 0, sizeof(boot_data));
	boot_data[0] = 'M'; boot_data[1] = 'Z'; put32(boot_data, 60, 64);
	put32(boot_data, 64, 0x4550); put32(boot_data, 68, 0x8664);
	boot_data[84] = 240; boot_data[88] = 0x0b; boot_data[89] = 2; boot_data[156] = 10;
	services.AllocatePool = allocate_pool; services.FreePool = free_pool;
	services.HandleProtocol = protocol; services.LocateHandleBuffer = locate;
	services.ProtocolsPerHandle = protocols; services.OpenProtocolInformation = opens;
	system_table.BootServices = &services;
	loaded.DeviceHandle = handle(1);
	filesystem.OpenVolume = open_volume;
	partition_info.Revision = EFI_PARTITION_INFO_PROTOCOL_REVISION;
	partition_info.Type = PARTITION_TYPE_GPT;
	partition_info.Info.Gpt.PartitionTypeGUID = context_esp_guid;
	internal_root.Open = open_file; internal_root.Close = close_file;
	boot_file.Close = close_file; boot_file.GetInfo = file_info; boot_file.Read = read_file;
	opened = closed = saved_boot = saved_context = 0;
	captured_json[0] = 0;
	no_boot = short_read = oversize = malformed = storage_error = json_error = is_usb_disk = FALSE;
	pathless_fs = volume_error = root_close_error = boot_close_error = open_error = FALSE;
}
static EFI_STATUS capture(VOID)
{
	return probe_capture_context(&system_table, handle(4), handle(3), save_usb);
}
int main(int argc, char **argv)
{
	reset(); assert(capture() == EFI_SUCCESS && saved_boot == 1 && saved_context == 1);
	assert(strstr(captured_json, "\"saved_count\":1,\"complete\":true"));
	assert(strstr(captured_json, "\"release_authorized\":false"));
	assert(strstr(captured_json, "\"start_lba\":2048,\"sectors\":204800"));
	assert(strstr(captured_json, "\"esp_type_confirmed\":true"));
	if (argc == 2) {
		FILE *output = fopen(argv[1], "wb");
		assert(output);
		assert(fwrite(captured_json, 1, strlen(captured_json), output) == strlen(captured_json));
		assert(!fclose(output));
	}
	reset(); no_boot = TRUE; assert(capture() == EFI_SUCCESS && !saved_boot);
	assert(strstr(captured_json, "\"saved_count\":0"));
	reset(); is_usb_disk = TRUE; assert(capture() == EFI_SUCCESS && !opened);
	reset(); short_read = TRUE; assert(capture() == EFI_SUCCESS && !saved_boot);
	assert(strstr(captured_json, "\"complete\":false"));
	reset(); oversize = TRUE; assert(capture() == EFI_SUCCESS && !saved_boot);
	reset(); boot_data[0] = 0; assert(capture() == EFI_SUCCESS && !saved_boot);
	reset(); storage_error = TRUE; assert(capture() == EFI_SUCCESS && saved_boot == 1);
	assert(strstr(captured_json, "\"complete\":false"));
	reset(); json_error = TRUE; assert(capture() == EFI_DEVICE_ERROR);
	reset(); pathless_fs = TRUE; assert(capture() == EFI_SUCCESS && saved_boot == 1);
	assert(strstr(captured_json, "\"filesystem_index\":2,\"stage\":\"device_path\""));
	assert(strstr(captured_json, "\"complete\":false"));
	reset(); volume_error = TRUE; assert(capture() == EFI_SUCCESS && !opened);
	assert(strstr(captured_json, "\"stage\":\"open_volume\""));
	reset(); root_close_error = TRUE; assert(capture() == EFI_SUCCESS && saved_boot == 1);
	assert(strstr(captured_json, "\"stage\":\"close_volume\""));
	assert(strstr(captured_json, "\"complete\":false"));
	reset(); boot_close_error = TRUE; assert(capture() == EFI_SUCCESS && saved_boot == 1);
	assert(strstr(captured_json, "\"stage\":\"close_file\""));
	reset(); open_error = TRUE; assert(capture() == EFI_SUCCESS && !saved_boot);
	assert(strstr(captured_json, "\"stage\":\"boot_file\""));
	assert(!active && opened == closed);
	puts("13 context host cases passed; internal files READ only; allocations/handles released.");
	return 0;
}
