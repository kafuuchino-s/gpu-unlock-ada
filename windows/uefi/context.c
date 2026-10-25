/* SPDX-License-Identifier: GPL-2.0-only */
/* Observe UEFI ownership and read Windows boot files. All output uses the
 * caller's new USB run directory. Internal file systems are opened READ only.
 * No boot variable writes, image execution, driver callbacks or PCI writes.
 */
#include "context.h"
#include "vendor/ad104/src/platform.h"
#include <Protocol/DevicePath.h>
#include <Protocol/LoadedImage.h>
#include <Protocol/PartitionInfo.h>
#include <Protocol/SimpleFileSystem.h>
#include <Guid/FileInfo.h>

#ifndef PROBE_BUILD_ID
#error Build with an ID covering the entire probe source set
#endif
#define CONTEXT_BYTES (8U * 1024U * 1024U)
#define BOOT_FILE_MAX (8U * 1024U * 1024U)
#define MAX_BOOT_FILES 16U

static EFI_GUID context_path_guid = EFI_DEVICE_PATH_PROTOCOL_GUID;
static EFI_GUID context_loaded_guid = EFI_LOADED_IMAGE_PROTOCOL_GUID;
static EFI_GUID context_fs_guid = EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID;
static EFI_GUID context_info_guid = EFI_FILE_INFO_ID;
static EFI_GUID context_partition_guid = EFI_PARTITION_INFO_PROTOCOL_GUID;
static EFI_GUID context_esp_guid = {
	0xc12a7328, 0xf81f, 0x11d2, {0xba, 0x4b, 0x00, 0xa0, 0xc9, 0x3e, 0xc9, 0x3b}
};
static EFI_GUID context_global_guid = {
	0x8be4df61, 0x93ca, 0x11d2, {0xaa, 0x0d, 0x00, 0xe0, 0x98, 0x03, 0x2b, 0x8c}
};

static UINT8 *context_path_status(EFI_BOOT_SERVICES *services, EFI_HANDLE handle,
	UINTN *length, EFI_STATUS *observed_status)
{
	VOID *path = NULL;
	BOOLEAN multi = FALSE;
	EFI_STATUS status = EFI_NOT_FOUND;

	*length = 0;
	if (handle)
		status = services->HandleProtocol(handle, &context_path_guid, &path);
	if (!EFI_ERROR(status) && !path)
		status = EFI_NOT_FOUND;
	if (!EFI_ERROR(status) && (!DpMeasure(path, PROBE_DP_MAX, length, &multi) || multi))
		status = EFI_COMPROMISED_DATA;
	if (EFI_ERROR(status)) {
		path = NULL;
		*length = 0;
	}
	if (observed_status)
		*observed_status = status;
	return path;
}

static UINT8 *context_path(EFI_BOOT_SERVICES *services, EFI_HANDLE handle, UINTN *length)
{
	return context_path_status(services, handle, length, NULL);
}

static VOID context_path_json(TEXT *text, const UINT8 *path, UINTN length)
{
	if (path)
		TextBytes(text, path, length);
	else
		TextPut(text, "null");
}

static BOOLEAN context_partition(TEXT *text, const UINT8 *path, UINTN length)
{
	UINTN offset = 0;

	while (offset + 4 < length) {
		UINTN bytes = Read16(path + offset + 2);
		const UINT8 *node = path + offset;

		if (node[0] == MEDIA_DEVICE_PATH && node[1] == MEDIA_HARDDRIVE_DP && bytes == 42) {
			TextPut(text, "{\"number\":");
			TextDec(text, Read32(node + 4));
			TextPut(text, ",\"start_lba\":");
			TextDec(text, Read64(node + 8));
			TextPut(text, ",\"sectors\":");
			TextDec(text, Read64(node + 16));
			TextPut(text, ",\"signature\":");
			TextBytes(text, node + 24, 16);
			TextPut(text, ",\"mbr_type\":");
			TextDec(text, node[40]);
			TextPut(text, ",\"signature_type\":");
			TextDec(text, node[41]);
			TextPut(text, "}");
			return TRUE;
		}
		offset += bytes;
	}
	TextPut(text, "null");
	return FALSE;
}

