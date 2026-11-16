$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime]
$null = [Windows.Devices.Enumeration.DeviceInformationCollection,Windows.Devices.Enumeration,ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.BluetoothDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.BluetoothLEDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
$classic = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelectorFromPairingState($true)
$ble = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($true)
$properties = [string[]]@('System.Devices.Aep.IsConnected','System.Devices.Aep.IsPaired',
    'System.Devices.Aep.DeviceAddress','System.Devices.BatteryLife','System.Devices.Aep.Manufacturer',
    'System.Devices.Aep.ModelName','System.Devices.Aep.ProtocolId')
$operation = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync(
    "($classic) OR ($ble)", $properties, [Windows.Devices.Enumeration.DeviceInformationKind]::AssociationEndpoint)
$task = $asTask.MakeGenericMethod([Windows.Devices.Enumeration.DeviceInformationCollection]).Invoke($null, @($operation))
if (-not $task.Wait(7000)) { $operation.Cancel(); throw 'Bluetooth enumeration timed out' }
$rows = @()
foreach ($device in $task.Result) {
    $props = @{}
    foreach ($entry in $device.Properties) { $props[$entry.Key] = $entry.Value }
    $rows += [ordered]@{
        id = $device.Id
        name = $device.Name
        address = $props['System.Devices.Aep.DeviceAddress']
        paired = $device.Pairing.IsPaired
        connected = $props['System.Devices.Aep.IsConnected']
        battery = $props['System.Devices.BatteryLife']
        manufacturer = $props['System.Devices.Aep.Manufacturer']
        model = $props['System.Devices.Aep.ModelName']
        protocol = $props['System.Devices.Aep.ProtocolId']
    }
}
ConvertTo-Json -InputObject $rows -Depth 4 -Compress
