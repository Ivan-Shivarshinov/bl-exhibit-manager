param([Parameter(Mandatory=$true)][string]$Fixture, [Parameter(Mandatory=$true)][string]$Destination)
# Native Word document round-trip. This does not simulate or validate Office.js.
$ErrorActionPreference = 'Stop'
$sample = Get-Content -LiteralPath $Fixture -Raw -Encoding UTF8 | ConvertFrom-Json
$outputPath = [IO.Path]::GetFullPath($Destination)
$wordApp = New-Object -ComObject Word.Application
$wordDocument = $null
try {
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3
    $wordDocument = $wordApp.Documents.Add()
    $wordDocument.Content.Text = "Fictional Word integration test. All materials are synthetic."
    $anchor = $wordDocument.Range(10,10)
    $footnote = $wordDocument.Footnotes.Add($anchor, [Type]::Missing, ($sample.text + ', Article 421.'))
    $range = $footnote.Range.Duplicate
    $range.End = $range.Start + $sample.text.Length
    $link = $wordDocument.Hyperlinks.Add($range, $sample.url)
    $link.Range.Font.Bold = -1
    $link.Range.Font.Italic = 0
    $link.Range.Font.Underline = 0
    $link.Range.Font.Color = 0
    $wordDocument.SaveAs2($outputPath,16)
    $wordDocument.Close(0)
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordDocument)
    $wordDocument = $wordApp.Documents.Open($outputPath, $false, $true, $false)
    $actual = $wordDocument.Footnotes.Item(1).Range.Hyperlinks.Item(1)
    if ($actual.Address -ne $sample.url) { throw 'Stable citation address changed on reopen.' }
    [pscustomobject]@{WordVersion=$wordApp.Version;WordBuild=$wordApp.Build;Footnotes=$wordDocument.Footnotes.Count;Links=$wordDocument.Footnotes.Item(1).Range.Hyperlinks.Count;Text=$actual.Range.Text;Reopened=$true} | ConvertTo-Json
} finally {
    if ($wordDocument) { $wordDocument.Close(0); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordDocument) }
    $wordApp.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp)
}