static BOOLEAN context_boot_pe(const UINT8 *data, UINTN bytes)
{
	UINTN pe, optional;

	if (bytes < 64 || Read16(data) != 0x5a4d)
		return FALSE;
	pe = Read32(data + 60);
	if (pe > bytes || bytes - pe < 24 || Read32(data + pe) != 0x4550 ||
	    Read16(data + pe + 4) != 0x8664)
		return FALSE;
	optional = pe + 24;
	return Read16(data + pe + 20) >= 72 && bytes - optional >= 72 &&
		Read16(data + optional) == 0x20b && Read16(data + optional + 68) == 10;
}

static EFI_STATUS context_boot_file(EFI_BOOT_SERVICES *services,
	EFI_FILE_PROTOCOL *file, UINTN index, TEXT *text, PROBE_SAVE save)
{
	union {
		EFI_FILE_INFO info;
		UINT8 bytes[1024];
	} metadata;
	UINTN info_bytes = sizeof(metadata), bytes = 0, read_bytes;
	UINT8 *data = NULL, hash[32], extra;
	CHAR16 filename[] = L"BOOTMGFW00.BIN";
	char ascii_name[] = "BOOTMGFW00.BIN";
	EFI_STATUS status;

	if (!file->GetInfo || !file->Read)
		return EFI_UNSUPPORTED;
	status = file->GetInfo(file, &context_info_guid, &info_bytes, &metadata);
	if (EFI_ERROR(status))
		return status;
	if (info_bytes < SIZE_OF_EFI_FILE_INFO || info_bytes > sizeof(metadata) ||
	    metadata.info.Size < SIZE_OF_EFI_FILE_INFO || metadata.info.Size > info_bytes ||
	    !metadata.info.FileSize || metadata.info.FileSize > BOOT_FILE_MAX ||
	    metadata.info.Attribute & EFI_FILE_DIRECTORY)
		return EFI_BAD_BUFFER_SIZE;
	bytes = (UINTN)metadata.info.FileSize;
	status = services->AllocatePool(EfiLoaderData, bytes, (VOID **)&data);
	if (EFI_ERROR(status) || !data)
		return EFI_OUT_OF_RESOURCES;
	read_bytes = bytes;
	status = file->Read(file, &read_bytes, data);
	if (!EFI_ERROR(status) && read_bytes != bytes)
		status = EFI_BAD_BUFFER_SIZE;
	if (!EFI_ERROR(status)) {
		read_bytes = 1;
		status = file->Read(file, &read_bytes, &extra);
		if (!EFI_ERROR(status) && read_bytes)
			status = EFI_BAD_BUFFER_SIZE;
	}
	if (!EFI_ERROR(status) && !context_boot_pe(data, bytes))
		status = EFI_COMPROMISED_DATA;
	if (!EFI_ERROR(status)) {
		filename[8] = ascii_name[8] = (char)('0' + index / 10);
		filename[9] = ascii_name[9] = (char)('0' + index % 10);
		status = save(filename, data, bytes);
		if (!EFI_ERROR(status)) {
			Sha256(data, bytes, hash);
			TextPut(text, ",\"file\":\"");
			TextPut(text, ascii_name);
			TextPut(text, "\",\"bytes\":");
			TextDec(text, bytes);
			TextPut(text, ",\"sha256\":");
			TextBytes(text, hash, sizeof(hash));
		}
	}
	services->FreePool(data);
	return status;
}

struct context_fs_failure {
	const char *stage;
	EFI_STATUS status;
	const UINT8 *path;
	UINTN path_bytes;
};
static struct context_fs_failure context_fs_failures[128];

static VOID context_fs_failure(UINTN index, const char *stage, EFI_STATUS status,
	const UINT8 *path, UINTN path_bytes)
{
	if (index >= 128 || context_fs_failures[index].stage)
		return;
	context_fs_failures[index] = (struct context_fs_failure){stage, status, path, path_bytes};
}

