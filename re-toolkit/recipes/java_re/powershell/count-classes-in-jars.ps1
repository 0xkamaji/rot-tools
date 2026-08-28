<#
Counts the number of .class files in each JAR file in the current directory
and sorts the results from highest to lowest class count.
#>

Get-ChildItem *.jar | ForEach-Object { [PSCustomObject]@{Jar=$_.Name; Classes=(jar tf $_.FullName | Select-String '\.class$').Count} } | Sort-Object Classes -Descending | Format-Table -AutoSize
