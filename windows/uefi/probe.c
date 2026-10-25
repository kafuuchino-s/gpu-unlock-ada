/* SPDX-License-Identifier: GPL-2.0-only */
/* GPU reads only. File writes go exclusively to the USB that loaded this EFI.
 * No GPU firmware, PCI writes, DMA maps, driver disconnects or resets are used.
 */
#include <Uefi.h>
#include <Protocol/DevicePath.h>
#include <Protocol/GraphicsOutput.h>
#include <Protocol/LoadedImage.h>
#include <Protocol/PciIo.h>
#include <Protocol/SimpleFileSystem.h>
#include <IndustryStandard/Acpi.h>
#include "context.h"

#ifndef PROBE_BUILD_ID
#error Build with an ID covering the entire probe source set
#endif

#ifndef PROBE_SUBSYSTEM_ID
#error Build with the exact subsystem ID for the intended board
#endif

#define TARGET_ID 0x270510deU
#define AD103_CHIP 0x193000a1U
#define PROM_OFFSET 0x00300000U
#define PROM_BYTES 0x00100000U
#define REPORT_BYTES 16384U

static EFI_SYSTEM_TABLE *table;
static EFI_BOOT_SERVICES *bs;
static EFI_FILE_PROTOCOL *root, *parent, *run, *report;
static EFI_GUID pci_guid = EFI_PCI_IO_PROTOCOL_GUID;
static EFI_GUID loaded_guid = EFI_LOADED_IMAGE_PROTOCOL_GUID;
static EFI_GUID fs_guid = EFI_SIMPLE_FILE_SYSTEM_PROTOCOL_GUID;
static EFI_GUID path_guid = EFI_DEVICE_PATH_PROTOCOL_GUID;
static EFI_GUID gop_guid = EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID;
static char report_text[REPORT_BYTES];
static UINTN report_used;
static BOOLEAN log_overflow;

static VOID log_text(const char *text)
{
	const char *cursor = text;

	while (*cursor) {
		if (report_used + 1 >= sizeof(report_text)) {
			log_overflow = TRUE;
			break;
		}
		report_text[report_used++] = *cursor++;
	}
	if (!table->ConOut || !table->ConOut->OutputString)
		return;
	while (*text) {
		CHAR16 line[128];
		UINTN used = 0;

		while (*text && used < 126) {
			if (*text == '\n')
				line[used++] = '\r';
			line[used++] = (UINT8)*text++;
		}
		line[used] = 0;
		table->ConOut->OutputString(table->ConOut, line);
	}
}

static VOID log_number(const char *name, UINT64 value)
{
	static const char digits[] = "0123456789abcdef";
	char hex[20] = "0x0000000000000000\n";
	UINTN i;

	for (i = 0; i < 16; i++)
		hex[17 - i] = digits[(value >> (i * 4)) & 15];
	log_text(name);
	log_text("=");
	log_text(hex);
}

static EFI_STATUS flush_report(VOID)
{
	UINTN bytes = report_used;
	EFI_STATUS status;

	if (!report || log_overflow)
		return EFI_DEVICE_ERROR;
	status = report->SetPosition(report, 0);
	if (!EFI_ERROR(status))
		status = report->Write(report, &bytes, report_text);
	if (!EFI_ERROR(status) && bytes != report_used)
		status = EFI_BAD_BUFFER_SIZE;
	if (!EFI_ERROR(status))
		status = report->Flush(report);
	return status;
}

static BOOLEAN is_usb(EFI_HANDLE handle)
{
	EFI_DEVICE_PATH_PROTOCOL *path = NULL;
	UINTN total = 0, nodes;
	BOOLEAN usb = FALSE;

	if (EFI_ERROR(bs->HandleProtocol(handle, &path_guid, (VOID **)&path)) || !path)
		return FALSE;
	for (nodes = 0; nodes < 64 && total + 4 <= 4096; nodes++) {
		UINTN bytes = path->Length[0] + ((UINTN)path->Length[1] << 8);

		if (bytes < 4 || bytes > 4096 - total)
			return FALSE;
		if (path->Type == MESSAGING_DEVICE_PATH &&
		    path->SubType == MSG_USB_DP && bytes == sizeof(USB_DEVICE_PATH))
			usb = TRUE;
		if (path->Type == END_DEVICE_PATH_TYPE)
			return usb && bytes == 4 && path->SubType == END_ENTIRE_DEVICE_PATH_SUBTYPE;
		total += bytes;
		path = (VOID *)((UINT8 *)path + bytes);
	}
	return FALSE;
}

