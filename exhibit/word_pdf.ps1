param([string]$InputDocument, [string]$OutputPdf, [string]$PidFile)
$ErrorActionPreference = 'Stop'
$application = $null
$document = $null
$owned = $false
$previousProcesses = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$savedOptions = $null
try {
    $application = New-Object -ComObject Word.Application
    $createdProcesses = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object { $_.Id -notin $previousProcesses })
    if ($createdProcesses.Count -ne 1) { throw 'Could not identify an isolated Word conversion process.' }
    $owned = $true
    [System.IO.File]::WriteAllText($PidFile, [string]$createdProcesses[0].Id)
    $application.Visible = $false
    $application.DisplayAlerts = 0
    $application.AutomationSecurity = 3
    $savedOptions = @($application.Options.UpdateLinksAtOpen, $application.Options.UpdateFieldsAtPrint, $application.Options.UpdateLinksAtPrint)
    $application.Options.UpdateLinksAtOpen = $false
    $application.Options.UpdateFieldsAtPrint = $false
    $application.Options.UpdateLinksAtPrint = $false
    $document = $application.Documents.Open($InputDocument, $false, $true, $false)
    $document.Repaginate()
    $document.ExportAsFixedFormat($OutputPdf, 17, $false)
} finally {
    if ($document) { $document.Close(0); [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
    if ($application) {
        if ($savedOptions) {
            $application.Options.UpdateLinksAtOpen = $savedOptions[0]
            $application.Options.UpdateFieldsAtPrint = $savedOptions[1]
            $application.Options.UpdateLinksAtPrint = $savedOptions[2]
        }
        if ($owned) { $application.Quit() }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($application)
    }
}
