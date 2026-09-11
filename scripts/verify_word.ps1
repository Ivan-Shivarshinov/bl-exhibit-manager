param([string]$Document = '', [switch]$FollowFirstLink)
$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Document) { $Document = Join-Path $projectDir 'output\relocated\Submission\Main document.docx' }
$wordDocPath = (Resolve-Path -LiteralPath $Document).Path
$qaDir = Join-Path $projectDir 'tmp\word-qa'
New-Item -ItemType Directory -Force -Path $qaDir | Out-Null
$wordApp = New-Object -ComObject Word.Application
$wordDocument = $null
try {
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3
    $wordDocument = $wordApp.Documents.Open($wordDocPath, $false, $true, $false)
    $wordDocument.Repaginate()
    $linkResults = @()
    foreach ($footnote in $wordDocument.Footnotes) {
        foreach ($hyperlink in $footnote.Range.Hyperlinks) {
            $address = [string]$hyperlink.Address
            $resolvedLink = [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $wordDocPath) ([System.Uri]::UnescapeDataString($address))))
            $linkResults += [pscustomobject]@{Address=$address;Exists=(Test-Path -LiteralPath $resolvedLink);Relative=(-not [System.IO.Path]::IsPathRooted($address))}
        }
    }
    # QA-only rendering. The application itself does not convert the main DOCX to PDF.
    $wordDocument.ExportAsFixedFormat((Join-Path $qaDir 'word-render.pdf'), 17)
    $followed = $false
    if ($FollowFirstLink) {
        $wordDocument.Footnotes.Item(1).Range.Hyperlinks.Item(1).Follow()
        $followed = $true
    }
    [pscustomobject]@{WordVersion=$wordApp.Version;WordBuild=$wordApp.Build;Pages=$wordDocument.ComputeStatistics(2);Footnotes=$wordDocument.Footnotes.Count;Links=$linkResults;FollowInvokedSuccessfully=$followed;Document=$wordDocPath} | ConvertTo-Json -Depth 4
} finally {
    if ($wordDocument) { $wordDocument.Close(0); [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordDocument) }
    $wordApp.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp)
}
