<#
.SYNOPSIS
    Unpairs a Bluetooth device by (partial) name -- automates the manual
    "Remove device" step in Windows Settings > Bluetooth & devices.

.DESCRIPTION
    Some BLE devices (like the Fitbit Air) only support one active connection
    at a time. Windows' own Bluetooth stack can silently hold a stale
    connection in the background even when no app is using it, which stops
    the device from advertising and blocks new scan-based connections until
    it's manually "forgotten." This script automates that step.

.PARAMETER DeviceName
    A substring to match against paired Bluetooth device names (case-insensitive).
    Defaults to "Fitbit".

.NOTES
    Best-effort. Requires Windows 10/11. Safe to run even if the device isn't
    currently paired -- it just does nothing in that case.
#>

param(
    [string]$DeviceName = "Fitbit"
)

$ErrorActionPreference = "SilentlyContinue"

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

try {
    [Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime] | Out-Null

    $selector = [Windows.Devices.Enumeration.DeviceInformation]::GetAqsFilterFromDeviceClass(
        [Windows.Devices.Enumeration.DeviceClass]::Bluetooth
    )
    $devices = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) `
        ([Windows.Devices.Enumeration.DeviceInformationCollection])

    $matches = $devices | Where-Object { $_.Name -and $_.Name -like "*$DeviceName*" }

    if (-not $matches) {
        Write-Output "No paired device matching '$DeviceName' found -- nothing to unpair."
        exit 0
    }

    foreach ($d in $matches) {
        if ($d.Pairing -and $d.Pairing.IsPaired) {
            Write-Output "Unpairing: $($d.Name) ($($d.Id))"
            $result = Await ($d.Pairing.UnpairAsync()) ([Windows.Devices.Enumeration.DeviceUnpairingResult])
            Write-Output "  Status: $($result.Status)"
        }
        else {
            Write-Output "Already not paired: $($d.Name)"
        }
    }
}
catch {
    Write-Output "Unpair step failed (non-fatal): $_"
    exit 1
}
