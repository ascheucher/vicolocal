"""Recover the Vicohome app's protobuf schemas from the jadx-decompiled sources.

Every full-runtime protobuf class (GeneratedMessageV3) embeds its original .proto as a
serialized FileDescriptorProto, passed to `FileDescriptor.internalBuildGeneratedFileFrom(
new String[]{...})` as Java string literals (one char per byte). The obfuscator renames
methods but can't touch these, so they give exact message/field/enum names and numbers.

    python proto/extract_protos.py [jadx_sources_dir]

Writes proto/<name>.proto (readable) and proto/vicohome.desc (a FileDescriptorSet the
client can load at runtime with descriptor_pool / message_factory).
"""
import re
import sys
from pathlib import Path

from google.protobuf import descriptor_pb2 as d

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "apk/jadx/sources"
START = "internalBuildGeneratedFileFrom(new String[]{"

TYPES = {1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
         7: "fixed32", 8: "bool", 9: "string", 10: "group", 12: "bytes", 13: "uint32",
         15: "sfixed32", 16: "sfixed64", 17: "sint32", 18: "sint64"}
SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", '"': '"', "'": "'", "\\": "\\"}


def java_literals(text: str, i: int) -> str:
    """Concatenate the Java string literals starting at text[i] up to the closing '}'."""
    out = []
    while True:
        while text[i] in " \t\r\n,+":
            i += 1
        if text[i] == "}":
            return "".join(out)
        if text[i] != '"':
            raise ValueError(f"unexpected {text[i]!r} in descriptor array")
        i += 1
        while text[i] != '"':
            c = text[i]
            if c != "\\":
                out.append(c); i += 1; continue
            e = text[i + 1]
            if e == "u":
                j = i + 1
                while text[j] == "u":
                    j += 1
                out.append(chr(int(text[j:j + 4], 16))); i = j + 4
            elif e in SIMPLE_ESC:
                out.append(SIMPLE_ESC[e]); i += 2
            elif e.isdigit():  # octal \0 .. \377
                m = re.match(r"[0-7]{1,3}", text[i + 1:])
                out.append(chr(int(m.group(), 8))); i += 1 + len(m.group())
            else:
                raise ValueError(f"unknown escape \\{e}")
        i += 1


def find_descriptors(root: Path) -> list[d.FileDescriptorProto]:
    found = {}
    for path in root.rglob("*.java"):
        text = path.read_text(encoding="utf-8", errors="replace")
        pos = text.find(START)
        if pos < 0:
            continue
        raw = java_literals(text, pos + len(START)).encode("latin-1")
        fd = d.FileDescriptorProto.FromString(raw)
        found[fd.name] = fd
    return [found[k] for k in sorted(found)]


# ---- .proto text emitter ----
def short(type_name: str, package: str) -> str:
    name = type_name.lstrip(".")
    return name[len(package) + 1:] if package and name.startswith(package + ".") else name


def emit_enum(e: d.EnumDescriptorProto, ind: str) -> list[str]:
    lines = [f"{ind}enum {e.name} {{"]
    lines += [f"{ind}  {v.name} = {v.number};" for v in e.value]
    return lines + [f"{ind}}}"]


def field_type(f: d.FieldDescriptorProto, maps: dict, package: str) -> str:
    if f.type_name in maps:
        k, v = maps[f.type_name]
        return f"map<{field_type(k, maps, package)}, {field_type(v, maps, package)}>"
    if f.type in (11, 14):
        return short(f.type_name, package)
    return TYPES[f.type]


def emit_field(f, maps, package, proto3, in_oneof):
    t = field_type(f, maps, package)
    label = ""
    if not t.startswith("map<") and not in_oneof:
        if f.label == f.LABEL_REPEATED:
            label = "repeated "
        elif f.label == f.LABEL_REQUIRED:
            label = "required "
        elif not proto3 or f.proto3_optional:
            label = "optional "
    return f"{label}{t} {f.name} = {f.number};"


def emit_message(m: d.DescriptorProto, ind: str, prefix: str, package: str, proto3: bool) -> list[str]:
    full = f"{prefix}.{m.name}"
    maps = {}
    for n in m.nested_type:
        if n.options.map_entry:
            maps[f"{full}.{n.name}"] = (n.field[0], n.field[1])
    lines = [f"{ind}message {m.name} {{"]
    for e in m.enum_type:
        lines += emit_enum(e, ind + "  ")
    for n in m.nested_type:
        if not n.options.map_entry:
            lines += emit_message(n, ind + "  ", full, package, proto3)
    emitted_oneofs = set()
    for f in m.field:
        oi = f.oneof_index if f.HasField("oneof_index") and not f.proto3_optional else None
        if oi is None:
            lines.append(f"{ind}  {emit_field(f, maps, package, proto3, False)}")
        elif oi not in emitted_oneofs:
            emitted_oneofs.add(oi)
            lines.append(f"{ind}  oneof {m.oneof_decl[oi].name} {{")
            for g in m.field:
                if g.HasField("oneof_index") and g.oneof_index == oi and not g.proto3_optional:
                    lines.append(f"{ind}    {emit_field(g, maps, package, proto3, True)}")
            lines.append(f"{ind}  }}")
    return lines + [f"{ind}}}"]


def emit_file(fd: d.FileDescriptorProto) -> str:
    proto3 = fd.syntax == "proto3"
    lines = [f'syntax = "{fd.syntax or "proto2"}";', ""]
    if fd.package:
        lines += [f"package {fd.package};", ""]
    lines += [f'import "{dep}";' for dep in fd.dependency]
    if fd.options.java_package:
        lines.append(f'option java_package = "{fd.options.java_package}";')
    lines.append("")
    prefix = f".{fd.package}" if fd.package else ""
    for e in fd.enum_type:
        lines += emit_enum(e, "") + [""]
    for m in fd.message_type:
        lines += emit_message(m, "", prefix, fd.package, proto3) + [""]
    for s in fd.service:
        lines.append(f"service {s.name} {{")
        for r in s.method:
            lines.append(f"  rpc {r.name}({short(r.input_type, fd.package)}) returns ({short(r.output_type, fd.package)});")
        lines += ["}", ""]
    return "\n".join(lines)


def main():
    fds = find_descriptors(SRC)
    out = HERE
    for fd in fds:
        if not fd.name.startswith("google/protobuf/"):  # well-known types; kept in the .desc only
            (out / Path(fd.name).name).write_text(emit_file(fd))
    fset = d.FileDescriptorSet(file=fds)
    (out / "vicohome.desc").write_bytes(fset.SerializeToString())
    for fd in fds:
        print(f"{fd.name:<40} {fd.package:<20} msgs={len(fd.message_type):<3} enums={len(fd.enum_type)}")
    print(f"\n{len(fds)} files -> {out}/*.proto + vicohome.desc")


if __name__ == "__main__":
    main()
