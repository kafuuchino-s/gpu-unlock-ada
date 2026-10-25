/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef GPU_PROBE_CONTEXT_H
#define GPU_PROBE_CONTEXT_H
#include <Uefi.h>

typedef EFI_STATUS (*PROBE_SAVE)(CHAR16 *, const VOID *, UINTN);
EFI_STATUS probe_capture_context(EFI_SYSTEM_TABLE *st, EFI_HANDLE image,
				 EFI_HANDLE target, PROBE_SAVE save);
#endif
