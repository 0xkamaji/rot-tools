<#
Searches all JAR files in the current directory
for a specified Java class.
#>

$Class = Read-Host "Class to find (e.g. com/vendor/client/Start.class)"
Get-ChildItem *.jar | ForEach-Object { if (jar tf $_.FullName | Select-String -SimpleMatch $Class) { $_.Name } }
