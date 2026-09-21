# Export the corpus PDFs with Microsoft Word.
#
# The PDF documents in data/manifest.json are made from an intermediate .docx (written by
# scripts/build_corpus_docx.py --pdf-intermediates <dir>) and exported here with Word, so
# they are genuine Word PDFs. Requires Microsoft Word on Windows.
#
# Usage:
#   python scripts\build_corpus_docx.py --pdf-intermediates C:\temp\corpus-docx
#   powershell -File scripts\export_pdfs_with_word.ps1 -IntermediateDir C:\temp\corpus-docx
#
# Word automation can hang on a hidden dialog, so run this with a timeout and make sure no
# stray WINWORD process is left behind afterwards.

param(
    [Parameter(Mandatory = $true)][string]$IntermediateDir,
    [string]$DataDir
)

# $PSScriptRoot is empty inside a param() default in Windows PowerShell 5.1, so set it here.
if (-not $DataDir) { $DataDir = Join-Path $PSScriptRoot "..\data" }

$manifest = Get-Content (Join-Path $DataDir "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$wdExportFormatPDF = 17
$word = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    foreach ($entry in ($manifest.documents | Where-Object { $_.format -eq "pdf" })) {
        $name = [IO.Path]::GetFileNameWithoutExtension($entry.path)
        $inputPath = [IO.Path]::GetFullPath((Join-Path $IntermediateDir "$name.docx"))
        $outputPath = [IO.Path]::GetFullPath((Join-Path (Join-Path $DataDir "synthetic") $entry.path))
        $document = $word.Documents.Open($inputPath, $false, $true)   # no conversion prompt, read-only
        "pages in $name : " + $document.ComputeStatistics(2)
        $document.ExportAsFixedFormat($outputPath, $wdExportFormatPDF)
        $document.Close([ref]0)
        "exported $($entry.path)"
    }
}
catch {
    "FAILED: " + $_.Exception.Message
    exit 1
}
finally {
    if ($word) {
        try { $word.Quit([ref]0) } catch { "Word did not quit cleanly: " + $_.Exception.Message }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
}
