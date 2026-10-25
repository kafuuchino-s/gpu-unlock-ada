/* SPDX-License-Identifier: GPL-2.0-only */
/* Host checks of the actual probe against mocked UEFI PCI and file protocols. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* EDK2 provides its own equivalent NULL definition. */
#undef NULL
#include "uefi/probe.c"

static EFI_BOOT_SERVICES fake_bs;
static EFI_SYSTEM_TABLE fake_table;
static EFI_PCI_IO_PROTOCOL fake_pci;
static EFI_FILE_PROTOCOL fake_file;
static UINT32 fake_config[64];
static UINTN target_count, gpu_reads, prom_reads, gpu_writes, active_pools;
static UINT64 fake_bar_length;
static UINT16 extra_registers_read;
static UINT32 fake_chip;
static BOOLEAN fail_prom, fail_storage;
static UINT8 fake_device_path[10];

static VOID *host_alloc(UINTN bytes)
{
	VOID *value = malloc(bytes);

	assert(value);
	active_pools++;
	return value;
}

static EFI_STATUS EFIAPI fake_free(VOID *value)
{
	assert(value && active_pools);
	active_pools--;
	free(value);
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_allocate(EFI_MEMORY_TYPE type, UINTN bytes, VOID **out)
{
	(void)type;
	*out = host_alloc(bytes);
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_open(EFI_FILE_PROTOCOL *self, EFI_FILE_PROTOCOL **out,
				   CHAR16 *name, UINT64 mode, UINT64 attributes)
{
	(void)self; (void)name; (void)attributes;
	if (!(mode & EFI_FILE_MODE_CREATE))
		return EFI_NOT_FOUND;
	*out = &fake_file;
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_close(EFI_FILE_PROTOCOL *self)
{
	(void)self;
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_position(EFI_FILE_PROTOCOL *self, UINT64 position)
{
	(void)self;
	assert(position == 0);
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_write(EFI_FILE_PROTOCOL *self, UINTN *bytes, VOID *data)
{
	(void)self;
	assert(data && *bytes);
	return fail_storage && *bytes == PROM_BYTES ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_pci_read(EFI_PCI_IO_PROTOCOL *self,
	EFI_PCI_IO_PROTOCOL_WIDTH width, UINT32 offset, UINTN count, VOID *out)
{
	(void)self;
	assert(width == EfiPciIoWidthUint32 && offset % 4 == 0);
	assert(offset + count * 4 <= sizeof(fake_config));
	memcpy(out, (UINT8 *)fake_config + offset, count * 4);
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI forbidden_pci_write(EFI_PCI_IO_PROTOCOL *self,
	EFI_PCI_IO_PROTOCOL_WIDTH width, UINT32 offset, UINTN count, VOID *data)
{
	(void)self; (void)width; (void)offset; (void)count; (void)data;
	gpu_writes++;
	return EFI_ACCESS_DENIED;
}

static EFI_STATUS EFIAPI fake_memory_read(EFI_PCI_IO_PROTOCOL *self,
	EFI_PCI_IO_PROTOCOL_WIDTH width, UINT8 bar, UINT64 offset, UINTN count, VOID *out)
{
	UINTN i;

	(void)self;
	assert(width == EfiPciIoWidthUint32 && bar == 0 && offset % 4 == 0);
	assert(offset + count * 4 <= fake_bar_length);
	gpu_reads++;
	if (offset >= PROM_OFFSET && offset < PROM_OFFSET + PROM_BYTES) {
		prom_reads++;
		if (fail_prom && offset == PROM_OFFSET + 65536)
			return EFI_DEVICE_ERROR;
		for (i = 0; i < count * 4; i++)
			((UINT8 *)out)[i] = (UINT8)(offset + i);
	} else {
		static const UINT32 additional[] = {0x1fa824, 0x823814, 0x823818,
			0x625f04, 0x1fa7c8, 0x1fa82c, 0x1fa830, 0x824148, 0x8241e0};

		assert(count == 1);
		for (i = 0; i < sizeof(additional) / sizeof(additional[0]); i++)
			if (offset == additional[i])
				extra_registers_read |= (UINT16)(1U << i);
		*(UINT32 *)out = offset == 0 ? fake_chip : 0;
	}
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI forbidden_memory_write(EFI_PCI_IO_PROTOCOL *self,
	EFI_PCI_IO_PROTOCOL_WIDTH width, UINT8 bar, UINT64 offset, UINTN count, VOID *data)
{
	(void)self; (void)width; (void)bar; (void)offset; (void)count; (void)data;
	gpu_writes++;
	return EFI_ACCESS_DENIED;
}

static EFI_STATUS EFIAPI fake_location(EFI_PCI_IO_PROTOCOL *self, UINTN *segment,
				       UINTN *bus, UINTN *device, UINTN *function)
{
	(void)self;
	*segment = 0; *bus = 1; *device = 0; *function = 0;
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_bar(EFI_PCI_IO_PROTOCOL *self, UINT8 bar,
				 UINT64 *supported, VOID **out)
{
	EFI_ACPI_ADDRESS_SPACE_DESCRIPTOR *resource = host_alloc(sizeof(*resource));

	(void)self; (void)supported;
	assert(bar == 0);
	memset(resource, 0, sizeof(*resource));
	resource->Desc = ACPI_ADDRESS_SPACE_DESCRIPTOR;
	resource->Len = sizeof(*resource) - 3;
	resource->AddrSpaceGranularity = 64;
	resource->AddrRangeMin = 0xa00000000ULL;
	resource->AddrLen = fake_bar_length;
	*out = resource;
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_handles(EFI_LOCATE_SEARCH_TYPE search, EFI_GUID *guid,
				     VOID *key, UINTN *count, EFI_HANDLE **out)
{
	UINTN i;

	(void)search; (void)key;
	assert(!memcmp(guid, &pci_guid, sizeof(*guid)));
	*count = target_count;
	*out = host_alloc(sizeof(**out) * target_count);
	for (i = 0; i < target_count; i++)
		(*out)[i] = (EFI_HANDLE)(i + 1);
	return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI fake_protocol(EFI_HANDLE handle, EFI_GUID *guid, VOID **out)
{
	(void)handle;
	if (!memcmp(guid, &path_guid, sizeof(*guid))) {
		*out = fake_device_path;
		return EFI_SUCCESS;
	}
	assert(!memcmp(guid, &pci_guid, sizeof(*guid)));
	*out = &fake_pci;
	return EFI_SUCCESS;
}

static VOID reset_mocks(VOID)
{
	assert(!gpu_writes && !active_pools);
	memset(&fake_bs, 0, sizeof(fake_bs));
	memset(&fake_table, 0, sizeof(fake_table));
	memset(&fake_pci, 0, sizeof(fake_pci));
	memset(&fake_file, 0, sizeof(fake_file));
	memset(fake_config, 0, sizeof(fake_config));
	memcpy(fake_device_path, (UINT8[]){3, 5, 6, 0, 1, 0, 0x7f, 0xff, 4, 0}, 10);
	fake_bs.AllocatePool = fake_allocate;
	fake_bs.FreePool = fake_free;
	fake_bs.LocateHandleBuffer = fake_handles;
	fake_bs.HandleProtocol = fake_protocol;
	fake_table.BootServices = &fake_bs;
	fake_file.Open = fake_open;
	fake_file.Write = fake_write;
	fake_file.Close = fake_close;
	fake_file.Flush = fake_close;
	fake_file.SetPosition = fake_position;
	fake_pci.Pci.Read = fake_pci_read;
	fake_pci.Pci.Write = forbidden_pci_write;
	fake_pci.Mem.Read = fake_memory_read;
	fake_pci.Mem.Write = forbidden_memory_write;
	fake_pci.GetLocation = fake_location;
	fake_pci.GetBarAttributes = fake_bar;
	fake_config[0] = TARGET_ID;
	fake_config[1] = 0x00100002;
	fake_config[2] = 0x030000a1;
	fake_config[11] = PROBE_SUBSYSTEM_ID;
	fake_config[0x34 / 4] = 0x60;
	fake_config[0x60 / 4] = 0x7801;
	fake_config[0x78 / 4] = 0x10;
	fake_config[0x7c / 4] = 1U << 28;
	table = &fake_table; bs = &fake_bs;
	run = report = &fake_file;
	report_used = 0; log_overflow = FALSE;
	target_count = 1; gpu_reads = prom_reads = 0; extra_registers_read = 0;
	fake_bar_length = 0x1000000; fake_chip = AD103_CHIP;
	fail_prom = fail_storage = FALSE;
}

int main(void)
{
	EFI_PCI_IO_PROTOCOL *pci = NULL;
	EFI_HANDLE handle = NULL;
	UINT32 config[64];
	UINT64 bytes = 0;

	reset_mocks();
	assert(is_usb((EFI_HANDLE)1));
	reset_mocks();
	fake_device_path[1] = MSG_SATA_DP;
	assert(!is_usb((EFI_HANDLE)1));
	reset_mocks();
	fake_device_path[2] = 0;
	assert(!is_usb((EFI_HANDLE)1));
	reset_mocks();
	assert(find_target(&pci, &handle) == EFI_SUCCESS && pci == &fake_pci);
	reset_mocks();
	fake_config[0] ^= 1;
	assert(find_target(&pci, &handle) == EFI_UNSUPPORTED && !gpu_reads);
	reset_mocks();
	fake_config[11] ^= 1;
	assert(find_target(&pci, &handle) == EFI_UNSUPPORTED && !gpu_reads);
	reset_mocks();
	target_count = 2;
	assert(find_target(&pci, &handle) == EFI_UNSUPPORTED && !gpu_reads);
	reset_mocks();
	assert(check_config(&fake_pci, config) == EFI_SUCCESS);
	reset_mocks();
	fake_config[0x64 / 4] = 3;
	assert(check_config(&fake_pci, config) == EFI_NOT_READY && !gpu_reads);
	reset_mocks();
	fake_config[1] &= ~2U;
	assert(check_config(&fake_pci, config) == EFI_UNSUPPORTED && !gpu_reads);
	reset_mocks();
	fake_config[0x60 / 4] = 0x6001;
	assert(check_config(&fake_pci, config) == EFI_COMPROMISED_DATA && !gpu_reads);
	reset_mocks();
	assert(check_bar(&fake_pci, &bytes) == EFI_SUCCESS && bytes == 0x1000000);
	reset_mocks();
	fake_bar_length = PROM_OFFSET;
	assert(check_bar(&fake_pci, &bytes) == EFI_UNSUPPORTED && !gpu_reads);
	reset_mocks();
	fake_chip = 0xffffffff;
	assert(collect(&fake_pci, fake_bar_length) == EFI_UNSUPPORTED && !prom_reads);
	reset_mocks();
	assert(collect(&fake_pci, fake_bar_length) == EFI_SUCCESS && prom_reads == 16);
	assert(gpu_reads == 37 && extra_registers_read == 0x1ff);
	reset_mocks();
	fail_prom = TRUE;
	assert(collect(&fake_pci, fake_bar_length) == EFI_DEVICE_ERROR && prom_reads == 2);
	reset_mocks();
	fail_storage = TRUE;
	assert(collect(&fake_pci, fake_bar_length) == EFI_DEVICE_ERROR && prom_reads == 16);
	assert(!gpu_writes && !active_pools);
	puts("17 probe host checks passed; no PCI/MMIO writes; allocations released.");
	return 0;
}
