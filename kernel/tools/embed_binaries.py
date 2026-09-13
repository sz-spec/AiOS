"""Generate the kernel's user-program table, publishing the result atomically."""
import argparse
import os
import re
import tempfile
from pathlib import Path


def generate(output: Path, bin_dir: Path, programs: list[str]) -> None:
    identifiers = []
    for program in programs:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", program) or program in {".", ".."}:
            raise ValueError(f"Invalid program name: {program}")
        identifier = program.replace(".", "_").replace("-", "_")
        if identifier in identifiers:
            raise ValueError(f"Duplicate C identifier: {identifier}")
        identifiers.append(identifier)
        if not (bin_dir / program).is_file():
            raise FileNotFoundError(bin_dir / program)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=output.parent,
                                         prefix=".embed-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write("/* Auto-generated embedded binaries */\n")
            stream.write("#include <stdint.h>\n#include <stddef.h>\n\n")
            for program, identifier in zip(programs, identifiers):
                stream.write(f"/* Embedded: {program} */\n")
                stream.write(f"const uint8_t _embedded_{identifier}[] = {{\n")
                size = 0
                with (bin_dir / program).open("rb") as binary:
                    for chunk in iter(lambda: binary.read(12), b""):
                        stream.write("  " + ", ".join(f"0x{b:02x}" for b in chunk) + ",\n")
                        size += len(chunk)
                stream.write(f"}};\nconst size_t _embedded_{identifier}_size = {size};\n\n")
            stream.write("typedef struct { const char* name; const uint8_t* data; size_t size; } embedded_binary_t;\n")
            stream.write("const embedded_binary_t g_embedded_binaries[] = {\n")
            for program, identifier in zip(programs, identifiers):
                stream.write(f'    {{ "{program}", _embedded_{identifier}, sizeof(_embedded_{identifier}) }},\n')
            stream.write("    { (void*)0, (void*)0, 0 }\n};\n")
            stream.write("const size_t g_embedded_binaries_count = sizeof(g_embedded_binaries) / sizeof(g_embedded_binaries[0]) - 1;\n")
        temporary.chmod(0o644)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("programs", nargs="+")
    args = parser.parse_args()
    generate(args.output, args.bin_dir, args.programs)