static EFI_STATUS open_report(EFI_HANDLE image)
{
	EFI_LOADED_IMAGE_PROTOCOL *loaded = NULL;
	EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *fs = NULL;
	EFI_STATUS status;
	UINTN slot;

	status = bs->HandleProtocol(image, &loaded_guid, (VOID **)&loaded);
	if (EFI_ERROR(status) || !loaded || !is_usb(loaded->DeviceHandle))
		return EFI_UNSUPPORTED;
	status = bs->HandleProtocol(loaded->DeviceHandle, &fs_guid, (VOID **)&fs);
	if (EFI_ERROR(status) || !fs)
		return EFI_UNSUPPORTED;
	status = fs->OpenVolume(fs, &root);
	if (EFI_ERROR(status) || !root)
		return EFI_DEVICE_ERROR;
	status = root->Open(root, &parent, L"GPU-UNLOCK-2705",
			    EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE, 0);
	if (EFI_ERROR(status) || !parent)
		return EFI_NOT_FOUND;
	for (slot = 0; slot < 100; slot++) {
		CHAR16 name[] = L"RUN00";
		EFI_FILE_PROTOCOL *existing = NULL;

		name[3] = (CHAR16)('0' + slot / 10);
		name[4] = (CHAR16)('0' + slot % 10);
		status = parent->Open(parent, &existing, name, EFI_FILE_MODE_READ, 0);
		if (!EFI_ERROR(status)) {
			if (!existing || EFI_ERROR(existing->Close(existing)))
				return EFI_DEVICE_ERROR;
			continue;
		}
		if (status != EFI_NOT_FOUND)
			return status;
		status = parent->Open(parent, &run, name,
			EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE | EFI_FILE_MODE_CREATE,
			EFI_FILE_DIRECTORY);
		if (EFI_ERROR(status) || !run)
			return EFI_DEVICE_ERROR;
		status = run->Open(run, &report, L"REPORT.TXT",
			EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE | EFI_FILE_MODE_CREATE, 0);
		if (EFI_ERROR(status) || !report)
			return EFI_DEVICE_ERROR;
		log_text("probe_version=3\nmode=gpu_read_only\nunlock_implemented=0\n");
		log_text("build_id=" PROBE_BUILD_ID "\n");
		log_number("run_index", slot);
		return flush_report();
	}
	return EFI_VOLUME_FULL;
}

static EFI_STATUS save_file(CHAR16 *name, const VOID *data, UINTN bytes)
{
	EFI_FILE_PROTOCOL *file = NULL;
	EFI_STATUS status, closed;
	UINTN written = bytes;

	status = run->Open(run, &file, name, EFI_FILE_MODE_READ, 0);
	if (!EFI_ERROR(status)) {
		if (file)
			file->Close(file);
		return EFI_ALREADY_STARTED;
	}
	if (status != EFI_NOT_FOUND)
		return status;
	status = run->Open(run, &file, name,
		EFI_FILE_MODE_READ | EFI_FILE_MODE_WRITE | EFI_FILE_MODE_CREATE, 0);
	if (EFI_ERROR(status) || !file)
		return EFI_ERROR(status) ? status : EFI_DEVICE_ERROR;
	status = file->Write(file, &written, (VOID *)data);
	if (!EFI_ERROR(status) && written != bytes)
		status = EFI_BAD_BUFFER_SIZE;
	if (!EFI_ERROR(status))
		status = file->Flush(file);
	closed = file->Close(file);
	return EFI_ERROR(status) ? status : closed;
}

