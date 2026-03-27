#!/usr/bin/env python3
"""Generate Verilog modules from hierarchy/cell pin data.

Supported input formats:
1) Hierarchy-centric JSON:
{
  "hierarchies": [
    {
      "name": "top",
      "pins": [{"name": "clk", "direction": "input", "width": 1}],
      "cells": [
        {"name": "u1", "type": "and2", "pins": {"A": "a", "B": "b", "Y": "n1"}}
      ]
    }
  ]
}

2) Flat JSON/CSV records (one row per cell-pin connection):
[
  {"hierarchy":"top","instance":"u1","cell_type":"and2","pin":"A","net":"a"},
  {"hierarchy":"top","instance":"u1","cell_type":"and2","pin":"B","net":"b"},
  {"hierarchy":"top","instance":"u1","cell_type":"and2","pin":"Y","net":"n1"}
]

For flat records, module ports are optional and can be marked with:
  {"hierarchy":"top","module_pin":true,"pin":"clk","direction":"input","width":1}
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
SIMPLE_NET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass
class Port:
    name: str
    direction: str = "input"
    width: Any = 1


@dataclass
class Instance:
    name: str
    cell_type: str
    pins: "OrderedDict[str, str]" = field(default_factory=OrderedDict)


@dataclass
class Hierarchy:
    name: str
    ports: List[Port] = field(default_factory=list)
    instances: List[Instance] = field(default_factory=list)


def pick(row: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def sanitize_identifier(name: str, fallback: str = "unnamed") -> str:
    if not name:
        return fallback
    clean = re.sub(r"[^A-Za-z0-9_$]", "_", str(name))
    if re.match(r"^[0-9]", clean):
        clean = "_" + clean
    return clean or fallback


def verilog_net_expr(expr: str) -> str:
    """Return connection expression; escape invalid bare identifiers."""
    raw = str(expr).strip()
    if not raw:
        return "/* UNCONNECTED */"

    # Keep expressions/constants/slices as-is when they are not bare identifiers.
    if SIMPLE_NET_RE.match(raw):
        return raw
    if re.search(r"[\s\[\]\(\)\{\}'\":\+\-\*/&|~^?.,]", raw):
        return raw

    # Escape odd tokens like a/b or name.with.dot
    return f"\\{raw} "


def direction_token(direction: str) -> str:
    mapping = {
        "input": "input",
        "in": "input",
        "output": "output",
        "out": "output",
        "inout": "inout",
    }
    token = mapping.get(str(direction).strip().lower())
    if token is None:
        raise ValueError(f"Unsupported direction '{direction}'. Use input/output/inout.")
    return token


def width_range(width: Any) -> str:
    if width is None:
        return ""
    if isinstance(width, bool):
        return ""
    if isinstance(width, int):
        return f" [{width - 1}:0]" if width > 1 else ""

    text = str(width).strip()
    if not text or text == "1":
        return ""
    if text.startswith("[") and text.endswith("]"):
        return f" {text}"
    if ":" in text:
        return f" [{text}]"
    if text.isdigit():
        n = int(text)
        return f" [{n - 1}:0]" if n > 1 else ""
    return f" [{text}]"


def parse_hierarchy_json(data: Dict[str, Any]) -> List[Hierarchy]:
    raw_hiers = data.get("hierarchies")
    if not isinstance(raw_hiers, list):
        raise ValueError("Hierarchy JSON must contain key 'hierarchies' as a list.")

    result: List[Hierarchy] = []
    for item in raw_hiers:
        name = pick(item, ["name", "hierarchy", "module"])
        if not name:
            raise ValueError(f"Hierarchy item missing name: {item}")
        hierarchy = Hierarchy(name=str(name))

        raw_ports = pick(item, ["pins", "ports"], default=[])
        if raw_ports:
            for p in raw_ports:
                pname = pick(p, ["name", "pin", "port"])
                if not pname:
                    raise ValueError(f"Port missing name in hierarchy '{name}': {p}")
                direction = pick(p, ["direction", "dir"], default="input")
                width = pick(p, ["width", "bus", "range"], default=1)
                hierarchy.ports.append(Port(str(pname), str(direction), width))

        raw_cells = pick(item, ["cells", "instances"], default=[])
        for c in raw_cells:
            iname = pick(c, ["name", "instance", "inst"])
            ctype = pick(c, ["type", "cell", "cell_type", "ref"])
            if not iname or not ctype:
                raise ValueError(f"Cell in hierarchy '{name}' needs instance and cell type: {c}")
            inst = Instance(name=str(iname), cell_type=str(ctype))

            raw_pins = pick(c, ["pins", "connections"], default={})
            if isinstance(raw_pins, dict):
                for pin_name, net_name in raw_pins.items():
                    inst.pins[str(pin_name)] = str(net_name)
            elif isinstance(raw_pins, list):
                for row in raw_pins:
                    pin_name = pick(row, ["pin", "name"])
                    net_name = pick(row, ["net", "signal", "wire"])
                    if not pin_name:
                        raise ValueError(f"Cell pin entry missing pin name: {row}")
                    inst.pins[str(pin_name)] = "" if net_name is None else str(net_name)
            else:
                raise ValueError(f"Unsupported pins type in cell '{iname}' of '{name}'.")

            hierarchy.instances.append(inst)

        result.append(hierarchy)
    return result


def parse_hierarchy_map_json(data: Dict[str, Any]) -> List[Hierarchy]:
    """Parse top-level mapping: {hier_path: {type:..., pins:[...]}}."""
    result: List[Hierarchy] = []
    for hname, item in data.items():
        if not isinstance(item, dict):
            continue

        # Skip known wrapper keys of other supported formats.
        if hname in ("hierarchies", "records"):
            continue

        ctype = pick(item, ["type", "cell", "cell_type", "ref"])
        raw_ports = pick(item, ["pins", "ports"], default=[])
        if not ctype or not isinstance(raw_ports, list):
            continue

        hierarchy = Hierarchy(name=str(hname))
        inst = Instance(name="u_cell", cell_type=str(ctype))

        for p in raw_ports:
            if not isinstance(p, dict):
                raise ValueError(f"Pin entry must be an object in hierarchy '{hname}': {p}")
            pname = pick(p, ["name", "pin", "port"])
            if not pname:
                raise ValueError(f"Port missing name in hierarchy '{hname}': {p}")

            direction = pick(p, ["direction", "dir"], default="input")
            width = pick(p, ["width", "bus", "range"], default=1)
            hierarchy.ports.append(Port(str(pname), str(direction), width))

            # If net is omitted, connect pin to the same-name module port.
            net_name = pick(p, ["net", "signal", "wire"], default=str(pname))
            inst.pins[str(pname)] = str(net_name)

        hierarchy.instances.append(inst)
        result.append(hierarchy)

    return result


def parse_flat_records(records: List[Dict[str, Any]]) -> List[Hierarchy]:
    hier_map: "OrderedDict[str, Hierarchy]" = OrderedDict()
    instance_map: Dict[Tuple[str, str], Instance] = {}
    seen_ports: Dict[Tuple[str, str], bool] = {}

    for row in records:
        hname = pick(row, ["hierarchy", "hier", "module", "module_name"])
        if not hname:
            raise ValueError(f"Flat record missing hierarchy/module field: {row}")
        hname = str(hname)
        hierarchy = hier_map.setdefault(hname, Hierarchy(name=hname))

        raw_module_pin = pick(
            row, ["module_pin", "is_module_pin", "top_port", "is_port"], default=False
        )
        if isinstance(raw_module_pin, str):
            is_module_pin = raw_module_pin.strip().lower() in ("1", "true", "yes", "y")
        else:
            is_module_pin = bool(raw_module_pin)

        if is_module_pin:
            # In some flat exports, module port names may appear in "net"/"signal" columns.
            pname = pick(row, ["pin", "port", "name", "net", "signal", "wire"])
            if not pname:
                raise ValueError(f"Module pin record missing pin name: {row}")
            pname = str(pname)
            key = (hname, pname)
            if key not in seen_ports:
                direction = pick(row, ["direction", "dir"], default="input")
                width = pick(row, ["width", "bus", "range"], default=1)
                hierarchy.ports.append(Port(name=pname, direction=str(direction), width=width))
                seen_ports[key] = True
            continue

        iname = pick(row, ["instance", "inst", "inst_name", "cell_inst"])
        ctype = pick(row, ["cell_type", "cell", "type", "ref", "lib_cell"])
        pin = pick(row, ["pin", "pin_name", "port"])
        net = pick(row, ["net", "signal", "wire", "connection"], default="")
        if not iname or not ctype or not pin:
            raise ValueError(
                "Cell connection row must include hierarchy, instance, cell_type, pin, net. "
                f"Bad row: {row}"
            )

        ikey = (hname, str(iname))
        if ikey not in instance_map:
            inst = Instance(name=str(iname), cell_type=str(ctype))
            hierarchy.instances.append(inst)
            instance_map[ikey] = inst

        instance_map[ikey].pins[str(pin)] = "" if net is None else str(net)

    return list(hier_map.values())


def load_input(path: Path) -> Tuple[List[Hierarchy], str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        return parse_flat_records(rows), "flat(csv)"

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and isinstance(data.get("hierarchies"), list):
        return parse_hierarchy_json(data), "hierarchy(json)"

    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return parse_flat_records(data["records"]), "flat(json.records)"

    if isinstance(data, dict):
        parsed = parse_hierarchy_map_json(data)
        if parsed:
            return parsed, "hierarchy_map(json.dict)"

    if isinstance(data, list):
        return parse_flat_records(data), "flat(json.list)"

    raise ValueError(
        "Unsupported input JSON. Use either {'hierarchies': [...]}, {'records': [...]}, "
        "a hierarchy-map dict, or a list."
    )


def render_module(hierarchy: Hierarchy) -> str:
    module_name = sanitize_identifier(hierarchy.name, fallback="module_unnamed")

    port_names = [sanitize_identifier(p.name) for p in hierarchy.ports]
    if port_names:
        lines = [f"module {module_name} (", "    " + ",\n    ".join(port_names), ");", ""]
    else:
        lines = [f"module {module_name} ();", ""]

    for p in hierarchy.ports:
        p_name = sanitize_identifier(p.name)
        p_dir = direction_token(p.direction)
        p_w = width_range(p.width)
        lines.append(f"  {p_dir}{p_w} {p_name};")

    port_set = set(port_names)
    internal_nets = set()
    for inst in hierarchy.instances:
        for net in inst.pins.values():
            net_text = str(net).strip()
            if SIMPLE_NET_RE.match(net_text) and net_text not in port_set:
                internal_nets.add(net_text)

    if hierarchy.ports and internal_nets:
        lines.append("")
    if internal_nets:
        for n in sorted(internal_nets):
            lines.append(f"  wire {n};")

    if hierarchy.instances:
        lines.append("")
    for idx, inst in enumerate(hierarchy.instances):
        ctype = sanitize_identifier(inst.cell_type, fallback="cell_unnamed")
        iname = sanitize_identifier(inst.name, fallback=f"u{idx}")
        lines.append(f"  {ctype} {iname} (")
        pin_items = list(inst.pins.items())
        if not pin_items:
            lines.append("  );")
            continue

        for p_idx, (pin_name, net_name) in enumerate(pin_items):
            comma = "," if p_idx < len(pin_items) - 1 else ""
            pin_id = sanitize_identifier(pin_name, fallback=f"PIN{p_idx}")
            net_expr = verilog_net_expr(net_name)
            lines.append(f"    .{pin_id}({net_expr}){comma}")
        lines.append("  );")
        lines.append("")

    if lines[-1] == "":
        lines.pop()
    lines.append("endmodule")
    lines.append("")
    return "\n".join(lines)


def write_modules(hierarchies: List[Hierarchy], output_dir: Path) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping: Dict[str, str] = {}
    for h in hierarchies:
        mod_name = sanitize_identifier(h.name, fallback="module_unnamed")
        out_file = output_dir / f"{mod_name}.v"
        out_file.write_text(render_module(h), encoding="utf-8")
        mapping[h.name] = str(out_file)
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate one Verilog module file per hierarchy."
    )
    parser.add_argument("--input", required=True, help="Input JSON/CSV data file.")
    parser.add_argument(
        "--output-dir",
        default="generated_verilog",
        help="Directory to store generated .v files (default: generated_verilog).",
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="Optional JSON file path to write hierarchy-to-file mapping.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    hierarchies, detected = load_input(input_path)
    if not hierarchies:
        raise ValueError("No hierarchy data found in input.")

    mapping = write_modules(hierarchies, output_dir)
    print(
        f"Generated {len(mapping)} module files from {input_path} "
        f"(detected format: {detected})"
    )
    for hname, fpath in mapping.items():
        print(f"  - {hname} -> {fpath}")

    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"input": str(input_path), "modules": mapping}, indent=2),
            encoding="utf-8",
        )
        print(f"Manifest written: {manifest_path}")


if __name__ == "__main__":
    main()
