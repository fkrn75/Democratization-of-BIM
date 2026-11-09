# SPDX-License-Identifier: MPL-2.0
"""results/windows_clean_venv.json, linux_debian.json, linux_ubuntu.json 를 비교해 results/s10_summary.json 을 만든다."""
from __future__ import annotations

import json
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "results"


def load(n):
    return json.loads((R / n).read_text(encoding="utf-8"))


def main() -> None:
    win = load("windows_clean_venv.json")
    envs = {"windows": win, "debian": load("linux_debian.json")["suite"], "ubuntu": load("linux_ubuntu.json")["suite"]}
    base = win["ra01"]["json"]
    out: dict = {"environments": {}, "all_equal": {}}
    keys = {"semantic_hashes(state,derived,ifc)": lambda s: s["ra01"]["json"]["hashes"],
            "ifc_data_sha256(정규화)": lambda s: s["ra01"]["json"]["ifc_file"]["data_sha256"],
            "ifc_product_globalid_sha256": lambda s: s["ra01"]["json"]["ifc_file"]["globalid_sha256"],
            "mesh_sha256(s0_RA-01)": lambda s: next(c for c in s["render"] if c["label"] == "s0_RA-01")["info_first"]["mesh_sha256"],
            "pixel_sha256(s0_RA-01)": lambda s: next(c for c in s["render"] if c["label"] == "s0_RA-01")["info_first"]["pixel_sha256"],
            "png_sha256(s0_RA-01)": lambda s: next(c for c in s["render"] if c["label"] == "s0_RA-01")["info_first"]["png_sha256"],
            "id_buffer_sha256(s0_RA-01)": lambda s: next(c for c in s["render"] if c["label"] == "s0_RA-01")["info_first"]["id_buffer_sha256"],
            "pixel_sha256(core_ra01)": lambda s: next(c for c in s["render"] if c["label"] == "core_ra01")["info_first"]["pixel_sha256"],
            "pixel_sha256(foreign_BA)": lambda s: next(c for c in s["render"] if c["label"] == "foreign_Building-Architecture")["info_first"]["pixel_sha256"],
            "synthetic_cube_pixel_sha256": lambda s: s["render_synthetic_cube_pixel_sha256"]}
    for k, fn in keys.items():
        vals = {n: fn(s) for n, s in envs.items()}
        out["all_equal"][k] = len({json.dumps(v, sort_keys=True) for v in vals.values()}) == 1
    for n, s in envs.items():
        r = s["ra01"]
        out["environments"][n] = {
            "platform": s["platform"], "ra01_exit": r["exit"], "ra01_stdout_single_json": r["stdout_is_single_json"],
            "ra01_ok": r["json"]["ok"], "ra01_wall_seconds_total": r["json"].get("wall_seconds"),
            "child_call_seconds": {c["argv"][0] + ":" + (c["argv"][1] if len(c["argv"]) > 1 else ""): c["seconds"] for c in r["json"]["child_calls"]},
            "render_seconds_median": {c["label"]: c["seconds_total"]["median"] for c in s["render"]},
            "render_seconds_max": {c["label"]: c["seconds_total"]["max"] for c in s["render"]},
            "render_deterministic_repeat": {c["label"]: c["deterministic_repeat"] for c in s["render"]},
            "render_threads4_same_pixels": {c["label"]: c["threads4_same_pixels"] for c in s["render"]},
            "gui_distributions_found": s["gui_scan"]["gui_distributions_found"],
            "display_env": s["gui_scan"]["display_env"], "tkinter_importable": s["gui_scan"]["tkinter_importable"],
            "ldd_gui_libs_flagged": s["gui_scan"].get("ldd_gui_libs_flagged"), "ldd_not_found": s["gui_scan"].get("ldd_not_found"),
            "loaded_gui_libs_after_render": s["gui_scan"].get("loaded_gui_libs_after_render"),
        }
    out["linux_env_probe"] = {"debian": load("linux_debian.json")["env_probe"], "ubuntu": load("linux_ubuntu.json")["env_probe"]}
    out["ifc_raw_bytes"] = {n: s["ra01"]["json"]["ifc_file"]["bytes"] for n, s in envs.items()}
    (R / "s10_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    print(json.dumps(out["all_equal"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