static EFI_STATUS find_target(EFI_PCI_IO_PROTOCOL **out, EFI_HANDLE *target)
{
	EFI_HANDLE *handles = NULL;
	UINTN count = 0, found = 0, i;
	EFI_STATUS status;

	status = bs->LocateHandleBuffer(ByProtocol, &pci_guid, NULL, &count, &handles);
	if (EFI_ERROR(status))
		return status;
	if (!handles || count > 512) {
		if (handles)
			bs->FreePool(handles);
		return EFI_UNSUPPORTED;
	}
	for (i = 0; i < count; i++) {
		EFI_PCI_IO_PROTOCOL *pci = NULL;
		UINT32 id = 0, subsystem = 0;

		status = bs->HandleProtocol(handles[i], &pci_guid, (VOID **)&pci);
		if (EFI_ERROR(status) || !pci || !pci->Pci.Read)
			continue;
		status = pci->Pci.Read(pci, EfiPciIoWidthUint32, 0, 1, &id);
		if (EFI_ERROR(status) || id != TARGET_ID)
			continue;
		status = pci->Pci.Read(pci, EfiPciIoWidthUint32, 0x2c, 1, &subsystem);
		if (EFI_ERROR(status) || subsystem != PROBE_SUBSYSTEM_ID)
			continue;
		found++;
		*out = pci;
		*target = handles[i];
	}
	bs->FreePool(handles);
	log_number("matching_targets", found);
	return found == 1 ? EFI_SUCCESS : EFI_UNSUPPORTED;
}

static EFI_STATUS check_config(EFI_PCI_IO_PROTOCOL *pci, UINT32 config[64])
{
	UINTN segment, bus, device, function, i;
	UINT32 cap;
	UINT64 visited = 0;
	BOOLEAN pm_found = FALSE;
	EFI_STATUS status;

	status = pci->GetLocation(pci, &segment, &bus, &device, &function);
	if (EFI_ERROR(status))
		return status;
	log_number("segment", segment);
	log_number("bus", bus);
	log_number("device", device);
	log_number("function", function);
	status = pci->Pci.Read(pci, EfiPciIoWidthUint32, 0, 64, config);
	if (EFI_ERROR(status))
		return status;
	log_number("pci_id", config[0]);
	log_number("subsystem_id", config[0x2c / 4]);
	log_number("pci_class_revision", config[2]);
	log_number("pci_command", config[1] & 0xffff);
	for (i = 0; i < 6; i++) {
		char key[] = "bar0_config";

		key[3] = (char)('0' + i);
		log_number(key, config[4 + i]);
	}
	status = save_file(L"PCI-CONFIG.BIN", config, 256);
	log_number("config_save_status", status);
	if (EFI_ERROR(status))
		return status;
	if (config[0] != TARGET_ID || config[0x2c / 4] != PROBE_SUBSYSTEM_ID ||
	    (config[2] >> 8) != 0x030000 || !(config[1] & 2))
		return EFI_UNSUPPORTED;
	if (!(config[1] & 0x00100000U))
		return EFI_UNSUPPORTED;
	cap = config[0x34 / 4] & 0xff;
	while (cap) {
		UINT32 header;

		if (cap < 0x40 || cap > 0xfc || cap & 3 ||
		    (visited & ((UINT64)1 << (cap / 4))))
			return EFI_COMPROMISED_DATA;
		visited |= (UINT64)1 << (cap / 4);
		header = config[cap / 4];
		if ((header & 0xff) == 1) {
			if (cap > 0xf8)
				return EFI_COMPROMISED_DATA;
			pm_found = TRUE;
			log_number("pm_cap", cap);
			log_number("pmcsr", config[(cap + 4) / 4] & 0xffff);
			if (config[(cap + 4) / 4] & 3)
				return EFI_NOT_READY;
		}
		if ((header & 0xff) == 0x10) {
			if (cap > 0xf8)
				return EFI_COMPROMISED_DATA;
			log_number("pcie_cap", cap);
			log_number("pcie_device_cap", config[(cap + 4) / 4]);
		}
		cap = (header >> 8) & 0xff;
	}
	return pm_found ? EFI_SUCCESS : EFI_UNSUPPORTED;
}

