# Export Ghidra-defined strings and functions through an interactive report.
# Mirrors re-toolkit/recipes/binary-ninja/export_triage.py.
# Run after Ghidra auto-analysis completes.
#@author 0xkamaji
#@category Loadbot
#@menupath Tools.Loadbot.Export Triage Report

from java.awt import Toolkit
from java.awt.datatransfer import StringSelection
from java.util import ArrayList

from ghidra.program.util import DefinedStringIterator
from ghidra.util.exception import CancelledException


DIALOG_TITLE = "Loadbot Ghidra Recipe"


def java_choices(items):
    """Return a java.util.List for Ghidra's askChoice API."""
    choices = ArrayList()
    for item in items:
        choices.add(item)
    return choices


def format_address(address):
    """Format a Ghidra address like the Binary Ninja recipe."""
    return "0x{:x}".format(int(address.getUnsignedOffset()))


def collect_strings(program):
    """Return Ghidra's defined strings, ordered by address."""
    strings = []
    iterator = DefinedStringIterator.forProgram(program)

    while iterator.hasNext():
        data = iterator.next()
        value = data.getValue()
        if value is None:
            continue
        strings.append((data.getAddress(), str(value)))

    strings.sort(key=lambda item: int(item[0].getUnsignedOffset()))
    lines = ["=== STRINGS ==="]
    lines.extend(
        "{}  {}".format(format_address(address), value)
        for address, value in strings
    )
    return "\n".join(lines), len(strings)


def collect_internal_functions(program):
    """Return non-external functions Ghidra identified inside the binary."""
    functions = []
    iterator = program.getFunctionManager().getFunctions(True)

    while iterator.hasNext():
        functions.append(iterator.next())

    lines = ["=== INTERNAL FUNCTIONS ==="]
    lines.extend(
        "{}  {}".format(
            format_address(function.getEntryPoint()),
            function.getName(),
        )
        for function in functions
    )
    return "\n".join(lines), len(functions)


def collect_imported_functions(program):
    """Return functions Ghidra identified in the external namespace."""
    imports = []
    iterator = program.getFunctionManager().getExternalFunctions()

    while iterator.hasNext():
        function = iterator.next()
        imports.append(str(function.getName(True)))

    imports.sort(key=lambda name: name.lower())
    lines = ["=== IMPORTED FUNCTIONS ==="]
    lines.extend(imports)
    return "\n".join(lines), len(imports)


def combine_sections(*sections):
    """Join report sections with one blank line between them."""
    return "\n\n".join(section for section in sections if section)


def copy_to_clipboard(text):
    """Copy report text to the system clipboard."""
    selection = StringSelection(text)
    Toolkit.getDefaultToolkit().getSystemClipboard().setContents(selection, None)


def save_to_file(text):
    """Ask for a destination and save the report as UTF-8 text."""
    output_file = askFile("Save triage report", "Save")
    if output_file is None:
        println("Save cancelled.")
        return False

    output_path = str(output_file.getAbsolutePath())
    if not output_path.lower().endswith(".txt"):
        output_path += ".txt"

    with open(output_path, "w", encoding="utf-8") as destination:
        destination.write(text)

    println("Triage report saved to: {}".format(output_path))
    return True


def choose_report(program):
    """Ask which data to collect and return the formatted report."""
    choices = java_choices([
        "Strings",
        "Internal functions",
        "Imported functions",
        "All functions",
        "Full report",
    ])

    choice = askChoice(
        DIALOG_TITLE,
        "What data should be collected?",
        choices,
        choices.get(0),
    )

    if choice == "Strings":
        strings, count = collect_strings(program)
        return strings, "{} strings".format(count)

    if choice == "Internal functions":
        internal, count = collect_internal_functions(program)
        return internal, "{} internal functions".format(count)

    if choice == "Imported functions":
        imported, count = collect_imported_functions(program)
        return imported, "{} imported functions".format(count)

    internal, internal_count = collect_internal_functions(program)
    imported, imported_count = collect_imported_functions(program)
    functions = combine_sections(internal, imported)
    function_summary = (
        "{} internal functions and {} imported functions"
        .format(internal_count, imported_count)
    )

    if choice == "All functions":
        return functions, function_summary

    strings, string_count = collect_strings(program)
    report = combine_sections(functions, strings)
    summary = "{}, and {} strings".format(function_summary, string_count)
    return report, summary


def deliver_report(output, summary):
    """Ask how the report should be delivered and perform the selection."""
    choices = java_choices([
        "Print to console",
        "Copy to clipboard",
        "Save to file",
        "Print and copy",
        "Print, copy, and save",
    ])

    choice = askChoice(
        DIALOG_TITLE,
        "How should the results be delivered?",
        choices,
        choices.get(0),
    )

    should_print = choice in (
        "Print to console",
        "Print and copy",
        "Print, copy, and save",
    )
    should_copy = choice in (
        "Copy to clipboard",
        "Print and copy",
        "Print, copy, and save",
    )
    should_save = choice in (
        "Save to file",
        "Print, copy, and save",
    )

    if should_print:
        println(output)

    if should_copy:
        copy_to_clipboard(output)
        println("Copied {} to clipboard.".format(summary))

    if should_save:
        save_to_file(output)


def run(program):
    """Run the interactive triage exporter for the active Ghidra program."""
    output, summary = choose_report(program)
    deliver_report(output, summary)


if currentProgram is None:
    printerr("No program is open. Open a program and run the script again.")
else:
    try:
        run(currentProgram)
    except CancelledException:
        println("Export cancelled.")
    except Exception as error:
        printerr("Triage export failed: {}".format(error))
        raise