static VOID context_windows(TEXT *text, EFI_BOOT_SERVICES *services, PROBE_SAVE save)
{
	EFI_HANDLE *handles = NULL;
	UINTN count = 0, i, candidates = 0, saved = 0, failures = 0;
	EFI_STATUS status;
	BOOLEAN complete = TRUE;

	for (i = 0; i < 128; i++)
		context_fs_failures[i].stage = NULL;
	status = services->LocateHandleBuffer(ByProtocol, &context_fs_guid, NULL, &count, &handles);
	TextPut(text, "{\"enumeration_status\":");
	TextHex(text, status, 16);
	TextPut(text, ",\"filesystem_count\":");
	TextDec(text, count);
	TextPut(text, ",\"candidates\":[");
	if (EFI_ERROR(status) || !handles || count > 128) {
		complete = FALSE;
		goto out;
	}
	for (i = 0; i < count; i++) {
		EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *fs = NULL;
		EFI_PARTITION_INFO_PROTOCOL *partition = NULL;
		EFI_FILE_PROTOCOL *volume = NULL, *file = NULL;
		UINTN length = 0;
		EFI_STATUS path_status, close_status;
		UINT8 *path = context_path_status(services, handles[i], &length, &path_status);
		BOOLEAN esp = FALSE;

		if (!path) {
			context_fs_failure(i, "device_path", path_status, NULL, 0);
			complete = FALSE;
			continue;
		}
		if (DpHasUsb(path, length))
			continue;
		status = services->HandleProtocol(handles[i], &context_fs_guid, (VOID **)&fs);
		if (EFI_ERROR(status) || !fs || !fs->OpenVolume) {
			context_fs_failure(i, "filesystem_protocol", EFI_ERROR(status) ? status :
				EFI_COMPROMISED_DATA, path, length);
			complete = FALSE;
			continue;
		}
		status = fs->OpenVolume(fs, &volume);
		if (EFI_ERROR(status) || !volume) {
			context_fs_failure(i, "open_volume", EFI_ERROR(status) ? status :
				EFI_COMPROMISED_DATA, path, length);
			complete = FALSE;
			continue;
		}
		status = volume->Open(volume, &file, L"EFI\\Microsoft\\Boot\\bootmgfw.efi", EFI_FILE_MODE_READ, 0);
		close_status = volume->Close(volume);
		if (EFI_ERROR(close_status)) {
			context_fs_failure(i, "close_volume", close_status, path, length);
			complete = FALSE;
		}
		if (status == EFI_NOT_FOUND)
			continue;
		if (candidates == MAX_BOOT_FILES) {
			context_fs_failure(i, "candidate_limit", EFI_BAD_BUFFER_SIZE, path, length);
			if (file)
				file->Close(file);
			complete = FALSE;
			break;
		}
		if (candidates)
			TextPut(text, ",");
		TextPut(text, "{\"path\":");
		context_path_json(text, path, length);
		TextPut(text, ",\"partition\":");
		if (!context_partition(text, path, length)) {
			context_fs_failure(i, "partition_path", EFI_COMPROMISED_DATA, path, length);
			complete = FALSE;
		}
		if (!EFI_ERROR(services->HandleProtocol(handles[i], &context_partition_guid, (VOID **)&partition)) &&
		    partition && partition->Revision == EFI_PARTITION_INFO_PROTOCOL_REVISION &&
		    partition->Type == PARTITION_TYPE_GPT)
			esp = BytesEqual(&partition->Info.Gpt.PartitionTypeGUID, &context_esp_guid, sizeof(EFI_GUID));
		TextPut(text, ",\"esp_type_confirmed\":");
		TextPut(text, esp ? "true" : "false");
		TextPut(text, ",\"open_status\":");
		TextHex(text, status, 16);
		if (!EFI_ERROR(status) && file) {
			status = context_boot_file(services, file, candidates, text, save);
			if (!EFI_ERROR(status))
				saved++;
		}
		if (EFI_ERROR(status) || !file) {
			context_fs_failure(i, "boot_file", EFI_ERROR(status) ? status :
				EFI_COMPROMISED_DATA, path, length);
			complete = FALSE;
		}
		if (file) {
			close_status = file->Close(file);
			if (EFI_ERROR(close_status)) {
				context_fs_failure(i, "close_file", close_status, path, length);
				complete = FALSE;
			}
		}
		TextPut(text, ",\"capture_status\":");
		TextHex(text, status, 16);
		TextPut(text, "}");
		candidates++;
	}
out:
	if (handles)
		services->FreePool(handles);
	TextPut(text, "],\"candidate_count\":");
	TextDec(text, candidates);
	TextPut(text, ",\"saved_count\":");
	TextDec(text, saved);
	TextPut(text, ",\"complete\":");
	TextPut(text, complete ? "true" : "false");
	TextPut(text, ",\"filesystem_errors\":[");
	for (i = 0; i < 128; i++) {
		const struct context_fs_failure *failure = &context_fs_failures[i];

		if (!failure->stage)
			continue;
		if (failures++)
			TextPut(text, ",");
		TextPut(text, "{\"filesystem_index\":");
		TextDec(text, i);
		TextPut(text, ",\"stage\":\"");
		TextPut(text, failure->stage);
		TextPut(text, "\",\"status\":");
		TextHex(text, failure->status, 16);
		TextPut(text, ",\"path\":");
		context_path_json(text, failure->path, failure->path_bytes);
		TextPut(text, "}");
	}
	TextPut(text, "]}");
}

