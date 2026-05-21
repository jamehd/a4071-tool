<#
.SYNOPSIS
    Upload a new A4071-Tool release to the backend.

.EXAMPLE
    .\release.ps1 -Base http://a4071-tool.j4m.dev:4071 `
        -User admin -Pass secret `
        -Version 0.2.0 -Notes "- Sửa lỗi" `
        -File dist\A4071-Tool.exe
#>
param(
    [Parameter(Mandatory)][string]$Base,
    [Parameter(Mandatory)][string]$User,
    [Parameter(Mandatory)][string]$Pass,
    [Parameter(Mandatory)][string]$Version,
    [string]$Notes = "",
    [Parameter(Mandatory)][string]$File
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $File)) {
    throw "File not found: $File"
}
$fileItem = Get-Item -LiteralPath $File
Write-Host "Uploading $($fileItem.FullName) ($([math]::Round($fileItem.Length / 1MB, 1)) MB) as v$Version"

$loginBody = @{ username = $User; password = $Pass } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -Uri "$Base/api/admin/login" `
    -Method Post -ContentType "application/json" -Body $loginBody
$token = $login.token
if (-not $token) { throw "Login failed: no token in response" }

$form = @{
    version = $Version
    notes   = $Notes
    file    = $fileItem
}
$resp = Invoke-RestMethod -Uri "$Base/api/admin/release" -Method Post `
    -Headers @{ Authorization = "Bearer $token" } -Form $form

Write-Host "Released v$($resp.version)  sha256=$($resp.sha256)  size=$($resp.size) bytes"
