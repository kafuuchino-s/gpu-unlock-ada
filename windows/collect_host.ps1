# SPDX-License-Identifier: GPL-2.0-only
# Read-only Windows inventory. Does not change drivers, boot entries or GPU state.
param([string]$OutputPath = (Join-Path $PSScriptRoot '..\build\windows\host-info.json'))

$ErrorActionPreference = 'Stop'
$gpuTaskOs = Get-CimInstance Win32_OperatingSystem
$gpuTaskBoard = Get-CimInstance Win32_BaseBoard
$gpuTaskSecureBoot = $null
try {
    $gpuTaskSecureBoot = (Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State').UEFISecureBootEnabled
} catch { }
$gpuTaskBitLocker = 'unknown'
try {
    $gpuTaskBitLocker = Get-BitLockerVolume -MountPoint $env:SystemDrive |
        Select-Object MountPoint, VolumeStatus, ProtectionStatus
} catch { }
$gpuTaskNvidia = & nvidia-smi --query-gpu=name,pci.bus_id,pci.device_id,pci.sub_device_id,vbios_version,memory.total,driver_version --format=csv,noheader,nounits
if ($LASTEXITCODE -ne 0) { throw 'nvidia-smi inventory query failed' }
$gpuTaskGpus = @($gpuTaskNvidia | ConvertFrom-Csv -Header name,bdf,pci_id,subsystem_id,vbios_version,memory_mib,driver_version)
$gpuTaskData = [ordered]@{
    captured_utc = [DateTime]::UtcNow.ToString('o')
    windows = [ordered]@{ caption = $gpuTaskOs.Caption; version = $gpuTaskOs.Version; architecture = $gpuTaskOs.OSArchitecture }
    mainboard = [ordered]@{ manufacturer = $gpuTaskBoard.Manufacturer; product = $gpuTaskBoard.Product }
    secure_boot_registry = $gpuTaskSecureBoot
    bitlocker = $gpuTaskBitLocker
    gpus = $gpuTaskGpus
}
$gpuTaskOutput = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($gpuTaskOutput)) | Out-Null
$gpuTaskData | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $gpuTaskOutput -Encoding UTF8
Write-Output $gpuTaskOutput