static VOID context_variables(TEXT *text, EFI_SYSTEM_TABLE *st)
{
	CHAR16 *names[] = {L"SecureBoot", L"SetupMode", L"BootCurrent", L"ConOut", L"ConOutDev"};
	const char *keys[] = {"secure_boot", "setup_mode", "boot_current", "conout", "conout_dev"};
	static UINT8 buffer[PROBE_DP_MAX];
	UINTN i;

	TextPut(text, "{");
	for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
		UINTN bytes = sizeof(buffer);
		EFI_STATUS status = EFI_UNSUPPORTED;

		if (i)
			TextPut(text, ",");
		TextPut(text, "\"");
		TextPut(text, keys[i]);
		TextPut(text, "\":{\"status\":");
		if (st->RuntimeServices && st->RuntimeServices->GetVariable)
			status = st->RuntimeServices->GetVariable(names[i], &context_global_guid, NULL, &bytes, buffer);
		TextHex(text, status, 16);
		TextPut(text, ",\"hex\":");
		if (!EFI_ERROR(status) && bytes <= sizeof(buffer))
			TextBytes(text, buffer, bytes);
		else
			TextPut(text, "null");
		TextPut(text, "}");
	}
	TextPut(text, "}");
}

EFI_STATUS probe_capture_context(EFI_SYSTEM_TABLE *st, EFI_HANDLE image_handle,
				 EFI_HANDLE target, PROBE_SAVE save)
{
	EFI_BOOT_SERVICES *services = st->BootServices;
	EFI_LOADED_IMAGE_PROTOCOL *image = NULL;
	UINT8 *path = NULL;
	UINTN path_bytes = 0;
	CHAR8 *data = NULL;
	TEXT text;
	EFI_STATUS status;

	status = services->AllocatePool(EfiLoaderData, CONTEXT_BYTES, (VOID **)&data);
	if (EFI_ERROR(status) || !data)
		return EFI_OUT_OF_RESOURCES;
	TextInit(&text, data, CONTEXT_BYTES);
	TextPut(&text, "{\"context_version\":1,\"probe_version\":3,\"build_id\":\"" PROBE_BUILD_ID
		"\",\"gpu_writes_enabled\":false,\"release_authorized\":false,\"pci_id\":\"0x270510de\",\"subsystem_id\":");
	TextHex(&text, PROBE_SUBSYSTEM_ID, 8);
	TextPut(&text, ",\"firmware_revision\":");
	TextHex(&text, st->FirmwareRevision, 8);
	if (!EFI_ERROR(services->HandleProtocol(image_handle, &context_loaded_guid, (VOID **)&image)) && image)
		path = context_path(services, image->DeviceHandle, &path_bytes);
	TextPut(&text, ",\"loading_path\":");
	context_path_json(&text, path, path_bytes);
	TextPut(&text, ",\"variables\":");
	context_variables(&text, st);
	TextPut(&text, ",\"platform\":");
	SamplePlatform(&text, st, target);
	TextPut(&text, ",\"windows_boot\":");
	context_windows(&text, services, save);
	TextPut(&text, "}\n");
	status = text.failed ? EFI_BUFFER_TOO_SMALL : save(L"CONTEXT.JSON", data, text.len);
	services->FreePool(data);
	return status;
}