static EFI_STATUS check_bar(EFI_PCI_IO_PROTOCOL *pci, UINT64 *bytes)
{
	EFI_ACPI_ADDRESS_SPACE_DESCRIPTOR *resource = NULL;
	EFI_STATUS status;

	status = pci->GetBarAttributes(pci, 0, NULL, (VOID **)&resource);
	if (EFI_ERROR(status))
		return status;
	if (!resource || resource->Desc != ACPI_ADDRESS_SPACE_DESCRIPTOR ||
	    resource->Len != sizeof(*resource) - 3 || resource->ResType != 0 ||
	    resource->AddrTranslationOffset ||
	    (resource->AddrSpaceGranularity != 32 && resource->AddrSpaceGranularity != 64)) {
		status = EFI_UNSUPPORTED;
	} else {
		log_number("bar0_base", resource->AddrRangeMin);
		log_number("bar0_length", resource->AddrLen);
		log_number("bar0_max_descriptor", resource->AddrRangeMax);
		log_number("bar0_granularity", resource->AddrSpaceGranularity);
		*bytes = resource->AddrLen;
		if (!resource->AddrRangeMin || *bytes < 0x009a014cU)
			status = EFI_UNSUPPORTED;
	}
	if (resource)
		bs->FreePool(resource);
	return status;
}

static VOID log_gop(EFI_HANDLE target)
{
	EFI_HANDLE *handles = NULL;
	UINTN count = 0, matched = 0, i;
	EFI_STATUS status;

	status = bs->LocateHandleBuffer(ByProtocol, &gop_guid, NULL, &count, &handles);
	log_number("gop_enumeration_status", status);
	if (EFI_ERROR(status))
		return;
	if (handles && count <= 128) {
		for (i = 0; i < count; i++) {
			EFI_DEVICE_PATH_PROTOCOL *path = NULL;
			EFI_GRAPHICS_OUTPUT_PROTOCOL *gop = NULL;
			EFI_HANDLE owner = NULL;

			if (EFI_ERROR(bs->HandleProtocol(handles[i], &path_guid, (VOID **)&path)) || !path)
				continue;
			if (EFI_ERROR(bs->LocateDevicePath(&pci_guid, &path, &owner)) || owner != target)
				continue;
			if (EFI_ERROR(bs->HandleProtocol(handles[i], &gop_guid, (VOID **)&gop)) ||
			    !gop || !gop->Mode || !gop->Mode->Info)
				continue;
			matched++;
			log_number("target_gop_framebuffer", gop->Mode->FrameBufferBase);
			log_number("target_gop_framebuffer_bytes", gop->Mode->FrameBufferSize);
			log_number("target_gop_width", gop->Mode->Info->HorizontalResolution);
			log_number("target_gop_height", gop->Mode->Info->VerticalResolution);
		}
	}
	log_number("gop_handles", count);
	log_number("target_gop_handles", matched);
	if (handles)
		bs->FreePool(handles);
}

static EFI_STATUS collect(EFI_PCI_IO_PROTOCOL *pci, UINT64 bar_bytes)
{
	static const struct {
		const char *name;
		UINT32 offset;
	} registers[] = {
		{"chip", 0x00000000},
		{"fb_mib", 0x001183a4},
		{"gfw_plm", 0x00118128},
		{"gfw_boot", 0x00118234},
		{"gfw_cpuctl", 0x00110100},
		{"wpr2_hi", 0x001fa828},
		{"wpr_cfg_plm", 0x001fa7cc},
		{"wpr_plm", 0x001fa7c4},
		{"fbpa_plm", 0x009a0148},
		{"feat_plm", 0x00823804},
		{"ss0", 0x0082381c},
		{"ss1", 0x00823820},
		{"wpr2_lo", 0x001fa824},
		{"reg_00823814", 0x00823814},
		{"reg_00823818", 0x00823818},
		{"reg_00625f04", 0x00625f04},
		{"reg_001fa7c8", 0x001fa7c8},
		{"reg_001fa82c", 0x001fa82c},
		{"reg_001fa830", 0x001fa830},
		{"reg_00824148", 0x00824148},
		{"reg_008241e0", 0x008241e0},
	};
	UINT8 *prom = NULL;
	UINTN i, offset;
	EFI_STATUS status;

	log_number("pci_rom_bytes", pci->RomSize);
	if (pci->RomImage && pci->RomSize && pci->RomSize <= 2 * 1024 * 1024) {
		status = save_file(L"PCI-ROM.BIN", pci->RomImage, (UINTN)pci->RomSize);
		log_number("pci_rom_save_status", status);
		if (EFI_ERROR(status))
			return status;
	} else {
		log_text("pci_rom_available=0\n");
	}
	if (EFI_ERROR(flush_report()))
		return EFI_DEVICE_ERROR;
	for (i = 0; i < sizeof(registers) / sizeof(registers[0]); i++) {
		UINT32 value = 0;

		if ((UINT64)registers[i].offset + 4 > bar_bytes)
			return EFI_UNSUPPORTED;
		status = pci->Mem.Read(pci, EfiPciIoWidthUint32, 0, registers[i].offset, 1, &value);
		if (EFI_ERROR(status))
			return status;
		log_number(registers[i].name, value);
		if (i == 0 && value != AD103_CHIP)
			return EFI_UNSUPPORTED;
	}
	log_number("prom_bar_offset", PROM_OFFSET);
	if ((UINT64)PROM_OFFSET + PROM_BYTES > bar_bytes)
		return EFI_UNSUPPORTED;
	status = bs->AllocatePool(EfiLoaderData, PROM_BYTES, (VOID **)&prom);
	if (EFI_ERROR(status) || !prom)
		return EFI_OUT_OF_RESOURCES;
	for (offset = 0; offset < PROM_BYTES; offset += 65536) {
		log_number("prom_bytes_completed", offset);
		if (EFI_ERROR(flush_report())) {
			status = EFI_DEVICE_ERROR;
			break;
		}
		status = pci->Mem.Read(pci, EfiPciIoWidthUint32, 0,
			PROM_OFFSET + offset, 65536 / 4, prom + offset);
		if (EFI_ERROR(status))
			break;
	}
	if (!EFI_ERROR(status)) {
		log_number("prom_bytes_completed", PROM_BYTES);
		status = save_file(L"PROM.BIN", prom, PROM_BYTES);
		log_number("prom_save_status", status);
	}
	bs->FreePool(prom);
	return status;
}

