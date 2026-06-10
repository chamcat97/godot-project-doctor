extends SceneTree

## Headless runner for the parity harness.
##
## Copied into <fixture>/addons/run_addon_scan.gd next to a copy of the
## godot_project_doctor addon, then executed with:
##   godot --headless --path <fixture> --script res://addons/run_addon_scan.gd
## Prints one machine-readable line ("PARITY_JSON:[...]") consumed by
## tests/parity/compare.py. Living under addons/ keeps it out of the scan.

func _initialize() -> void:
	var ScannerS = preload("res://addons/godot_project_doctor/scanner.gd")
	var ChecksS = preload("res://addons/godot_project_doctor/checks.gd")
	var index = ScannerS.new().scan()
	var issues = ChecksS.new().run_all(index)
	var out: Array = []
	for it in issues:
		out.append({"severity": it["severity"], "code": it["code"], "file": it["file"]})
	print("PARITY_JSON:" + JSON.stringify(out))
	quit()