static VOID close_files(VOID)
{
	EFI_FILE_PROTOCOL **files[] = {&report, &run, &parent, &root};
	UINTN i;

	for (i = 0; i < sizeof(files) / sizeof(files[0]); i++) {
		if (*files[i]) {
			(*files[i])->Close(*files[i]);
			*files[i] = NULL;
		}
	}
}

EFI_STATUS EFIAPI EfiMain(EFI_HANDLE image, EFI_SYSTEM_TABLE *system_table)
{
	EFI_PCI_IO_PROTOCOL *pci = NULL;
	EFI_HANDLE target = NULL;
	UINT32 config[64];
	UINT64 bar_bytes = 0;
	EFI_STATUS status;
	UINTN i;

	table = system_table;
	bs = table->BootServices;
	status = open_report(image);
	if (EFI_ERROR(status)) {
		log_text("Cannot open probe log on the loading USB. Copy the entire USB tree.\n");
		log_number("open_report_status", status);
		goto out;
	}
	log_number("expected_pci_id", TARGET_ID);
	log_number("expected_subsystem_id", PROBE_SUBSYSTEM_ID);
	status = find_target(&pci, &target);
	if (EFI_ERROR(status))
		goto finish;
	status = check_config(pci, config);
	if (EFI_ERROR(status))
		goto finish;
	status = check_bar(pci, &bar_bytes);
	if (EFI_ERROR(status))
		goto finish;
	log_gop(target);
	log_text("stage=collect_rom_and_registers\n");
	if (EFI_ERROR(flush_report())) {
		status = EFI_DEVICE_ERROR;
		goto finish;
	}
	status = collect(pci, bar_bytes);
	if (EFI_ERROR(status))
		goto finish;
	log_text("stage=collect_uefi_and_windows_context\n");
	if (EFI_ERROR(flush_report())) {
		status = EFI_DEVICE_ERROR;
		goto finish;
	}
	status = probe_capture_context(table, image, target, save_file);
	log_number("context_save_status", status);
finish:
	log_number("probe_status", status);
	log_text("This was a diagnostic capture. No GPU unlock was attempted.\n");
	if (EFI_ERROR(flush_report()) && !EFI_ERROR(status))
		status = EFI_DEVICE_ERROR;
out:
	close_files();
	log_text("Press a key to return to firmware (automatic return after 30 seconds).\n");
	if (table->ConIn && table->ConIn->ReadKeyStroke) {
		for (i = 0; i < 300; i++) {
			EFI_INPUT_KEY key;

			if (table->ConIn->ReadKeyStroke(table->ConIn, &key) != EFI_NOT_READY)
				break;
			bs->Stall(100000);
		}
	}
	return status;
}
